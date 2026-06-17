"""Admin tenant CRUD integration tests.

Mirrors the structure of ``test_admin_tenant_policy.py``: the route module
is exercised against an in-memory ``TenantRepository`` test double + a
mocked container. We deliberately bypass the full FastAPI lifespan +
session factory to keep the test surface narrow and deterministic.

Coverage matrix
---------------
1.  POST creates tenant with UUID id, slug, name; emits audit_log.
2.  POST duplicate slug → 409 (TenantSlugConflictError).
3.  POST as non-superadmin → 403 (require_min_level guard).
4.  GET returns tenant; missing UUID → 404.
5.  LIST returns items + total + pagination echoes.
6.  LIST with ``search`` filters by name substring.
7.  PATCH updates name + config + rate-limit; invalidates cache.
8.  PATCH triggers tenant_config_cache.invalidate exactly once.
9.  PATCH emits audit_log with before/after diff.
10. DELETE soft-deletes when no active bots; 204; emits audit.
11. DELETE rejects 409 when active bots exist.
12. Path UUID type rejects malformed → handled by FastAPI; we test the
    handler-level guard for missing tenants (404).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.infrastructure.repositories.tenant_repository import (
    TenantHasActiveBotsError,
    TenantSlugConflictError,
)
from ragbot.interfaces.http.routes import admin_tenants
from ragbot.interfaces.http.schemas.admin_tenants import (
    TenantCreateRequest,
    TenantPatchRequest,
)
from ragbot.shared.errors import ForbiddenError


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeTenantRepository:
    """In-memory stand-in for ``TenantRepository``.

    Records every call so tests can assert on ordering + arguments.
    Implements only the surface the route layer touches.
    """

    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[UUID] = []
        # Toggles to drive failure paths without touching the route code.
        self.create_raises: Exception | None = None
        self.update_raises: Exception | None = None
        self.delete_raises: Exception | None = None
        self.active_bot_count: int = 0

    @staticmethod
    def _make_row(
        *,
        record_tenant_id: UUID,
        name: str,
        slug: str,
        config: dict[str, Any] | None = None,
        deleted: bool = False,
    ) -> dict[str, Any]:
        cfg = dict(config or {})
        cfg["slug"] = slug
        now = datetime.now(tz=timezone.utc)
        return {
            "record_tenant_id": record_tenant_id,
            "name": name,
            "slug": slug,
            "config": cfg,
            "bypass_rate_limit": False,
            "rate_limit_per_min": None,
            "monthly_token_cap": None,
            "allowed_origins": [],
            "created_at": now,
            "updated_at": now,
            "deleted_at": now if deleted else None,
        }

    async def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        config: dict[str, Any] | None = None,
        upstream_tenant_id: int | None = None,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {
                "name": name,
                "slug": slug,
                "config": config,
                "upstream_tenant_id": upstream_tenant_id,
            },
        )
        if self.create_raises is not None:
            raise self.create_raises
        rid = uuid4()
        merged = dict(config or {})
        if upstream_tenant_id is not None:
            merged["upstream_tenant_id"] = int(upstream_tenant_id)
        row = self._make_row(
            record_tenant_id=rid, name=name, slug=slug, config=merged,
        )
        self.rows[rid] = row
        return dict(row)

    async def get_tenant(
        self, record_tenant_id: UUID, *, include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        row = self.rows.get(record_tenant_id)
        if row is None:
            return None
        if not include_deleted and row.get("deleted_at") is not None:
            return None
        return dict(row)

    async def list_tenants(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        items = [
            dict(r)
            for r in self.rows.values()
            if include_deleted or r.get("deleted_at") is None
        ]
        if search:
            items = [r for r in items if search.lower() in r["name"].lower()]
        total = len(items)
        return items[offset : offset + limit], total

    async def update_tenant(
        self,
        record_tenant_id: UUID,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        bypass_rate_limit: bool | None = None,
        rate_limit_per_min: int | None = None,
        monthly_token_cap: int | None = None,
        allowed_origins: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        self.update_calls.append(
            {
                "record_tenant_id": record_tenant_id,
                "name": name,
                "config": config,
                "bypass_rate_limit": bypass_rate_limit,
                "rate_limit_per_min": rate_limit_per_min,
                "monthly_token_cap": monthly_token_cap,
                "allowed_origins": allowed_origins,
            },
        )
        if self.update_raises is not None:
            raise self.update_raises
        row = self.rows.get(record_tenant_id)
        if row is None or row.get("deleted_at") is not None:
            return None
        before = dict(row)
        if name is not None:
            row["name"] = name
        if config is not None:
            new_cfg = dict(config)
            existing_slug = (row.get("config") or {}).get("slug")
            if existing_slug is not None:
                new_cfg["slug"] = existing_slug
            row["config"] = new_cfg
            row["slug"] = new_cfg.get("slug")
        if bypass_rate_limit is not None:
            row["bypass_rate_limit"] = bool(bypass_rate_limit)
        if rate_limit_per_min is not None:
            row["rate_limit_per_min"] = int(rate_limit_per_min)
        if monthly_token_cap is not None:
            row["monthly_token_cap"] = int(monthly_token_cap)
        if allowed_origins is not None:
            row["allowed_origins"] = [str(o) for o in allowed_origins]
        row["updated_at"] = datetime.now(tz=timezone.utc)
        return before, dict(row)

    async def soft_delete_tenant(
        self, record_tenant_id: UUID,
    ) -> dict[str, Any] | None:
        self.delete_calls.append(record_tenant_id)
        if self.delete_raises is not None:
            raise self.delete_raises
        if self.active_bot_count > 0:
            raise TenantHasActiveBotsError(self.active_bot_count)
        row = self.rows.get(record_tenant_id)
        if row is None or row.get("deleted_at") is not None:
            return None
        before = dict(row)
        row["deleted_at"] = datetime.now(tz=timezone.utc)
        return before

    async def count_active_bots_for_tenant(
        self, record_tenant_id: UUID,
    ) -> int:
        return self.active_bot_count


def _build_request(
    *,
    role: str,
    record_tenant_id: UUID | None,
    repo: _FakeTenantRepository,
    audit_repo: MagicMock,
    cache: MagicMock,
) -> Any:
    """Compose a minimal ``Request``-shaped object the route helpers read.

    Only the attribute access paths used by the route module are
    populated — anything else would be a YAGNI risk.
    """

    @asynccontextmanager
    async def _session_ctx() -> Any:
        # The route uses ``TenantRepository(session)``; we monkeypatch the
        # constructor at import-site to return our fake instead. The
        # session itself is unused.
        yield SimpleNamespace()

    sf_callable = MagicMock(return_value=_session_ctx())

    container = MagicMock()
    container.session_factory = MagicMock(return_value=sf_callable)
    container.tenant_config_cache = MagicMock(return_value=cache)
    container.ai_config_repo = MagicMock(return_value=audit_repo)
    # Stash the repo so the patched constructor can hand it back.
    container._test_repo = repo  # type: ignore[attr-defined]

    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            record_tenant_id=record_tenant_id,
            user_id="ops@example.com",
            trace_id="trace-test-001",
        ),
    )


@pytest.fixture
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> _FakeTenantRepository:
    """Patch ``admin_tenants.TenantRepository`` to return our fake.

    The route layer instantiates ``TenantRepository(session)`` inside the
    handler — patching the constructor lets us inject behaviour without
    touching the real DB.
    """
    repo = _FakeTenantRepository()

    def _ctor(_session: Any) -> _FakeTenantRepository:
        return repo

    monkeypatch.setattr(admin_tenants, "TenantRepository", _ctor)
    return repo


@pytest.fixture
def audit_repo() -> MagicMock:
    m = MagicMock()
    m.write_audit = AsyncMock(return_value=None)
    return m


@pytest.fixture
def cache() -> MagicMock:
    m = MagicMock()
    m.invalidate = AsyncMock(return_value=None)
    return m


# ---------------------------------------------------------------------------
# 1. RBAC gate — non-superadmin rejected on every endpoint.
# ---------------------------------------------------------------------------


class TestRBAC:
    @pytest.mark.asyncio
    async def test_create_rejects_tenant_admin(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="tenant", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantCreateRequest(name="Acme", slug="acme")
        with pytest.raises(ForbiddenError):
            await admin_tenants.admin_create_tenant(body, req)
        # Repo not touched when RBAC blocks.
        assert fake_repo.create_calls == []
        audit_repo.write_audit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_rejects_admin_level(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        with pytest.raises(ForbiddenError):
            await admin_tenants.admin_get_tenant(uuid4(), req)

    @pytest.mark.asyncio
    async def test_list_rejects_guest(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="guest", record_tenant_id=None,
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        with pytest.raises(ForbiddenError):
            await admin_tenants.admin_list_tenants(req)

    @pytest.mark.asyncio
    async def test_patch_rejects_tenant(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="tenant", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantPatchRequest(name="x")
        with pytest.raises(ForbiddenError):
            await admin_tenants.admin_update_tenant(uuid4(), body, req)
        cache.invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_rejects_operator(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="operator", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        with pytest.raises(ForbiddenError):
            await admin_tenants.admin_delete_tenant(uuid4(), req)
        assert fake_repo.delete_calls == []


# ---------------------------------------------------------------------------
# 2. POST /admin/tenants
# ---------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_returns_201_with_uuid_and_audit(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantCreateRequest(
            name="Acme Corp", slug="acme-corp",
            config={"region": "ap-se-1"},
            upstream_tenant_id=42,
        )
        resp = await admin_tenants.admin_create_tenant(body, req)
        assert resp["ok"] is True
        data = resp["data"]
        assert UUID(data["record_tenant_id"]) is not None
        assert data["name"] == "Acme Corp"
        assert data["slug"] == "acme-corp"
        assert data["config"]["upstream_tenant_id"] == 42
        # Audit row.
        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "admin_tenant_create"
        assert entry.resource_type == "tenant"
        assert entry.before is None
        assert entry.after is not None
        # Volatile timestamp fields stripped from audit diff.
        assert "created_at" not in entry.after
        assert "updated_at" not in entry.after
        # Cache is NOT busted on create — there was no prior row to bust.
        cache.invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_slug_returns_409(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        fake_repo.create_raises = TenantSlugConflictError("slug already in use: acme")
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantCreateRequest(name="Acme", slug="acme")
        with pytest.raises(HTTPException) as exc:
            await admin_tenants.admin_create_tenant(body, req)
        assert exc.value.status_code == 409
        # No audit row when the create itself failed.
        audit_repo.write_audit.assert_not_awaited()

    def test_invalid_slug_rejected_at_schema_layer(self) -> None:
        """Pydantic regex enforces ``^[a-z0-9][a-z0-9-]*$`` at validation."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TenantCreateRequest(name="Acme", slug="Has Capital")
        with pytest.raises(ValidationError):
            TenantCreateRequest(name="Acme", slug="under_score")
        with pytest.raises(ValidationError):
            TenantCreateRequest(name="Acme", slug="-leadinghyphen")


# ---------------------------------------------------------------------------
# 3. GET /admin/tenants/{id}
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_get_returns_tenant(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        rid = uuid4()
        fake_repo.rows[rid] = _FakeTenantRepository._make_row(
            record_tenant_id=rid, name="Acme", slug="acme",
        )
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        resp = await admin_tenants.admin_get_tenant(rid, req)
        assert resp["data"]["name"] == "Acme"
        # GET does not write audit (read-only).
        audit_repo.write_audit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_missing_returns_404(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        with pytest.raises(HTTPException) as exc:
            await admin_tenants.admin_get_tenant(uuid4(), req)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 4. LIST /admin/tenants
# ---------------------------------------------------------------------------


class TestList:
    @pytest.mark.asyncio
    async def test_list_returns_items_total_and_pagination(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        for n in ("Alpha", "Bravo", "Charlie"):
            rid = uuid4()
            fake_repo.rows[rid] = _FakeTenantRepository._make_row(
                record_tenant_id=rid, name=n, slug=n.lower(),
            )
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        resp = await admin_tenants.admin_list_tenants(
            req, limit=2, offset=0, search=None,
        )
        data = resp["data"]
        assert len(data["items"]) == 2
        assert data["total"] == 3
        assert data["limit"] == 2
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_list_search_filters_by_name(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        for n in ("Alpha", "Bravo", "Alphabet"):
            rid = uuid4()
            fake_repo.rows[rid] = _FakeTenantRepository._make_row(
                record_tenant_id=rid, name=n, slug=n.lower(),
            )
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        resp = await admin_tenants.admin_list_tenants(
            req, limit=50, offset=0, search="alpha",
        )
        names = sorted(r["name"] for r in resp["data"]["items"])
        assert names == ["Alpha", "Alphabet"]
        assert resp["data"]["total"] == 2


# ---------------------------------------------------------------------------
# 5. PATCH /admin/tenants/{id}
# ---------------------------------------------------------------------------


class TestPatch:
    @pytest.mark.asyncio
    async def test_patch_updates_fields_and_invalidates_cache(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        rid = uuid4()
        fake_repo.rows[rid] = _FakeTenantRepository._make_row(
            record_tenant_id=rid, name="Acme", slug="acme",
        )
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantPatchRequest(
            name="Acme Renamed",
            config={"region": "us-east-1"},
            rate_limit_per_min=300,
        )
        resp = await admin_tenants.admin_update_tenant(rid, body, req)
        assert resp["data"]["name"] == "Acme Renamed"
        assert resp["data"]["rate_limit_per_min"] == 300
        # Slug must be pinned across config replacement.
        assert resp["data"]["slug"] == "acme"
        # Cache busted exactly once.
        cache.invalidate.assert_awaited_once_with(rid)

    @pytest.mark.asyncio
    async def test_patch_emits_audit_with_diff(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        rid = uuid4()
        fake_repo.rows[rid] = _FakeTenantRepository._make_row(
            record_tenant_id=rid, name="Acme", slug="acme",
        )
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantPatchRequest(monthly_token_cap=2_000_000)
        await admin_tenants.admin_update_tenant(rid, body, req)
        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.action == "admin_tenant_update"
        assert entry.before is not None
        assert entry.after is not None
        assert entry.before["monthly_token_cap"] is None
        assert entry.after["monthly_token_cap"] == 2_000_000
        # Volatile timestamp fields stripped from audit diff.
        assert "updated_at" not in entry.after

    @pytest.mark.asyncio
    async def test_patch_missing_tenant_returns_404(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        body = TenantPatchRequest(name="Renamed")
        with pytest.raises(HTTPException) as exc:
            await admin_tenants.admin_update_tenant(uuid4(), body, req)
        assert exc.value.status_code == 404
        # No audit + no cache invalidate when the row doesn't exist.
        audit_repo.write_audit.assert_not_awaited()
        cache.invalidate.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. DELETE /admin/tenants/{id}
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_soft_deletes_when_no_bots(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        rid = uuid4()
        fake_repo.rows[rid] = _FakeTenantRepository._make_row(
            record_tenant_id=rid, name="Acme", slug="acme",
        )
        fake_repo.active_bot_count = 0
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        # Returns None (status_code=204).
        result = await admin_tenants.admin_delete_tenant(rid, req)
        assert result is None
        # Audit + cache.
        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.action == "admin_tenant_delete"
        assert entry.before is not None
        assert entry.after is None
        cache.invalidate.assert_awaited_once_with(rid)

    @pytest.mark.asyncio
    async def test_delete_rejects_409_when_active_bots(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        rid = uuid4()
        fake_repo.rows[rid] = _FakeTenantRepository._make_row(
            record_tenant_id=rid, name="Acme", slug="acme",
        )
        fake_repo.active_bot_count = 3
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        with pytest.raises(HTTPException) as exc:
            await admin_tenants.admin_delete_tenant(rid, req)
        assert exc.value.status_code == 409
        assert "3" in str(exc.value.detail)
        # No audit + no cache invalidate when the delete failed.
        audit_repo.write_audit.assert_not_awaited()
        cache.invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_missing_tenant_returns_404(
        self, fake_repo: _FakeTenantRepository,
        audit_repo: MagicMock, cache: MagicMock,
    ) -> None:
        req = _build_request(
            role="super_admin", record_tenant_id=uuid4(),
            repo=fake_repo, audit_repo=audit_repo, cache=cache,
        )
        with pytest.raises(HTTPException) as exc:
            await admin_tenants.admin_delete_tenant(uuid4(), req)
        assert exc.value.status_code == 404
        audit_repo.write_audit.assert_not_awaited()
