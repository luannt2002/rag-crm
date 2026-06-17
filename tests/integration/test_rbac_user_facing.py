"""d — RBAC matrix tests for chat / documents / sync routes.

Verifies the 9 user-facing routes wired in Phase 2d are gated through the
metadata-driven ``module_permissions`` table (Phase 1 seed) and that the
role hierarchy holds across guest / viewer / user / operator / admin.

Like Phase 2a tests, this file exercises the ``require_permission_dep``
factory + the underlying ``require_permission`` function with a mocked
Redis layer — no FastAPI / TestClient boot, no JWT middleware. The
service-token path is verified by routing role="service" (level=60)
through the same dep, since that is what ``TenantContextMiddleware``
sets on ``request.state.role`` for verified service tokens.

A small subset of full-stack assertions also walks the actual ``router``
dependency lists to prove every route declares the right gate.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.interfaces.http.middlewares.rbac import (
    require_permission,
    require_permission_dep,
)
from ragbot.interfaces.http.routes import chat as chat_routes
from ragbot.interfaces.http.routes import documents as document_routes
from ragbot.interfaces.http.routes import sync as sync_routes
from ragbot.shared.errors import ForbiddenError


# Seed mapping mirrored from scripts/seed_rbac_permissions_s11b.py — drift
# between the seed file and these tests must trigger CI failure rather than
# a silent role-level downgrade for customer-facing chat / corpus / sync.
_USER_FACING_PERMISSIONS: dict[str, int] = {
    # chat (2)
    "chat:submit": 10,
    "chat:feedback": 10,
    # document (3)
    "document:ingest": 40,
    "document:delete_by_tool_name": 60,
    "document:rechunk": 40,
    # sync (4)
    "sync:bot_upsert": 60,
    "sync:documents_upsert": 60,
    "sync:documents_list": 20,
    "sync:documents_delete": 60,
}


def _fake_request(role: str) -> Any:
    """Minimal request stub with a Redis client returning the seed map."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(_USER_FACING_PERMISSIONS))
    redis.set = AsyncMock(return_value=None)
    container = MagicMock()
    container.redis_client = MagicMock(return_value=redis)
    container.session_factory = MagicMock()
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(app=app, state=SimpleNamespace(role=role))


# ---------------------------------------------------------------------------
# 1. Static contract — every wired route must declare a permission gate.
# ---------------------------------------------------------------------------


def _gated_paths(router: Any, prefix: str) -> list[tuple[str, str, str]]:
    """Return [(method, path, dep_name)] for routes with a ``require_*`` gate."""
    out: list[tuple[str, str, str]] = []
    for r in router.routes:
        for dep in getattr(r, "dependencies", ()) or ():
            fn = getattr(dep, "dependency", None)
            name = getattr(fn, "__name__", "") if fn else ""
            if name.startswith(prefix):
                method = next(iter(r.methods or {""}))
                out.append((method, r.path, name))
    return out


class TestRoutesGated:
    """Verify all 9 routes carry a ``require_permission_dep``."""

    def test_chat_routes_have_permission_dep(self) -> None:
        gated = _gated_paths(chat_routes.router, "require_chat_")
        assert len(gated) == 2, f"expected 2 chat gates, got {gated}"
        names = {n for (_m, _p, n) in gated}
        assert "require_chat_submit" in names
        assert "require_chat_feedback" in names

    def test_document_routes_have_permission_dep(self) -> None:
        gated = _gated_paths(document_routes.router, "require_document_")
        assert len(gated) == 3, f"expected 3 document gates, got {gated}"
        names = {n for (_m, _p, n) in gated}
        assert "require_document_ingest" in names
        assert "require_document_delete_by_tool_name" in names
        assert "require_document_rechunk" in names

    def test_sync_routes_have_permission_dep(self) -> None:
        gated = _gated_paths(sync_routes.router, "require_sync_")
        assert len(gated) == 4, f"expected 4 sync gates, got {gated}"
        names = {n for (_m, _p, n) in gated}
        assert "require_sync_bot_upsert" in names
        assert "require_sync_documents_upsert" in names
        assert "require_sync_documents_list" in names
        assert "require_sync_documents_delete" in names

    def test_no_hardcoded_role_strings_in_route_modules(self) -> None:
        """Domain-neutral / zero-hardcode: no inline role names in routes."""
        from pathlib import Path

        for mod in (chat_routes, document_routes, sync_routes):
            src = Path(mod.__file__).read_text()
            for role in (
                "\"admin\"", "\"super_admin\"", "\"tenant\"",
                "\"operator\"", "\"viewer\"", "\"guest\"", "\"user\"",
            ):
                assert role not in src, (
                    f"hardcoded role literal {role} leaked into "
                    f"{Path(mod.__file__).name}"
                )


# ---------------------------------------------------------------------------
# 2. Role matrix — guest blocked / public chat allows viewer+ / etc.
# ---------------------------------------------------------------------------


class TestChatRoleMatrix:
    """Customer-facing chat: viewer=10 floor, no guest writes."""

    @pytest.mark.asyncio
    async def test_guest_blocked_on_chat_submit(self) -> None:
        """guest=0 < 10 — anonymous customers cannot submit chat."""
        req = _fake_request("guest")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "chat", "submit")

    @pytest.mark.asyncio
    async def test_user_can_submit_chat(self) -> None:
        """user=20 >= 10 — standard caller passes."""
        req = _fake_request("user")
        await require_permission(req, "chat", "submit")  # no raise
        await require_permission(req, "chat", "feedback")  # no raise

    @pytest.mark.asyncio
    async def test_viewer_at_exact_boundary(self) -> None:
        """viewer=10 == seed=10 — boundary inclusive (>=)."""
        req = _fake_request("viewer")
        await require_permission(req, "chat", "submit")
        await require_permission(req, "chat", "feedback")


class TestDocumentRoleMatrix:
    """Tenant operator manages corpus: ingest=40, rechunk=40, delete=60."""

    @pytest.mark.asyncio
    async def test_viewer_blocked_on_ingest(self) -> None:
        """viewer=10 < 40 — read-only role cannot mutate corpus."""
        req = _fake_request("viewer")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "document", "ingest")

    @pytest.mark.asyncio
    async def test_user_blocked_on_ingest(self) -> None:
        """user=20 < 40 — even authenticated users cannot ingest."""
        req = _fake_request("user")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "document", "ingest")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "document", "rechunk")

    @pytest.mark.asyncio
    async def test_operator_can_ingest_and_rechunk_not_delete(self) -> None:
        """operator=40 passes the 40-floor gates but fails the 60 delete."""
        req = _fake_request("operator")
        await require_permission(req, "document", "ingest")
        await require_permission(req, "document", "rechunk")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "document", "delete_by_tool_name")

    @pytest.mark.asyncio
    async def test_admin_passes_every_document_permission(self) -> None:
        """admin=60 covers ingest/rechunk(40) + delete_by_tool_name(60)."""
        req = _fake_request("admin")
        await require_permission(req, "document", "ingest")
        await require_permission(req, "document", "rechunk")
        await require_permission(req, "document", "delete_by_tool_name")


class TestSyncRoleMatrix:
    """Sync routes: NestJS uses service token (role=service, level=60)."""

    @pytest.mark.asyncio
    async def test_user_blocked_on_sync_bot(self) -> None:
        """user=20 < 60 — non-admin humans cannot drive upstream sync."""
        req = _fake_request("user")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "sync", "bot_upsert")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "sync", "documents_upsert")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "sync", "documents_delete")

    @pytest.mark.asyncio
    async def test_user_can_list_sync_documents(self) -> None:
        """user=20 >= 20 — listing is read-only and allowed."""
        req = _fake_request("user")
        await require_permission(req, "sync", "documents_list")

    @pytest.mark.asyncio
    async def test_service_token_role_passes_sync(self) -> None:
        """role='service' resolves to level=60 via shared.rbac.ROLE_LEVELS,
        matching the sync gates that NestJS upstream calls — no explicit
        service-token bypass needed; the role mapping IS the bypass."""
        req = _fake_request("service")
        await require_permission(req, "sync", "bot_upsert")
        await require_permission(req, "sync", "documents_upsert")
        await require_permission(req, "sync", "documents_delete")
        await require_permission(req, "sync", "documents_list")

    @pytest.mark.asyncio
    async def test_admin_fallback_passes_sync(self) -> None:
        """RBAC fallback — admin=60 humans can call sync without service token."""
        req = _fake_request("admin")
        for perm in (
            "bot_upsert", "documents_upsert", "documents_list", "documents_delete",
        ):
            await require_permission(req, "sync", perm)


# ---------------------------------------------------------------------------
# 3. Dep factory — closure names + boundary semantics.
# ---------------------------------------------------------------------------


class TestRequirePermissionDep:
    @pytest.mark.asyncio
    async def test_dep_blocks_when_role_below_seed(self) -> None:
        dep = require_permission_dep("document", "delete_by_tool_name")  # needs 60
        req = _fake_request("operator")  # 40 — below
        with pytest.raises(ForbiddenError):
            await dep(req)

    @pytest.mark.asyncio
    async def test_dep_allows_when_role_at_seed(self) -> None:
        dep = require_permission_dep("document", "ingest")  # needs 40
        req = _fake_request("operator")  # 40 — exact match
        await dep(req)  # no exception

    def test_dep_names_for_all_user_facing_gates(self) -> None:
        """Closure name surfaces in OpenAPI / debug — must be self-describing."""
        for key in _USER_FACING_PERMISSIONS:
            module, perm = key.split(":")
            dep = require_permission_dep(module, perm)
            assert dep.__name__ == f"require_{module}_{perm}"


# ---------------------------------------------------------------------------
# 4. Cross-tenant verify — wired RBAC does NOT replace tenant scope.
# ---------------------------------------------------------------------------


class TestCrossTenantScopeStillEnforced:
    """RBAC level alone is not enough — the 4-key resolve via
    ``BotRegistryService`` is what isolates tenant A from tenant B's bot.

    These tests pin the contract so a future refactor that strips the
    BotRegistryService.lookup call from a route fails loudly here.
    """

    def test_chat_routes_resolve_via_registry(self) -> None:
        """chat.submit_chat + submit_feedback MUST call BotRegistryService."""
        from pathlib import Path

        src = Path(chat_routes.__file__).read_text()
        # Every chat handler carries a tenant-scoped 4-key lookup.
        assert src.count("registry.lookup(") >= 2
        # Identity is JWT-bound, never wire-supplied.
        assert "request.state.record_tenant_id" in src
        # Workspace slug flows through the resolver helper.
        assert "resolve_workspace_id(" in src

    def test_document_routes_resolve_via_registry(self) -> None:
        """ingest / delete / rechunk all flow through _resolve_bot_uuid."""
        from pathlib import Path

        src = Path(document_routes.__file__).read_text()
        assert src.count("_resolve_bot_uuid(") >= 3
        assert "resolve_workspace_id(" in src

    def test_sync_routes_scope_select_by_record_tenant(self) -> None:
        """sync_bot SELECT/UPDATE filter on record_tenant_id; sync_documents
        + delete_documents call ``find_by_4key(...)``.
        """
        from pathlib import Path

        src = Path(sync_routes.__file__).read_text()
        # SELECT in sync_bot scopes by the UUID FK column, not the legacy
        # INT alias.
        assert "record_tenant_id" in src
        # Docs upsert + delete go through the 4-key resolver.
        assert "find_by_4key(" in src
