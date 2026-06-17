"""b+c — RBAC matrix tests for admin_bots / admin_metrics /
admin_policy / admin_audit routes.

Verifies that the 10 routes wired in this phase are gated through the
metadata-driven ``module_permissions`` table (Phase 1 seed) and that the
role hierarchy holds across all 7 roles (guest..super_admin).

Mirrors the structure of ``test_rbac_admin_ai.py`` so a drift between the
seed file and the wiring is caught here rather than at run-time. We do NOT
boot the full FastAPI app — we exercise the ``require_permission_dep``
factory + the underlying ``require_permission`` function with a mocked
Redis cache returning the seed map. This isolates the RBAC contract from
DB / JWT middleware / settings concerns.
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
from ragbot.interfaces.http.routes import (
    admin_audit,
    admin_bots,
    admin_metrics,
    admin_policy,
)
from ragbot.shared.errors import ForbiddenError


# Seed mapping mirrored from scripts/seed_rbac_permissions_s11b.py for the
# routes wired in Phase 2b+c. Test failure here = drift between seed + tests.
_PHASE2BC_PERMISSIONS: dict[str, int] = {
    # admin_bots.py (4 routes)
    "bot:create": 60,
    "bot:update": 40,
    "bot:delete": 60,
    "bot:list": 60,
    # admin_metrics.py (3 routes)
    "system:metrics_overview": 60,
    "system:metrics_by_model": 60,
    "system:metrics_top_questions": 60,
    # admin_policy.py (2 routes — mutate only)
    "policy:capability_upsert": 80,
    "policy:policy_upsert": 80,
    # admin_audit.py (1 route)
    "admin:audit_message_read": 60,
}


def _fake_request(role: str, *, tenant_id: int | None = 1) -> Any:
    """Minimal request stub with a Redis client returning the seed map."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(_PHASE2BC_PERMISSIONS))
    redis.set = AsyncMock(return_value=None)
    container = MagicMock()
    container.redis_client = MagicMock(return_value=redis)
    container.session_factory = MagicMock()
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(role=role, tenant_id=tenant_id),
    )


# ---------------------------------------------------------------------------
# 1. Static contract — the 10 wired routes must each declare a perm gate.
# ---------------------------------------------------------------------------


class TestPhase2RoutesGated:
    """Verify the 10 Phase 2b+c routes carry a ``require_permission_dep``."""

    def _gated_paths(self, router: Any) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for r in router.routes:
            for dep in getattr(r, "dependencies", ()) or ():
                fn = getattr(dep, "dependency", None)
                name = getattr(fn, "__name__", "") if fn else ""
                if name.startswith("require_"):
                    method = next(iter(r.methods or {""}))
                    out.append((method, r.path, name))
        return out

    def test_admin_bots_has_four_gated_routes(self) -> None:
        gated = self._gated_paths(admin_bots.router)
        names = sorted(n for _, _, n in gated)
        # 4 wired in Phase 2b: create / update / delete / list.
        assert "require_bot_create" in names
        assert "require_bot_update" in names
        assert "require_bot_delete" in names
        assert "require_bot_list" in names
        # At least 4 — cache routes may also be gated by parallel work.
        assert len(gated) >= 4, f"got: {gated}"

    def test_admin_metrics_has_three_gated_routes(self) -> None:
        gated = self._gated_paths(admin_metrics.router)
        names = sorted(n for _, _, n in gated)
        assert "require_system_metrics_overview" in names
        assert "require_system_metrics_by_model" in names
        assert "require_system_metrics_top_questions" in names
        assert len(gated) >= 3, f"got: {gated}"

    def test_admin_policy_has_two_mutate_gated_routes(self) -> None:
        gated = self._gated_paths(admin_policy.router)
        names = sorted(n for _, _, n in gated)
        assert "require_policy_capability_upsert" in names
        assert "require_policy_policy_upsert" in names
        assert len(gated) >= 2, f"got: {gated}"

    def test_admin_audit_has_one_gated_route(self) -> None:
        gated = self._gated_paths(admin_audit.router)
        names = sorted(n for _, _, n in gated)
        assert "require_admin_audit_message_read" in names

    def test_no_role_literal_used_for_gating_in_wired_files(self) -> None:
        """Zero-hardcode: no ``role == "<name>"`` / ``role in (...)`` patterns
        used to gate access in the four wired modules. Incidental string
        literals (audit-actor fallbacks, etc.) are fine — we only ban patterns
        that gate access on a hard-coded role name.
        """
        import re
        from pathlib import Path

        forbidden = [
            r"role\s*==\s*[\"']",
            r"role\s+in\s*\([\"']",
        ]
        for mod in (admin_bots, admin_metrics, admin_policy, admin_audit):
            src = Path(mod.__file__).read_text()
            for pat in forbidden:
                assert re.search(pat, src) is None, (
                    f"forbidden gating pattern {pat!r} present in "
                    f"{Path(mod.__file__).name}"
                )


# ---------------------------------------------------------------------------
# 2. 7-role × 4-critical-route matrix (>= 28 assertions).
# ---------------------------------------------------------------------------


class TestSevenRoleMatrix:
    """Drive ``require_permission`` directly to assert the level hierarchy.

    Critical routes chosen to span every level boundary in the seed:
        - bot.update         (40, operator)
        - bot.create         (60, admin)
        - policy.policy_upsert (80, tenant)
        - admin.audit_message_read (60, admin)
    """

    _CRITICAL: list[tuple[str, str, int]] = [
        ("bot", "update", 40),
        ("bot", "create", 60),
        ("policy", "policy_upsert", 80),
        ("admin", "audit_message_read", 60),
    ]

    # Map: role -> level, mirroring shared/rbac.py constants.
    _ROLES: dict[str, int] = {
        "guest": 0,
        "viewer": 10,
        "user": 20,
        "operator": 40,
        "admin": 60,
        "tenant": 80,
        "super_admin": 100,
    }

    @pytest.mark.asyncio
    async def test_role_level_matrix_holds_for_critical_routes(self) -> None:
        """For each (role, route): pass iff level >= required."""
        for role, role_level in self._ROLES.items():
            for module, perm, required in self._CRITICAL:
                req = _fake_request(role)
                if role_level >= required:
                    # Must NOT raise.
                    await require_permission(req, module, perm)
                else:
                    with pytest.raises(ForbiddenError):
                        await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_guest_blocked_on_every_phase2_perm(self) -> None:
        req = _fake_request("guest")
        for key, level in _PHASE2BC_PERMISSIONS.items():
            module, perm = key.split(":")
            if level <= 0:
                continue
            with pytest.raises(ForbiddenError):
                await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_super_admin_passes_every_phase2_perm(self) -> None:
        """super_admin=100 covers the whole matrix incl. cross-tenant."""
        req = _fake_request("super_admin")
        for key in _PHASE2BC_PERMISSIONS:
            module, perm = key.split(":")
            await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_admin_passes_reads_blocks_tenant_only_mutates(self) -> None:
        """admin=60 passes 60-level gates but is blocked on policy 80 mutates."""
        req = _fake_request("admin")
        # admin reads its own audit + lists bots.
        await require_permission(req, "bot", "list")
        await require_permission(req, "admin", "audit_message_read")
        await require_permission(req, "system", "metrics_overview")
        # admin cannot upsert tenant-scoped policy (level 80).
        with pytest.raises(ForbiddenError):
            await require_permission(req, "policy", "policy_upsert")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "policy", "capability_upsert")

    @pytest.mark.asyncio
    async def test_operator_can_update_bot_only(self) -> None:
        """operator=40 hits exactly bot.update; everything else blocked."""
        req = _fake_request("operator")
        await require_permission(req, "bot", "update")  # 40 == 40
        with pytest.raises(ForbiddenError):
            await require_permission(req, "bot", "create")  # 60
        with pytest.raises(ForbiddenError):
            await require_permission(req, "bot", "delete")  # 60
        with pytest.raises(ForbiddenError):
            await require_permission(req, "bot", "list")  # 60


# ---------------------------------------------------------------------------
# 3. Dep factory — Depends() wrapper enforces same gate as bare function.
# ---------------------------------------------------------------------------


class TestRequirePermissionDepPhase2:
    @pytest.mark.asyncio
    async def test_bot_create_dep_blocks_viewer(self) -> None:
        dep = require_permission_dep("bot", "create")  # 60
        req = _fake_request("viewer")  # 10
        with pytest.raises(ForbiddenError):
            await dep(req)

    @pytest.mark.asyncio
    async def test_bot_update_dep_allows_operator_exactly(self) -> None:
        """Boundary: operator=40 == bot.update=40."""
        dep = require_permission_dep("bot", "update")
        req = _fake_request("operator")
        await dep(req)  # no exception — exact equality passes

    @pytest.mark.asyncio
    async def test_policy_upsert_dep_blocks_admin(self) -> None:
        """Boundary: admin=60 < policy.policy_upsert=80 → blocked."""
        dep = require_permission_dep("policy", "policy_upsert")
        req = _fake_request("admin")
        with pytest.raises(ForbiddenError):
            await dep(req)

    @pytest.mark.asyncio
    async def test_policy_upsert_dep_allows_tenant(self) -> None:
        dep = require_permission_dep("policy", "policy_upsert")
        req = _fake_request("tenant")  # 80
        await dep(req)

    def test_dep_names_are_self_describing(self) -> None:
        """OpenAPI / debug surfaces — names must show module + permission."""
        assert (
            require_permission_dep("bot", "delete").__name__
            == "require_bot_delete"
        )
        assert (
            require_permission_dep("system", "metrics_overview").__name__
            == "require_system_metrics_overview"
        )
        assert (
            require_permission_dep("admin", "audit_message_read").__name__
            == "require_admin_audit_message_read"
        )


# ---------------------------------------------------------------------------
# 4. Cross-tenant red-team — non-super_admin role with tenant=A cannot pass
#    the gate by spoofing tenant=B in request.state. Tenant filtering is
#    enforced at the repo layer (existing pattern), but the RBAC gate itself
#    must NOT leak any cross-tenant signal — same role/level, same outcome.
# ---------------------------------------------------------------------------


class TestCrossTenantRbacInvariance:
    """RBAC gate decision depends solely on role-level, never on tenant_id.

    The tenant scoping is enforced separately by the repo layer (e.g.
    ``BotManagementService.update_bot`` raises ``CrossTenantForbiddenError``
    when the caller's tenant != bot's tenant). The RBAC layer must remain
    tenant-agnostic so that both tenants A and B with role=admin get the
    same Allow/Deny answer for the same permission.
    """

    @pytest.mark.asyncio
    async def test_tenant_a_admin_and_tenant_b_admin_get_same_decision(
        self,
    ) -> None:
        req_a = _fake_request("admin", tenant_id=1001)
        req_b = _fake_request("admin", tenant_id=2002)
        # Both should pass on the admin-floor gates.
        await require_permission(req_a, "bot", "list")
        await require_permission(req_b, "bot", "list")
        # Both should fail on the tenant-floor gates.
        with pytest.raises(ForbiddenError):
            await require_permission(req_a, "policy", "policy_upsert")
        with pytest.raises(ForbiddenError):
            await require_permission(req_b, "policy", "policy_upsert")

    @pytest.mark.asyncio
    async def test_super_admin_bypass_independent_of_tenant_id(self) -> None:
        """super_admin must pass every gate regardless of which tenant slot
        they're operating from — the cross-tenant override lives at the repo
        level (``admin_tenant=None`` in BotManagementService), not here.
        """
        req_x = _fake_request("super_admin", tenant_id=None)
        req_y = _fake_request("super_admin", tenant_id=99999)
        for key in _PHASE2BC_PERMISSIONS:
            module, perm = key.split(":")
            await require_permission(req_x, module, perm)
            await require_permission(req_y, module, perm)
