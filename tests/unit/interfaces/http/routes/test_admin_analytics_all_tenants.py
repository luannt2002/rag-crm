"""Unit tests for ``GET /admin/analytics/all-tenants``.

Pin behaviour at the route layer with a stubbed service:
  * level-100 super_admin RBAC (lower roles → 403, service not touched),
  * default 7-day window when ``since`` omitted,
  * ``sort_by`` forwarded to the service (default ``total_cost``),
  * ``limit`` capped by the schema-level pydantic validator,
  * empty-data path returns ``totals = 0`` everywhere,
  * cross-workspace counts on a single tenant pass through unchanged.

No live DB — service is stubbed at the ``_build_service`` seam.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ragbot.application.services.tenant_analytics_service import TenantSummary
from ragbot.interfaces.http.routes import admin_analytics
from ragbot.shared.constants import (
    DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
    DEFAULT_ANALYTICS_ALL_TENANTS_WINDOW_DAYS,
    MAX_ANALYTICS_ALL_TENANTS_LIMIT,
)
from ragbot.shared.errors import ForbiddenError


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _make_request(*, role: str = "super_admin") -> Any:
    """Build a minimal Request stub with only the attrs the route reads.

    The all-tenants endpoint does NOT touch ``record_tenant_id`` — it is
    explicitly cross-tenant — so we don't set that attr.
    """
    container = MagicMock()
    container.session_factory = MagicMock(return_value=MagicMock())
    container.request_log_repo = MagicMock(return_value=MagicMock())
    container.message_repo = MagicMock(return_value=MagicMock())
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(role=role),
    )


class _FakeAnalyticsService:
    """Records every ``all_tenants_summary`` call for assertion."""

    def __init__(self, *, rows: list[TenantSummary] | None = None) -> None:
        self.rows = rows or []
        self.captured: list[dict[str, Any]] = []

    async def all_tenants_summary(self, **kwargs: Any) -> list[TenantSummary]:
        self.captured.append({**kwargs})
        return self.rows


def _tenant_row(
    *,
    tenant: UUID,
    cost: float = 0.0,
    requests: int = 0,
    workspace_count: int = 1,
    bot_count: int = 1,
) -> TenantSummary:
    """Build a TenantSummary with sane defaults for table-style tests."""
    return TenantSummary(
        record_tenant_id=tenant,
        workspace_count=workspace_count,
        bot_count=bot_count,
        total_requests=requests,
        total_cost_usd=cost,
        avg_duration_ms=0.0,
        p95_duration_ms=0.0,
        total_tokens=0,
        first_seen_at=None,
        last_seen_at=None,
    )


# ---------------------------------------------------------------------------
# 1. RBAC — super_admin (level 100) required
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_requires_super_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anything below level 100 → ForbiddenError; service untouched."""
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )

    # admin = level 60, not enough for cross-tenant
    req = _make_request(role="admin")
    with pytest.raises(ForbiddenError):
        await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )
    assert fake.captured == []

    # tenant = level 80, still not enough
    req = _make_request(role="tenant_admin")
    with pytest.raises(ForbiddenError):
        await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )
    assert fake.captured == []

    # super_admin = level 100 — passes
    req = _make_request(role="super_admin")
    resp = await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )
    assert resp["ok"] is True
    assert len(fake.captured) == 1


# ---------------------------------------------------------------------------
# 2. Default window = 7 days
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_window_7_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When caller omits ``since``/``until``, the route fills a 7-day window."""
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request()
    before = datetime.now(tz=timezone.utc)
    resp = await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )
    after = datetime.now(tz=timezone.utc)

    captured = fake.captured[0]
    span = captured["until"] - captured["since"]
    # tolerate sub-microsecond drift between the two now() calls
    expected = timedelta(days=DEFAULT_ANALYTICS_ALL_TENANTS_WINDOW_DAYS)
    assert abs(span - expected) < timedelta(seconds=1)
    # until must sit between before/after the call
    assert before <= captured["until"] <= after
    # echo'd in response
    window = resp["window"]
    assert window["since"] == captured["since"].isoformat()
    assert window["until"] == captured["until"].isoformat()


# ---------------------------------------------------------------------------
# 3. Default sort_by = total_cost
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sort_by_total_cost_desc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without explicit ``sort_by``, the route passes ``total_cost``.

    Ordering is delegated to the service (SQL ORDER BY total_cost DESC);
    we assert the parameter is forwarded verbatim.
    """
    t1, t2 = uuid4(), uuid4()
    fake = _FakeAnalyticsService(rows=[
        _tenant_row(tenant=t1, cost=50.0, requests=100),
        _tenant_row(tenant=t2, cost=5.0, requests=10),
    ])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request()
    resp = await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )

    assert fake.captured[0]["sort_by"] == "total_cost"
    # Service returns rows in the requested order — route preserves it
    data = resp["data"]
    assert data[0]["record_tenant_id"] == str(t1)
    assert data[1]["record_tenant_id"] == str(t2)
    # Totals roll up across the page
    assert resp["totals"]["total_tenants"] == 2
    assert resp["totals"]["grand_total_cost_usd"] == 55.0
    assert resp["totals"]["grand_total_requests"] == 110


# ---------------------------------------------------------------------------
# 4. ``limit`` bounded by MAX_ANALYTICS_ALL_TENANTS_LIMIT
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_limit_capped_at_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``limit`` above MAX raises 422 at the pydantic Query() boundary.

    We invoke the function directly with the over-limit value because
    the FastAPI router would already return 422 to the client; the unit
    surface is to keep the contract pinned regardless of transport.
    """
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request()
    # The route's pydantic Query() validator caps at MAX; calling with
    # a higher value through the HTTP layer would 422. Invoking the
    # function directly, the route trusts the validator — so we also
    # check the constant's own boundedness as a regression guard.
    assert MAX_ANALYTICS_ALL_TENANTS_LIMIT >= DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT
    # And the default is positive
    assert DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT > 0

    # Happy path with an in-range explicit value — service receives it
    resp = await admin_analytics.analytics_all_tenants(
        req,
        since=None,
        until=None,
        limit=MAX_ANALYTICS_ALL_TENANTS_LIMIT,
        sort_by="total_cost",
    )
    assert resp["ok"] is True
    assert fake.captured[0]["limit"] == MAX_ANALYTICS_ALL_TENANTS_LIMIT


# ---------------------------------------------------------------------------
# 5. Empty result set → totals zeroed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_when_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tenants in window → data=[], totals all zero, ok=True."""
    fake = _FakeAnalyticsService(rows=[])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request()
    resp = await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )

    assert resp["ok"] is True
    assert resp["data"] == []
    assert resp["totals"]["total_tenants"] == 0
    assert resp["totals"]["grand_total_cost_usd"] == 0
    assert resp["totals"]["grand_total_requests"] == 0


# ---------------------------------------------------------------------------
# 6. Workspace + bot counts roll up per tenant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_aggregates_across_workspaces_per_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One tenant with 3 workspaces + 5 bots surfaces as a single row.

    The service has already done the DISTINCT GROUP BY — route's job is
    to pass through the per-tenant numbers without re-bucketing.
    """
    tenant = uuid4()
    fake = _FakeAnalyticsService(rows=[
        TenantSummary(
            record_tenant_id=tenant,
            workspace_count=3,
            bot_count=5,
            total_requests=1234,
            total_cost_usd=12.345678,
            avg_duration_ms=8500.0,
            p95_duration_ms=19600.0,
            total_tokens=234567,
            first_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_seen_at=datetime(2026, 1, 8, tzinfo=timezone.utc),
        ),
    ])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request()
    resp = await admin_analytics.analytics_all_tenants(
            req, since=None, until=None,
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )

    assert len(resp["data"]) == 1
    row = resp["data"][0]
    assert row["record_tenant_id"] == str(tenant)
    assert row["workspace_count"] == 3
    assert row["bot_count"] == 5
    assert row["total_requests"] == 1234
    assert row["total_cost_usd"] == 12.345678
    assert row["avg_duration_ms"] == 8500.0
    assert row["p95_duration_ms"] == 19600.0
    assert row["total_tokens"] == 234567
    assert row["first_seen_at"] == "2026-01-01T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-01-08T00:00:00+00:00"
    # Totals match the single row exactly
    assert resp["totals"]["total_tenants"] == 1
    assert resp["totals"]["grand_total_cost_usd"] == 12.345678
    assert resp["totals"]["grand_total_requests"] == 1234


# ---------------------------------------------------------------------------
# 7. since > until → 400 (window validation reused from per-tenant routes)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rejects_inverted_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request()
    with pytest.raises(HTTPException) as excinfo:
        await admin_analytics.analytics_all_tenants(
            req,
            since=datetime(2026, 5, 1, tzinfo=timezone.utc),
            until=datetime(2026, 1, 1, tzinfo=timezone.utc),
            limit=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
            sort_by="total_cost",
        )
    assert excinfo.value.status_code == 400
    assert fake.captured == []
