"""Admin tenant policy CRUD integration tests.

Mirrors the structure of ``test_rbac_admin_ai.py`` / ``test_rbac_admin_routes.py``
— exercises the route module's gating + handler logic with mocked
session_factory + Redis double, without booting the full FastAPI app.

Coverage matrix (8 tests):
    1. super_admin can read any tenant's policy.
    2. tenant admin reads its own policy row.
    3. tenant admin gets 404 (not 403) when reading another tenant.
    4. super_admin PATCH writes the rate-limit override.
    5. tenant admin PATCH (level 80) is denied by RBAC dep (level 100 required).
    6. PATCH invalidates the per-tenant Redis cache.
    7. PATCH with only one field leaves the other two untouched.
    8. PATCH ``monthly_token_cap=0`` stores ``0`` (soft-unlimited semantics).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.interfaces.http.middlewares.rbac import require_permission
from ragbot.interfaces.http.routes import admin_tenant_policy
from ragbot.shared.errors import ForbiddenError


_S12A_TENANT_PERMISSIONS: dict[str, int] = {
    "tenant:policy_read": 80,
    "tenant:policy_update": 100,
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSession:
    """Stand-in for AsyncSession — ``execute`` returns a row tuple stub.

    The route module only needs the methods ``TenantRepository`` calls
    (``execute`` + ``commit``). ``execute_results`` is a queue of tuples
    matching the column ordering in the repository SQL.
    """

    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self._rows = list(rows)
        self.committed = False
        self.executed_sql: list[str] = []
        self.executed_params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.executed_sql.append(str(stmt))
        self.executed_params.append(dict(params or {}))
        # Pop one row; treat list exhaustion as None (UPDATE no-row).
        row = self._rows.pop(0) if self._rows else None
        result = MagicMock()
        result.fetchone = MagicMock(return_value=row)
        return result

    async def commit(self) -> None:
        self.committed = True


def _session_factory(rows: list[tuple[Any, ...] | None]) -> Any:
    """Build a session_factory() that yields a context manager over _FakeSession."""
    session = _FakeSession(rows)

    @asynccontextmanager
    async def _ctx() -> Any:
        yield session

    sf_callable = MagicMock(return_value=_ctx())
    return sf_callable, session


def _container(
    *,
    rows: list[tuple[Any, ...] | None],
    redis_perms: dict[str, int],
) -> tuple[MagicMock, _FakeSession, MagicMock, MagicMock]:
    """Build the request.app.state.container double.

    Returns ``(container, fake_session, fake_cache, fake_audit_repo)`` so
    individual tests can assert against session executions, cache
    invalidations, and audit-log writes.
    """
    sf_callable, fake_session = _session_factory(rows)

    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(redis_perms))
    redis.set = AsyncMock(return_value=None)

    fake_cache = MagicMock()
    fake_cache.invalidate = AsyncMock(return_value=None)

    # Audit repo double — captures every ``write_audit`` call so tests can
    # assert PATCH paths actually emit a forensic row. Mirrors the real
    # ``AIConfigRepositoryPort.write_audit(entry: AuditEntry)`` signature.
    fake_audit_repo = MagicMock()
    fake_audit_repo.write_audit = AsyncMock(return_value=None)

    container = MagicMock()
    container.session_factory = MagicMock(return_value=sf_callable)
    container.redis_client = MagicMock(return_value=redis)
    container.tenant_config_cache = MagicMock(return_value=fake_cache)
    container.ai_config_repo = MagicMock(return_value=fake_audit_repo)
    return container, fake_session, fake_cache, fake_audit_repo


def _request(role: str, *, tenant_id: UUID | None, container: MagicMock) -> Any:
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(role=role, tenant_id=tenant_id),
    )


def _tenant_row(
    tid: UUID,
    *,
    name: str = "Acme",
    bypass: bool = False,
    rl: int | None = 200,
    cap: int | None = 1_000_000,
    tid_int: int | None = 42,
) -> tuple[Any, ...]:
    """Tuple shape matching repository SELECT order."""
    return (tid, name, bypass, rl, cap, tid_int)


# ---------------------------------------------------------------------------
# 1. Static contract — module wires both routes through require_permission_dep.
# ---------------------------------------------------------------------------


class TestRoutesGated:
    def test_two_routes_carry_permission_dep(self) -> None:
        gated_names: list[str] = []
        for r in admin_tenant_policy.router.routes:
            for dep in getattr(r, "dependencies", ()) or ():
                fn = getattr(dep, "dependency", None)
                name = getattr(fn, "__name__", "") if fn else ""
                if name.startswith("require_tenant_"):
                    gated_names.append(name)
        assert "require_tenant_policy_read" in gated_names
        assert "require_tenant_policy_update" in gated_names
        assert len(gated_names) == 2, gated_names

    def test_no_role_literal_used_for_gating(self) -> None:
        import re
        from pathlib import Path
        src = Path(admin_tenant_policy.__file__).read_text()
        for pat in (r"role\s*==\s*[\"']", r"role\s+in\s*\([\"']"):
            assert re.search(pat, src) is None, (
                f"forbidden gating pattern {pat!r} present in admin_tenant_policy.py"
            )


# ---------------------------------------------------------------------------
# 2. RBAC matrix — read 80, update 100.
# ---------------------------------------------------------------------------


class TestRbacMatrix:
    @pytest.mark.asyncio
    async def test_super_admin_passes_read_and_update(self) -> None:
        container, _s, _c, _ar = _container(rows=[], redis_perms=_S12A_TENANT_PERMISSIONS)
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        await require_permission(req, "tenant", "policy_read")
        await require_permission(req, "tenant", "policy_update")

    @pytest.mark.asyncio
    async def test_tenant_admin_passes_read_blocks_update(self) -> None:
        container, _s, _c, _ar = _container(rows=[], redis_perms=_S12A_TENANT_PERMISSIONS)
        req = _request("tenant", tenant_id=uuid4(), container=container)
        await require_permission(req, "tenant", "policy_read")  # 80 == 80
        with pytest.raises(ForbiddenError):
            await require_permission(req, "tenant", "policy_update")  # needs 100

    @pytest.mark.asyncio
    async def test_admin_blocked_on_both(self) -> None:
        container, _s, _c, _ar = _container(rows=[], redis_perms=_S12A_TENANT_PERMISSIONS)
        req = _request("admin", tenant_id=uuid4(), container=container)
        with pytest.raises(ForbiddenError):
            await require_permission(req, "tenant", "policy_read")  # 60 < 80
        with pytest.raises(ForbiddenError):
            await require_permission(req, "tenant", "policy_update")  # 60 < 100


# ---------------------------------------------------------------------------
# 3. Handler logic — drive the route fns directly with mocked container.
# ---------------------------------------------------------------------------


class TestGetTenantPolicy:
    @pytest.mark.asyncio
    async def test_super_admin_get_any_tenant_policy(self) -> None:
        target_tid = uuid4()
        caller_tid = uuid4()  # different from target — super_admin still sees it
        container, _session, _cache, _ar = _container(
            rows=[_tenant_row(target_tid, rl=200, cap=500_000)],
            redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=caller_tid, container=container)
        resp = await admin_tenant_policy.get_tenant_policy(target_tid, req)
        assert resp["ok"] is True
        data = resp["data"]
        assert data["record_tenant_id"] == str(target_tid)
        assert data["rate_limit_per_min"] == 200
        assert data["monthly_token_cap"] == 500_000
        # Internal-only field must not leak in the response.
        assert "tenant_id_int" not in data

    @pytest.mark.asyncio
    async def test_tenant_admin_get_own_policy(self) -> None:
        tid = uuid4()
        container, _s, _c, _ar = _container(
            rows=[_tenant_row(tid, name="Self")],
            redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("tenant", tenant_id=tid, container=container)
        resp = await admin_tenant_policy.get_tenant_policy(tid, req)
        assert resp["data"]["name"] == "Self"

    @pytest.mark.asyncio
    async def test_tenant_admin_get_other_tenant_returns_404(self) -> None:
        target_tid = uuid4()
        caller_tid = uuid4()  # != target
        container, _s, _c, _ar = _container(
            rows=[_tenant_row(target_tid)],
            redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("tenant", tenant_id=caller_tid, container=container)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await admin_tenant_policy.get_tenant_policy(target_tid, req)
        # 404, not 403 — anti-enumeration.
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_returns_404_when_row_missing(self) -> None:
        target_tid = uuid4()
        container, _s, _c, _ar = _container(
            rows=[None], redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await admin_tenant_policy.get_tenant_policy(target_tid, req)
        assert exc.value.status_code == 404


class TestPatchTenantPolicy:
    @pytest.mark.asyncio
    async def test_super_admin_patch_rate_limit(self) -> None:
        from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
            TenantPolicyUpdateRequest,
        )
        tid = uuid4()
        # Three SQL calls: existing get, UPDATE returning row, post-update get.
        rows: list[tuple[Any, ...] | None] = [
            _tenant_row(tid, rl=200),     # existing get_policy
            (tid,),                        # UPDATE...RETURNING id
            _tenant_row(tid, rl=500),     # second get_policy after UPDATE
        ]
        container, session, cache, _ar = _container(
            rows=rows, redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        body = TenantPolicyUpdateRequest(rate_limit_per_min=500)
        resp = await admin_tenant_policy.patch_tenant_policy(tid, body, req)
        assert resp["ok"] is True
        assert resp["data"]["rate_limit_per_min"] == 500
        assert session.committed is True
        # Verify UPDATE statement targeted only rate_limit_per_min.
        update_sql = session.executed_sql[1]
        assert "rate_limit_per_min" in update_sql
        assert "bypass_rate_limit" not in update_sql
        assert "monthly_token_cap" not in update_sql

    @pytest.mark.asyncio
    async def test_patch_invalidates_cache(self) -> None:
        from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
            TenantPolicyUpdateRequest,
        )
        tid = uuid4()
        # tenant_id_int = 7 — invalidate must be called with int(7).
        rows: list[tuple[Any, ...] | None] = [
            _tenant_row(tid, tid_int=7),
            (tid,),
            _tenant_row(tid, bypass=True, tid_int=7),
        ]
        container, _session, cache, _ar = _container(
            rows=rows, redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        body = TenantPolicyUpdateRequest(bypass_rate_limit=True)
        await admin_tenant_policy.patch_tenant_policy(tid, body, req)
        # Cache invalidated exactly once with the int form.
        cache.invalidate.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_patch_partial_only_writes_passed_fields(self) -> None:
        from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
            TenantPolicyUpdateRequest,
        )
        tid = uuid4()
        rows: list[tuple[Any, ...] | None] = [
            _tenant_row(tid, bypass=False, rl=200, cap=1_000),
            (tid,),
            _tenant_row(tid, bypass=True, rl=200, cap=1_000),  # only bypass flipped
        ]
        container, session, _c, _ar = _container(
            rows=rows, redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        body = TenantPolicyUpdateRequest(bypass_rate_limit=True)
        resp = await admin_tenant_policy.patch_tenant_policy(tid, body, req)
        # Only bypass column should appear in UPDATE SET clause.
        update_params = session.executed_params[1]
        assert "bypass_rate_limit" in update_params
        assert "rate_limit_per_min" not in update_params
        assert "monthly_token_cap" not in update_params
        # Other two fields must come back unchanged.
        assert resp["data"]["rate_limit_per_min"] == 200
        assert resp["data"]["monthly_token_cap"] == 1_000

    @pytest.mark.asyncio
    async def test_patch_zero_token_cap_stored_as_zero(self) -> None:
        """``monthly_token_cap=0`` is a real write (soft-unlimited), not skipped."""
        from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
            TenantPolicyUpdateRequest,
        )
        tid = uuid4()
        rows: list[tuple[Any, ...] | None] = [
            _tenant_row(tid, cap=1_000_000),
            (tid,),
            _tenant_row(tid, cap=0),
        ]
        container, session, _c, _ar = _container(
            rows=rows, redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        body = TenantPolicyUpdateRequest(monthly_token_cap=0)
        resp = await admin_tenant_policy.patch_tenant_policy(tid, body, req)
        assert resp["data"]["monthly_token_cap"] == 0
        # UPDATE actually fired (zero is not skipped).
        assert "monthly_token_cap" in session.executed_params[1]
        assert session.executed_params[1]["monthly_token_cap"] == 0

    @pytest.mark.asyncio
    async def test_patch_returns_404_when_tenant_missing(self) -> None:
        from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
            TenantPolicyUpdateRequest,
        )
        tid = uuid4()
        rows: list[tuple[Any, ...] | None] = [None]  # existing get_policy → None
        container, _session, cache, audit_repo = _container(
            rows=rows, redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=uuid4(), container=container)
        body = TenantPolicyUpdateRequest(bypass_rate_limit=True)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await admin_tenant_policy.patch_tenant_policy(tid, body, req)
        assert exc.value.status_code == 404
        # No cache invalidate when the row doesn't exist.
        cache.invalidate.assert_not_awaited()
        # No audit row written either — there was nothing to mutate.
        audit_repo.write_audit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_patch_emits_audit_log(self) -> None:
        """Successful PATCH writes an ``audit_log`` row with action='tenant_policy_update'.

        Forensic guarantee: every flip of bypass_rate_limit / rate_limit_per_min /
        monthly_token_cap is reconstructable post-hoc from the audit table.
        Ref: architect-review P1 finding "audit log gap".
        """
        from ragbot.application.ports.ai_config_port import AuditEntry
        from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
            TenantPolicyUpdateRequest,
        )

        tid = uuid4()
        caller_tid = uuid4()
        # before row: rl=200; after row: rl=500.
        rows: list[tuple[Any, ...] | None] = [
            _tenant_row(tid, rl=200, cap=1_000),
            (tid,),
            _tenant_row(tid, rl=500, cap=1_000),
        ]
        container, _session, _cache, audit_repo = _container(
            rows=rows, redis_perms=_S12A_TENANT_PERMISSIONS,
        )
        req = _request("super_admin", tenant_id=caller_tid, container=container)
        body = TenantPolicyUpdateRequest(rate_limit_per_min=500)
        resp = await admin_tenant_policy.patch_tenant_policy(tid, body, req)
        assert resp["ok"] is True

        # Exactly one audit row, well-formed.
        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "tenant_policy_update"
        assert entry.resource_type == "tenant"
        assert str(entry.resource_id) == str(tid)
        # Caller's tenant_id propagated for tenant-scoped audit listing.
        assert str(entry.record_tenant_id) == str(caller_tid)
        # Tenant-policy mutations are not bot-scoped.
        assert entry.record_bot_id is None
        # Before/after diff captures the mutated column only and excludes
        # internal mapping columns (tenant_id_int / record_tenant_id / name).
        assert entry.before == {
            "bypass_rate_limit": False,
            "rate_limit_per_min": 200,
            "monthly_token_cap": 1_000,
        }
        assert entry.after == {
            "bypass_rate_limit": False,
            "rate_limit_per_min": 500,
            "monthly_token_cap": 1_000,
        }
        # No user_id / trace_id was set on request.state in the fixture, so
        # the route falls back to the documented "unknown" / "n/a" sentinels
        # rather than crashing — keeps the audit row honest about identity.
        assert entry.actor_user_id == "unknown"
        assert entry.trace_id == "n/a"
