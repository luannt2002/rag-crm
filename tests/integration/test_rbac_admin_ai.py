"""a — RBAC matrix tests for admin_ai routes.

Verifies that every admin_ai route is gated through the metadata-driven
``module_permissions`` table (Phase 1 seed) and that the role hierarchy
holds across guest / viewer / admin / super_admin.

The tests do NOT spin up the full FastAPI app — they exercise the
``require_permission_dep`` factory + the underlying ``require_permission``
function with mocked Redis + container, which is faster and avoids the
JWT middleware reading real settings.
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
from ragbot.interfaces.http.routes import admin_ai
from ragbot.shared.errors import ForbiddenError


# Seed mapping mirrored from scripts/seed_rbac_permissions_s11b.py — kept
# tight on purpose so a drift between seed + test triggers a CI failure
# rather than a silent role-level downgrade.
_AI_PERMISSIONS: dict[str, int] = {
    "ai:provider_read": 60,
    "ai:provider_create": 80,
    "ai:provider_update": 80,
    "ai:provider_delete": 80,
    "ai:provider_test": 60,
    "ai:provider_rotate_key": 80,
    "ai:provider_add_key": 80,  # Stream J Phase 4 — add/verify key endpoints
    "ai:model_read": 20,
    "ai:model_create": 80,
    "ai:model_update": 80,
    "ai:model_delete": 80,
    "ai:binding_read": 60,
    "ai:binding_create": 80,
    "ai:binding_update": 80,
    "ai:binding_delete": 80,
    "ai:audit_read": 60,
    "ai:cache_reload": 60,
    "ai:cache_status": 60,
    "ai:effective_config_read": 60,
}


def _fake_request(role: str) -> Any:
    """Minimal request stub with a Redis client returning the seed map."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(_AI_PERMISSIONS))
    redis.set = AsyncMock(return_value=None)
    container = MagicMock()
    container.redis_client = MagicMock(return_value=redis)
    container.session_factory = MagicMock()
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(app=app, state=SimpleNamespace(role=role))


# ---------------------------------------------------------------------------
# 1. Static contract — every admin_ai route must declare a permission gate.
# ---------------------------------------------------------------------------


class TestAdminAiRoutesGated:
    """Verify all 18 admin_ai routes carry a ``require_permission_dep``."""

    def _gated_paths(self) -> list[tuple[str, str, str]]:
        """Return [(method, path, dep_name)] for routes with a perm gate."""
        out: list[tuple[str, str, str]] = []
        for r in admin_ai.router.routes:
            for dep in getattr(r, "dependencies", ()) or ():
                # FastAPI Depends wraps the callable on .dependency
                fn = getattr(dep, "dependency", None)
                name = getattr(fn, "__name__", "") if fn else ""
                if name.startswith("require_ai_"):
                    method = next(iter(r.methods or {""}))
                    out.append((method, r.path, name))
        return out

    def test_all_routes_have_permission_dep(self) -> None:
        gated = self._gated_paths()
        # 18 original + 3 Stream J Phase 4 (add_key, list_keys, verify_key) = 21.
        assert len(gated) == 21, f"expected 21 gated routes, got {len(gated)}: {gated}"

    def test_no_hardcoded_role_checks_in_route_module(self) -> None:
        """Zero-hardcode: no role-comparison literals (``role == "admin"`` etc).

        We allow incidental string literals (e.g. an audit-log actor fallback)
        because they are not RBAC decisions; we only ban patterns that gate
        access on a hard-coded role name.
        """
        import re
        from pathlib import Path

        src = Path(admin_ai.__file__).read_text()
        # Patterns that would gate access on a literal role name.
        forbidden_patterns = [
            r"role\s*==\s*[\"']",
            r"role\s+in\s*\([\"']",
            r"require_min_level\(",          # legacy level gate — replaced by perm dep
            r"_require_admin\(",              # private helper from pre-Phase-2a code
        ]
        for pat in forbidden_patterns:
            assert re.search(pat, src) is None, (
                f"forbidden RBAC pattern {pat!r} still present in admin_ai.py"
            )


# ---------------------------------------------------------------------------
# 2. Role matrix — guest / viewer / admin / super_admin × ai permissions.
# ---------------------------------------------------------------------------


class TestRoleMatrix:
    """Drive ``require_permission`` directly to assert the level hierarchy."""

    @pytest.mark.asyncio
    async def test_guest_blocked_on_every_mutate(self) -> None:
        """guest=0 cannot pass any provider_*/model_*/binding_* gate."""
        req = _fake_request("guest")
        for key, level in _AI_PERMISSIONS.items():
            module, perm = key.split(":")
            if level <= 0:
                continue  # nothing requires <= guest
            with pytest.raises(ForbiddenError):
                await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_viewer_can_read_models_only(self) -> None:
        """viewer=10 < 20 (model_read) → still blocked; viewer < admin reads too."""
        req = _fake_request("viewer")
        # model_read is the lowest bar (level=20). viewer=10 must NOT pass.
        with pytest.raises(ForbiddenError):
            await require_permission(req, "ai", "model_read")
        # viewer obviously cannot read provider list (level=60).
        with pytest.raises(ForbiddenError):
            await require_permission(req, "ai", "provider_read")
        # And cannot mutate.
        with pytest.raises(ForbiddenError):
            await require_permission(req, "ai", "provider_create")

    @pytest.mark.asyncio
    async def test_user_can_read_models_but_not_admin_reads(self) -> None:
        """user=20 passes model_read=20 but fails provider_read=60."""
        req = _fake_request("user")
        await require_permission(req, "ai", "model_read")  # OK
        with pytest.raises(ForbiddenError):
            await require_permission(req, "ai", "provider_read")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "ai", "binding_create")

    @pytest.mark.asyncio
    async def test_admin_passes_all_reads_blocks_mutates(self) -> None:
        """admin=60 passes every read (60 floor) but is blocked on mutates (80)."""
        req = _fake_request("admin")
        for key, level in _AI_PERMISSIONS.items():
            module, perm = key.split(":")
            if level <= 60:
                await require_permission(req, module, perm)  # must pass
            else:
                with pytest.raises(ForbiddenError):
                    await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_tenant_passes_every_ai_permission(self) -> None:
        """tenant=80 owns the workspace — passes all ai.* gates (max=80)."""
        req = _fake_request("tenant")
        for key in _AI_PERMISSIONS:
            module, perm = key.split(":")
            await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_super_admin_passes_every_ai_permission(self) -> None:
        """super_admin=100 covers the whole matrix incl. cross-tenant."""
        req = _fake_request("super_admin")
        for key in _AI_PERMISSIONS:
            module, perm = key.split(":")
            await require_permission(req, module, perm)


# ---------------------------------------------------------------------------
# 3. Dep factory — Depends() wrapper enforces the same gate as the bare fn.
# ---------------------------------------------------------------------------


class TestRequirePermissionDep:
    @pytest.mark.asyncio
    async def test_dep_blocks_when_role_below_seed(self) -> None:
        dep = require_permission_dep("ai", "provider_create")  # needs 80
        req = _fake_request("admin")  # 60 — below
        with pytest.raises(ForbiddenError):
            await dep(req)

    @pytest.mark.asyncio
    async def test_dep_allows_when_role_at_or_above_seed(self) -> None:
        dep = require_permission_dep("ai", "provider_read")  # needs 60
        req = _fake_request("admin")  # 60 — exact match
        await dep(req)  # no exception

    def test_dep_name_includes_module_and_permission(self) -> None:
        """Closure name surfaces in OpenAPI / debug — must be self-describing."""
        dep = require_permission_dep("ai", "binding_delete")
        assert dep.__name__ == "require_ai_binding_delete"
