"""P0 cross-tenant impersonation guard — route-level enforcement.

CLAUDE.md identity rule v3 line 252 mandates that the JWT/header
``tenant_id`` MUST equal the request body ``tenant_id``. The
``TenantContextMiddleware`` already does this cross-check inside the
service-JWT block, but it skips when:

* The body is not parseable as JSON / not present in time.
* The body shape doesn't put ``tenant_id`` at the top level.
* The user-JWT path is taken (the mismatch branch only runs in the
  service-JWT block).

These tests exercise ``enforce_tenant_match`` and the four routes that
resolve a bot via ``req.tenant_id``:

* ``POST /chat`` (chat.submit_chat)
* ``POST /chat/stream`` (chat_stream.chat_stream)
* ``POST /sync/bot``, ``POST /sync/documents``, ``DELETE /sync/documents``
* ``POST /documents/create`` (and delete / rechunk via ``_resolve_bot_uuid``)

Each test wires a stub auth middleware that sets ``request.state.role``
+ ``tenant_id_int`` from custom test headers, then drives a real route
function with a ``TestClient``. We use mocks for the container so we
don't need a real Postgres/Redis.

A cross-tenant attempt (JWT tenant=A, body tenant=B, role != super_admin)
MUST get HTTP 403 ``tenant_id mismatch``. Super-admin (level 100) MUST
pass. JWT == body MUST pass.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from ragbot.interfaces.http.middlewares.tenant_context import (
    enforce_tenant_match,
)


# ---------------------------------------------------------------------------
# Helpers — unit-level fakes
# ---------------------------------------------------------------------------


def _make_request(role: str, jwt_tenant_int: int | None) -> Any:
    """Lightweight request stub for ``enforce_tenant_match`` unit tests."""
    state = SimpleNamespace(role=role)
    if jwt_tenant_int is not None:
        state.tenant_id_int = jwt_tenant_int
    url = SimpleNamespace(path="/test")
    return SimpleNamespace(state=state, url=url)


# ---------------------------------------------------------------------------
# Unit tests — enforce_tenant_match itself
# ---------------------------------------------------------------------------


class TestEnforceTenantMatchUnit:
    def test_match_passes(self) -> None:
        req = _make_request("service", 32)
        # Should NOT raise.
        enforce_tenant_match(req, 32)

    def test_mismatch_raises_403(self) -> None:
        req = _make_request("service", 32)
        with pytest.raises(HTTPException) as exc_info:
            enforce_tenant_match(req, 999)
        assert exc_info.value.status_code == 403
        assert "tenant_id mismatch" in str(exc_info.value.detail)

    def test_super_admin_bypass_mismatch(self) -> None:
        req = _make_request("super_admin", 32)
        # super_admin (level 100) bypasses — even cross-tenant body OK.
        enforce_tenant_match(req, 999)

    def test_system_role_bypass(self) -> None:
        # ``system`` role is also level 100 in ROLE_LEVELS.
        req = _make_request("system", 1)
        enforce_tenant_match(req, 4242)

    def test_admin_level_60_does_not_bypass(self) -> None:
        # admin = 60 < 100 → must still match body.
        req = _make_request("admin", 32)
        with pytest.raises(HTTPException) as exc_info:
            enforce_tenant_match(req, 999)
        assert exc_info.value.status_code == 403

    def test_missing_jwt_tenant_id_int_raises_403(self) -> None:
        req = _make_request("service", None)
        with pytest.raises(HTTPException) as exc_info:
            enforce_tenant_match(req, 32)
        assert exc_info.value.status_code == 403
        assert "missing tenant context" in str(exc_info.value.detail)

    def test_user_role_with_match_passes(self) -> None:
        req = _make_request("user", 32)
        enforce_tenant_match(req, 32)

    def test_user_role_with_mismatch_blocked(self) -> None:
        req = _make_request("user", 32)
        with pytest.raises(HTTPException) as exc_info:
            enforce_tenant_match(req, 99)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Route-level integration — stub auth middleware + minimal app
# ---------------------------------------------------------------------------


class _StubAuthMiddleware(BaseHTTPMiddleware):
    """Set request.state.{role, tenant_id_int, tenant_id, user_id, trace_id}
    from ``X-Test-Role`` and ``X-Test-Tenant-Int`` headers. Replaces the
    real ``TenantContextMiddleware`` so we don't need JWTs to drive the
    route-level guard tests.
    """

    async def dispatch(self, request, call_next):
        request.state.role = request.headers.get("X-Test-Role", "service")
        tid_h = request.headers.get("X-Test-Tenant-Int")
        if tid_h is not None:
            request.state.tenant_id_int = int(tid_h)
        # Defaults the routes read for command building.
        request.state.tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        request.state.user_id = "test-user"
        request.state.trace_id = "trace-test"
        return await call_next(request)


def _build_test_app(routers: list[Any], container: MagicMock) -> FastAPI:
    """Build a stripped-down FastAPI app with only what the cross-tenant
    guard tests need: stub auth middleware + the routers under test +
    request.app.state.container double.
    """
    app = FastAPI()
    app.add_middleware(_StubAuthMiddleware)
    app.state.container = container
    app.state.settings = SimpleNamespace(app=SimpleNamespace())
    for r in routers:
        app.include_router(r)
    return app


def _bypass_rbac_dep(app: FastAPI) -> None:
    """Override the rbac permission dependency factory so RBAC isn't
    what blocks the request — the cross-tenant guard is what we're
    testing. We replace each ``require_<module>_<perm>`` dep with a
    no-op.
    """
    # FastAPI dependency_overrides operates on the *callable* used in
    # ``Depends(...)``. ``require_permission_dep`` returns a fresh
    # closure per call site, so we walk the routes and override each.
    for route in app.routes:
        deps = getattr(route, "dependant", None)
        if deps is None:
            continue
        for sub in getattr(deps, "dependencies", []):
            fn = sub.call
            if fn is None:
                continue
            name = getattr(fn, "__name__", "")
            if name.startswith("require_"):
                async def _noop() -> None:  # noqa: D401
                    return None
                app.dependency_overrides[fn] = _noop


def _container_with_resolve_passthrough(found_bot_id: UUID | None = None) -> MagicMock:
    """Build a container double that resolves ``BotRegistryService.lookup``
    + ``bot_repo.find_by_4key`` to a fake BotConfig — so if the
    cross-tenant guard fails to fire, the route would otherwise return
    200/202. Tests assert 403 to prove the guard short-circuits BEFORE
    any DB / use-case work.
    """
    container = MagicMock()

    fake_bot = SimpleNamespace(
        id=found_bot_id or uuid4(),
        tenant_id=32,
        bot_id="test",
        channel_type="web",
        system_prompt="",
    )

    registry = MagicMock()
    registry.lookup = AsyncMock(return_value=fake_bot)
    container.bot_registry_service = MagicMock(return_value=registry)

    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=fake_bot)
    container.bot_repo = MagicMock(return_value=repo)

    # Use-cases — return MagicMock results so the route can finish if it
    # ever gets past the guard (super-admin bypass tests).
    uc_result = SimpleNamespace(
        job_id=uuid4(), trace_id="trace-test", tool_name="x",
        status_url="/job/x",
    )
    uc = MagicMock()
    uc.execute = AsyncMock(return_value=uc_result)
    container.answer_question_uc = MagicMock(return_value=uc)
    container.give_feedback_uc = MagicMock(return_value=uc)
    container.ingest_document_uc = MagicMock(return_value=uc)
    container.delete_document_uc = MagicMock(
        return_value=MagicMock(execute=AsyncMock(
            return_value=SimpleNamespace(deleted_chunks=0, corpus_version=1),
        )),
    )
    container.rechunk_document_uc = MagicMock(return_value=uc)

    # session_factory + redis_client (sync.py / chat_stream.py reach for
    # these; we only need 403 to fire BEFORE they're hit).
    sf = MagicMock(return_value=_session_ctx())
    container.session_factory = MagicMock(return_value=sf)
    container.redis_client = MagicMock(return_value=MagicMock())
    return container


def _session_ctx() -> Any:
    @asynccontextmanager
    async def _ctx() -> Any:
        yield MagicMock()
    return _ctx


# ---------------------------------------------------------------------------
# /chat — mismatch / match / super-admin
# ---------------------------------------------------------------------------


def _chat_payload(tenant_id: int) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "bot_id": "test",
        "channel_type": "web",
        "user_id": "u1",
        "content": "hello",
        "mode": "async",
    }


class TestChatRouteCrossTenantGuard:
    def _app(self) -> FastAPI:
        from ragbot.interfaces.http.routes.chat import router as chat_router
        container = _container_with_resolve_passthrough()
        app = _build_test_app([chat_router], container)
        _bypass_rbac_dep(app)
        return app

    def test_chat_jwt_tenant_a_body_tenant_b_returns_403(self) -> None:
        app = self._app()
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/chat",
                headers={"X-Test-Role": "service", "X-Test-Tenant-Int": "32"},
                json=_chat_payload(tenant_id=999),
            )
        assert r.status_code == 403, r.text
        assert "tenant_id mismatch" in r.text

    def test_jwt_match_body_passes(self) -> None:
        app = self._app()
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/chat",
                headers={"X-Test-Role": "service", "X-Test-Tenant-Int": "32"},
                json=_chat_payload(tenant_id=32),
            )
        # Guard passes — route gets to the use-case (202 accepted).
        assert r.status_code == 202, r.text

    def test_super_admin_can_cross_tenant(self) -> None:
        app = self._app()
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/chat",
                headers={
                    "X-Test-Role": "super_admin",
                    "X-Test-Tenant-Int": "1",
                },
                json=_chat_payload(tenant_id=999),
            )
        assert r.status_code == 202, r.text


# ---------------------------------------------------------------------------
# /chat/stream — mismatch must 403 BEFORE pipeline build
# ---------------------------------------------------------------------------


class TestChatStreamRouteCrossTenantGuard:
    def _app(self) -> FastAPI:
        from ragbot.interfaces.http.routes.chat_stream import (
            router as stream_router,
        )
        container = _container_with_resolve_passthrough()
        app = _build_test_app([stream_router], container)
        _bypass_rbac_dep(app)
        return app

    def test_chat_stream_jwt_tenant_a_body_tenant_b_returns_403(self) -> None:
        app = self._app()
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/chat/stream",
                headers={"X-Test-Role": "service", "X-Test-Tenant-Int": "32"},
                json=_chat_payload(tenant_id=999),
            )
        assert r.status_code == 403, r.text
        assert "tenant_id mismatch" in r.text


# ---------------------------------------------------------------------------
# /sync/* — mismatch must 403
# ---------------------------------------------------------------------------


def _sync_documents_payload(tenant_id: int) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "bot_id": "test",
        "channel_type": "web",
        "documents": [
            {"title": "t", "content": "c"},
        ],
    }


class TestSyncRouteCrossTenantGuard:
    def _app(self) -> FastAPI:
        from ragbot.interfaces.http.routes.sync import router as sync_router
        container = _container_with_resolve_passthrough()
        app = _build_test_app([sync_router], container)
        _bypass_rbac_dep(app)
        return app

    def test_sync_jwt_tenant_a_body_tenant_b_returns_403(self) -> None:
        app = self._app()
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/sync/documents",
                headers={"X-Test-Role": "service", "X-Test-Tenant-Int": "32"},
                json=_sync_documents_payload(tenant_id=999),
            )
        assert r.status_code == 403, r.text
        assert "tenant_id mismatch" in r.text


# ---------------------------------------------------------------------------
# /documents/create — mismatch must 403
# ---------------------------------------------------------------------------


def _ingest_doc_payload(tenant_id: int) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "bot_id": "test",
        "channel_type": "web",
        "source_url": "https://example.com/doc",
        "document_name": "t",
        "mime_type": "text/plain",
    }


class TestDocumentsRouteCrossTenantGuard:
    def _app(self) -> FastAPI:
        from ragbot.interfaces.http.routes.documents import (
            router as doc_router,
        )
        container = _container_with_resolve_passthrough()
        app = _build_test_app([doc_router], container)
        _bypass_rbac_dep(app)
        return app

    def test_documents_jwt_tenant_a_body_tenant_b_returns_403(self) -> None:
        app = self._app()
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/documents/create",
                headers={"X-Test-Role": "service", "X-Test-Tenant-Int": "32"},
                json=_ingest_doc_payload(tenant_id=999),
            )
        assert r.status_code == 403, r.text
        assert "tenant_id mismatch" in r.text
