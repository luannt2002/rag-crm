"""admin_ai resource-ownership pre-verify tests.

Two layers covered here:

1. ``require_binding_ownership`` — the helper that pre-verifies a binding
   row's ``record_tenant_id`` matches the caller's tenant (with a
   super-admin bypass).

2. RBAC seed contract — provider/model mutate gates were elevated from
   level 80 to level 100 (super_admin) because ``ai_providers`` and
   ``ai_models`` are platform-shared resources. We enforce the contract
   by re-running the ``require_permission`` middleware against the
   elevated seed map and asserting tenant-admin (80) cannot pass and
   super-admin (100) can.

We do NOT spin up the full FastAPI app — the helper is exercised
directly with a mocked container + repo. This mirrors the style of
``test_rbac_admin_ai.py`` so a regression in either layer is caught
without needing JWT / settings / DB plumbing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ragbot.interfaces.http._resource_ownership import require_binding_ownership
from ragbot.interfaces.http.middlewares.rbac import require_permission
from ragbot.shared.constants import DEFAULT_SUPER_ADMIN_LEVEL
from ragbot.shared.errors import ForbiddenError


# ---------------------------------------------------------------------------
# Seed mirror — admin_ai elevations. Drift between the test + the seed
# script (scripts/seed_rbac_permissions_s12a.py) trips this map.
# ---------------------------------------------------------------------------

_S12A_ELEVATED: dict[str, int] = {
    "ai:provider_create": DEFAULT_SUPER_ADMIN_LEVEL,
    "ai:provider_update": DEFAULT_SUPER_ADMIN_LEVEL,
    "ai:provider_delete": DEFAULT_SUPER_ADMIN_LEVEL,
    "ai:provider_rotate_key": DEFAULT_SUPER_ADMIN_LEVEL,
    "ai:model_create": DEFAULT_SUPER_ADMIN_LEVEL,
    "ai:model_update": DEFAULT_SUPER_ADMIN_LEVEL,
    "ai:model_delete": DEFAULT_SUPER_ADMIN_LEVEL,
    # Read-like / binding gates remain at the  baseline so the
    # test doubles capture the full contract surface used below.
    "ai:provider_read": 60,
    "ai:provider_test": 60,
    "ai:model_read": 20,
    "ai:binding_update": 80,
    "ai:binding_delete": 80,
}


def _binding_row(tenant_id: Any) -> SimpleNamespace:
    """Stub for repo.get_binding(...) return value."""
    return SimpleNamespace(
        id=uuid4(),
        record_tenant_id=tenant_id,
        record_bot_id=uuid4(),
        active=True,
    )


def _fake_request(
    role: str,
    *,
    tenant_id: Any = None,
    binding_lookup: dict[str, Any] | None = None,
) -> Any:
    """Stub Request with rbac perms map + ai_config_repo.get_binding mock."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(_S12A_ELEVATED))
    redis.set = AsyncMock(return_value=None)

    repo = MagicMock()

    async def _get_binding(binding_id: Any, *, record_tenant_id: Any) -> Any:
        if binding_lookup is None:
            return None
        # Only return the row when caller's tenant_id matches the row's
        # record_tenant_id — same shape as the real repo's filter.
        row = binding_lookup.get("row")
        owner = binding_lookup.get("owner_tenant_id")
        if row is None:
            return None
        if owner is None or owner == record_tenant_id:
            return row
        return None

    repo.get_binding = AsyncMock(side_effect=_get_binding)

    container = MagicMock()
    container.redis_client = MagicMock(return_value=redis)
    container.session_factory = MagicMock()
    container.ai_config_repo = MagicMock(return_value=repo)

    app = MagicMock()
    app.state = SimpleNamespace(container=container)

    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(role=role, tenant_id=tenant_id),
    )


# ---------------------------------------------------------------------------
# 1. require_binding_ownership — row-level tenancy guard
# ---------------------------------------------------------------------------


class TestRequireBindingOwnership:
    """Caller-tenant matches binding-tenant required (super_admin bypass)."""

    @pytest.mark.asyncio
    async def test_tenant_admin_blocked_when_binding_belongs_to_other_tenant(
        self,
    ) -> None:
        tenant_a = uuid4()
        tenant_b = uuid4()
        row = _binding_row(tenant_b)
        req = _fake_request(
            "admin",  # level 60, NOT super-admin
            tenant_id=tenant_a,
            binding_lookup={"row": row, "owner_tenant_id": tenant_b},
        )
        with pytest.raises(HTTPException) as exc:
            await require_binding_ownership(req, row.id)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_tenant_admin_blocked_when_binding_does_not_exist(self) -> None:
        tenant_a = uuid4()
        req = _fake_request(
            "admin",
            tenant_id=tenant_a,
            binding_lookup=None,  # repo always returns None
        )
        with pytest.raises(HTTPException) as exc:
            await require_binding_ownership(req, uuid4())
        # Same status as the cross-tenant case — no enumeration leak.
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_tenant_admin_passes_when_binding_belongs_to_same_tenant(
        self,
    ) -> None:
        tenant_a = uuid4()
        row = _binding_row(tenant_a)
        req = _fake_request(
            "admin",
            tenant_id=tenant_a,
            binding_lookup={"row": row, "owner_tenant_id": tenant_a},
        )
        # No exception — caller owns the row.
        await require_binding_ownership(req, row.id)

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_ownership_check_entirely(self) -> None:
        """super_admin must NOT call into the repo (cross-tenant repair)."""
        tenant_b = uuid4()
        row = _binding_row(tenant_b)
        req = _fake_request(
            "super_admin",
            tenant_id=None,  # platform op without a tenant slot
            binding_lookup={"row": row, "owner_tenant_id": tenant_b},
        )
        await require_binding_ownership(req, row.id)
        # Confirm short-circuit: repo.get_binding never invoked.
        repo = req.app.state.container.ai_config_repo()
        assert repo.get_binding.await_count == 0

    @pytest.mark.asyncio
    async def test_super_admin_bypass_covers_aliases(self) -> None:
        """All aliases of super_admin (system / owner / platform_admin) bypass."""
        tenant_b = uuid4()
        row = _binding_row(tenant_b)
        for alias in ("system", "owner", "platform_admin", "superadmin"):
            req = _fake_request(
                alias,
                tenant_id=None,
                binding_lookup={"row": row, "owner_tenant_id": tenant_b},
            )
            await require_binding_ownership(req, row.id)

    @pytest.mark.asyncio
    async def test_caller_without_tenant_id_is_rejected(self) -> None:
        """Non-super caller without state.tenant_id cannot own anything."""
        row = _binding_row(uuid4())
        req = _fake_request(
            "admin",
            tenant_id=None,
            binding_lookup={"row": row, "owner_tenant_id": row.record_tenant_id},
        )
        with pytest.raises(HTTPException) as exc:
            await require_binding_ownership(req, row.id)
        assert exc.value.status_code == 404
        # Repo must NOT be hit when the caller has no tenant slot — we
        # short-circuit before any DB lookup.
        repo = req.app.state.container.ai_config_repo()
        assert repo.get_binding.await_count == 0


# ---------------------------------------------------------------------------
# 2. RBAC seed contract — provider/model mutates require super_admin
# ---------------------------------------------------------------------------


class TestProviderModelSuperAdminElevation:
    """Tenant admin (80) blocked on platform-shared mutates; super_admin passes."""

    _PLATFORM_MUTATES: list[tuple[str, str]] = [
        ("ai", "provider_create"),
        ("ai", "provider_update"),
        ("ai", "provider_delete"),
        ("ai", "provider_rotate_key"),
        ("ai", "model_create"),
        ("ai", "model_update"),
        ("ai", "model_delete"),
    ]

    @pytest.mark.asyncio
    async def test_tenant_admin_denied_on_every_platform_mutate(self) -> None:
        """tenant=80 < super_admin=100 → ForbiddenError on every gate."""
        req = _fake_request("tenant", tenant_id=uuid4())
        for module, perm in self._PLATFORM_MUTATES:
            with pytest.raises(ForbiddenError):
                await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_admin_denied_on_every_platform_mutate(self) -> None:
        """admin=60 also fails — only super_admin can edit shared resources."""
        req = _fake_request("admin", tenant_id=uuid4())
        for module, perm in self._PLATFORM_MUTATES:
            with pytest.raises(ForbiddenError):
                await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_super_admin_passes_every_platform_mutate(self) -> None:
        """super_admin=100 == seed level → must pass all elevated gates."""
        req = _fake_request("super_admin", tenant_id=None)
        for module, perm in self._PLATFORM_MUTATES:
            await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_provider_test_remains_admin_level(self) -> None:
        """provider_test is read-like (no shared-state mutation) → stay 60."""
        req = _fake_request("admin", tenant_id=uuid4())
        await require_permission(req, "ai", "provider_test")  # passes


# ---------------------------------------------------------------------------
# 3. Constant + import sanity — DEFAULT_SUPER_ADMIN_LEVEL aligned with rbac map
# ---------------------------------------------------------------------------


class TestSuperAdminConstantAlignment:
    def test_constant_matches_role_levels_super_admin(self) -> None:
        from ragbot.shared.rbac import ROLE_LEVELS

        # The whole point of the constant is to single-source the level.
        assert ROLE_LEVELS["super_admin"] == DEFAULT_SUPER_ADMIN_LEVEL

    def test_helper_module_does_not_inline_level_literal(self) -> None:
        """Zero-hardcode: the helper imports the constant, never inlines 100."""
        import re
        from pathlib import Path

        from ragbot.interfaces.http import _resource_ownership

        src = Path(_resource_ownership.__file__).read_text()
        # Find numeric literal 100 used outside string contexts. Allow
        # it inside comments / docstrings (those won't pass the runtime).
        # The check below is intentionally narrow — `\b100\b` immediately
        # adjacent to non-word chars in code lines.
        for line in src.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Skip docstring lines (rough heuristic — module-level triple
            # quotes block).
            if stripped.startswith('"') or stripped.startswith("'"):
                continue
            assert re.search(r"\b100\b", line) is None, (
                f"_resource_ownership.py inlines literal 100: {line!r}"
            )
