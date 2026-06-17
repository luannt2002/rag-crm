"""Integration tests for Stream J Phase 4 — admin key management endpoints.

Covers:
  POST   /ai/providers/{provider_id}/keys         — add_key happy/error paths
  GET    /ai/providers/{provider_id}/keys         — list_keys masked
  POST   /ai/providers/{provider_id}/keys/{id}/verify — verify_key

All tests exercise handler logic against Request doubles with mocked
AIConfigService; no real DB or network required.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.ai_config_service import ProviderNotFoundError
from ragbot.interfaces.http.routes import admin_ai
from ragbot.interfaces.http.schemas.admin_ai_schemas import AddKeyRequest
from ragbot.shared.errors import ForbiddenError, KeyNotFoundError, KeyVerifyError

_PROVIDER_ID = uuid4()
_KEY_ID = uuid4()
_TENANT_ID = uuid4()

_KEY_ROW = {
    "id": str(_KEY_ID),
    "fingerprint": "sk-l...iih9C",
    "status": "active",
    "is_default": True,
    "last_health_check_at": None,
    "last_health_status": None,
    "last_used_at": None,
    "created_at": "2026-05-06T00:00:00+00:00",
}


def _build_request(
    *,
    role: str,
    svc: Any,
    record_tenant_id: UUID = _TENANT_ID,
    user_id: str = "ops@example.com",
    trace_id: str = "trace-test",
) -> SimpleNamespace:
    """Return a minimal Request-like object wired to a mock AIConfigService."""
    container = MagicMock()
    container.ai_config_service = MagicMock(return_value=svc)
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            record_tenant_id=record_tenant_id,
            user_id=user_id,
            trace_id=trace_id,
        ),
    )


# ---------------------------------------------------------------------------
# POST /ai/providers/{provider_id}/keys
# ---------------------------------------------------------------------------


class TestAdminAddKey:
    @pytest.mark.asyncio
    async def test_add_key_201_returns_data(self) -> None:
        """Happy path: service returns data → endpoint returns {"ok": True, "data": ...}."""
        svc = MagicMock()
        svc.add_key = AsyncMock(return_value=_KEY_ROW)
        req = _build_request(role="super_admin", svc=svc)

        body = AddKeyRequest(plain_key="sk-live-test", set_as_default=True, verify_first=False)  # type: ignore[arg-type]
        result = await admin_ai.admin_add_key(_PROVIDER_ID, body, req)

        assert result["ok"] is True
        assert result["data"] == _KEY_ROW
        svc.add_key.assert_awaited_once_with(
            provider_id=_PROVIDER_ID,
            plain_key="sk-live-test",
            set_as_default=True,
            verify_first=False,
            record_tenant_id=_TENANT_ID,
            actor_user_id="ops@example.com",
            trace_id="trace-test",
        )

    @pytest.mark.asyncio
    async def test_add_key_400_when_verify_fails(self) -> None:
        """KeyVerifyError from service → HTTP 400 with message prefix."""
        from fastapi import HTTPException

        svc = MagicMock()
        svc.add_key = AsyncMock(side_effect=KeyVerifyError("429 quota exceeded"))
        req = _build_request(role="super_admin", svc=svc)

        body = AddKeyRequest(plain_key="sk-bad-key", set_as_default=False, verify_first=True)  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_info:
            await admin_ai.admin_add_key(_PROVIDER_ID, body, req)

        assert exc_info.value.status_code == 400
        assert "key verify failed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_add_key_404_provider_not_found(self) -> None:
        """ProviderNotFoundError → HTTP 404."""
        from fastapi import HTTPException

        svc = MagicMock()
        svc.add_key = AsyncMock(side_effect=ProviderNotFoundError("no such provider"))
        req = _build_request(role="super_admin", svc=svc)

        body = AddKeyRequest(plain_key="sk-any", set_as_default=False, verify_first=False)  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_info:
            await admin_ai.admin_add_key(_PROVIDER_ID, body, req)

        assert exc_info.value.status_code == 404
        assert "provider not found" in exc_info.value.detail


# ---------------------------------------------------------------------------
# GET /ai/providers/{provider_id}/keys
# ---------------------------------------------------------------------------


class TestAdminListKeys:
    @pytest.mark.asyncio
    async def test_list_keys_200_returns_masked(self) -> None:
        """Happy path: list_keys returns rows without exposing plain_key."""
        svc = MagicMock()
        rows = [_KEY_ROW, {**_KEY_ROW, "id": str(uuid4()), "status": "rotated_out"}]
        svc.list_keys = AsyncMock(return_value=rows)
        req = _build_request(role="admin", svc=svc)

        result = await admin_ai.admin_list_keys(_PROVIDER_ID, req)

        assert result["ok"] is True
        assert len(result["data"]) == 2
        # plain_key must never appear; only fingerprint in rows
        for row in result["data"]:
            assert "plain_key" not in row
            assert "fingerprint" in row
        svc.list_keys.assert_awaited_once_with(provider_id=_PROVIDER_ID)

    @pytest.mark.asyncio
    async def test_list_keys_404_provider_not_found(self) -> None:
        """ProviderNotFoundError → HTTP 404."""
        from fastapi import HTTPException

        svc = MagicMock()
        svc.list_keys = AsyncMock(side_effect=ProviderNotFoundError("gone"))
        req = _build_request(role="admin", svc=svc)

        with pytest.raises(HTTPException) as exc_info:
            await admin_ai.admin_list_keys(_PROVIDER_ID, req)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /ai/providers/{provider_id}/keys/{key_id}/verify
# ---------------------------------------------------------------------------


class TestAdminVerifyKey:
    @pytest.mark.asyncio
    async def test_verify_key_200_updates_health(self) -> None:
        """Happy path: verify_key returns updated health result."""
        health = {"status": "ok", "last_health_status": "ok", "latency_ms": 210}
        svc = MagicMock()
        svc.verify_key = AsyncMock(return_value=health)
        req = _build_request(role="super_admin", svc=svc)

        result = await admin_ai.admin_verify_key(_PROVIDER_ID, _KEY_ID, req)

        assert result["ok"] is True
        assert result["data"]["status"] == "ok"
        svc.verify_key.assert_awaited_once_with(
            provider_id=_PROVIDER_ID,
            key_id=_KEY_ID,
            record_tenant_id=_TENANT_ID,
            actor_user_id="ops@example.com",
            trace_id="trace-test",
        )

    @pytest.mark.asyncio
    async def test_verify_key_404_provider_not_found(self) -> None:
        """ProviderNotFoundError → HTTP 404."""
        from fastapi import HTTPException

        svc = MagicMock()
        svc.verify_key = AsyncMock(side_effect=ProviderNotFoundError("no provider"))
        req = _build_request(role="super_admin", svc=svc)

        with pytest.raises(HTTPException) as exc_info:
            await admin_ai.admin_verify_key(_PROVIDER_ID, _KEY_ID, req)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_verify_key_404_key_not_found(self) -> None:
        """KeyNotFoundError → HTTP 404."""
        from fastapi import HTTPException

        svc = MagicMock()
        svc.verify_key = AsyncMock(side_effect=KeyNotFoundError("no key"))
        req = _build_request(role="super_admin", svc=svc)

        with pytest.raises(HTTPException) as exc_info:
            await admin_ai.admin_verify_key(_PROVIDER_ID, _KEY_ID, req)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# RBAC — missing permission raises ForbiddenError at the dep layer
# ---------------------------------------------------------------------------


class TestAdminKeysRbac:
    """Verify the permission gates are correctly declared on each endpoint."""

    def _deps_for_route(self, path: str, method: str) -> list[str]:
        """Return dep closure names for a given route."""
        for r in admin_ai.router.routes:
            if r.path == path and method in (r.methods or set()):
                return [
                    getattr(dep.dependency, "__name__", "")
                    for dep in (getattr(r, "dependencies", None) or [])
                ]
        return []

    def test_add_key_requires_provider_add_key_permission(self) -> None:
        deps = self._deps_for_route("/ai/providers/{provider_id}/keys", "POST")
        assert any("provider_add_key" in d for d in deps), (
            f"POST /keys must require provider_add_key, got deps: {deps}"
        )

    def test_list_keys_requires_provider_read_permission(self) -> None:
        deps = self._deps_for_route("/ai/providers/{provider_id}/keys", "GET")
        assert any("provider_read" in d for d in deps), (
            f"GET /keys must require provider_read, got deps: {deps}"
        )

    def test_verify_key_requires_provider_add_key_permission(self) -> None:
        deps = self._deps_for_route(
            "/ai/providers/{provider_id}/keys/{key_id}/verify", "POST"
        )
        assert any("provider_add_key" in d for d in deps), (
            f"POST /verify must require provider_add_key, got deps: {deps}"
        )

    @pytest.mark.asyncio
    async def test_add_key_403_without_permission(self) -> None:
        """admin role (level=60) cannot pass provider_add_key (level=80)."""
        import json

        from ragbot.interfaces.http.middlewares.rbac import require_permission_dep

        _PERMS = {"ai:provider_add_key": 80}
        redis = MagicMock()
        redis.get = AsyncMock(return_value=json.dumps(_PERMS))
        redis.set = AsyncMock(return_value=None)
        container = MagicMock()
        container.redis_client = MagicMock(return_value=redis)
        container.session_factory = MagicMock()
        app = MagicMock()
        app.state = SimpleNamespace(container=container)
        req = SimpleNamespace(app=app, state=SimpleNamespace(role="admin"))

        dep = require_permission_dep("ai", "provider_add_key")
        with pytest.raises(ForbiddenError):
            await dep(req)
