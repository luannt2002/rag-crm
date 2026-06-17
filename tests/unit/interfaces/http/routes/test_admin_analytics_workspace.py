"""Unit tests for ``GET /admin/analytics/workspace-aggregate``.

Pin behaviour at the route layer with a stubbed service:
  * super_admin (level 100) MAY target any tenant via ``record_tenant_id``;
  * tenant_admin (level 80) blocked when ``record_tenant_id`` mismatches JWT;
  * roles below level 60 rejected outright (RBAC ForbiddenError);
  * per-workspace rows pass through unchanged (DISTINCT grouping is the
    service's job — the route does not re-bucket);
  * default 7-day window when ``since`` omitted;
  * ``sort_by`` defaults to ``total_cost`` and is forwarded verbatim;
  * ``p95_duration_ms`` percentile field surfaces in the response payload.

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

from ragbot.application.services.tenant_analytics_service import (
    WorkspaceSummary,
)
from ragbot.interfaces.http.routes import admin_analytics
from ragbot.shared.constants import (
    DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS,
    MAX_ANALYTICS_WORKSPACE_RESULTS,
)
from ragbot.shared.errors import ForbiddenError


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _make_request(
    *,
    role: str = "tenant_admin",
    record_tenant_id: UUID | None = None,
) -> Any:
    """Build a minimal Request stub with role + JWT tenant attrs.

    ``record_tenant_id`` is the JWT-derived tenant lifted onto
    ``request.state`` by :class:`TenantContextMiddleware`. None ↔ the
    JWT carried no tenant claim (e.g. unscoped super-admin token).
    """
    container = MagicMock()
    container.session_factory = MagicMock(return_value=MagicMock())
    container.request_log_repo = MagicMock(return_value=MagicMock())
    container.message_repo = MagicMock(return_value=MagicMock())
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    state_kwargs: dict[str, Any] = {"role": role}
    if record_tenant_id is not None:
        state_kwargs["record_tenant_id"] = record_tenant_id
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(**state_kwargs),
    )


class _FakeAnalyticsService:
    """Records every ``workspace_aggregate`` call for assertion."""

    def __init__(self, *, rows: list[WorkspaceSummary] | None = None) -> None:
        self.rows = rows or []
        self.captured: list[dict[str, Any]] = []

    async def workspace_aggregate(self, **kwargs: Any) -> list[WorkspaceSummary]:
        self.captured.append({**kwargs})
        return self.rows


def _ws_row(
    *,
    tenant: UUID,
    workspace: str,
    cost: float = 0.0,
    requests: int = 0,
    bot_count: int = 1,
    avg_ms: float = 0.0,
    p95_ms: float = 0.0,
    total_tokens: int = 0,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
) -> WorkspaceSummary:
    """Build a WorkspaceSummary with sane defaults for table-style tests."""
    return WorkspaceSummary(
        record_tenant_id=tenant,
        workspace_id=workspace,
        bot_count=bot_count,
        total_requests=requests,
        total_cost_usd=cost,
        avg_duration_ms=avg_ms,
        p95_duration_ms=p95_ms,
        total_tokens=total_tokens,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
    )


# ---------------------------------------------------------------------------
# 1. super_admin can query any tenant via record_tenant_id query param
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_super_admin_can_query_any_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """super_admin passes ``record_tenant_id`` for a tenant that is NOT
    their own JWT tenant — service receives the param value, not the JWT.
    """
    jwt_tenant = uuid4()
    target_tenant = uuid4()
    fake = _FakeAnalyticsService(rows=[
        _ws_row(tenant=target_tenant, workspace="default", requests=10, cost=1.5),
    ])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )

    req = _make_request(role="super_admin", record_tenant_id=jwt_tenant)
    resp = await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=target_tenant,
        since=None,
        until=None,
        sort_by="total_cost",
    )

    assert resp["ok"] is True
    # Service saw the cross-tenant target, not the caller's JWT tenant
    assert fake.captured[0]["record_tenant_id"] == target_tenant
    assert resp["data"][0]["record_tenant_id"] == str(target_tenant)
    assert resp["data"][0]["workspace_id"] == "default"


# ---------------------------------------------------------------------------
# 2. tenant_admin blocked when record_tenant_id mismatches JWT
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_admin_blocked_cross_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-super_admin probing another tenant → 403, service untouched."""
    jwt_tenant = uuid4()
    other_tenant = uuid4()
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )

    req = _make_request(role="tenant_admin", record_tenant_id=jwt_tenant)
    with pytest.raises(HTTPException) as excinfo:
        await admin_analytics.analytics_workspace_aggregate(
            req,
            record_tenant_id=other_tenant,
            since=None,
            until=None,
            sort_by="total_cost",
        )
    assert excinfo.value.status_code == 403
    assert fake.captured == []

    # admin (level 60) — also blocked from cross-tenant probe
    req2 = _make_request(role="admin", record_tenant_id=jwt_tenant)
    with pytest.raises(HTTPException) as excinfo2:
        await admin_analytics.analytics_workspace_aggregate(
            req2,
            record_tenant_id=other_tenant,
            since=None,
            until=None,
            sort_by="total_cost",
        )
    assert excinfo2.value.status_code == 403
    assert fake.captured == []


# ---------------------------------------------------------------------------
# 3. Below level 60 rejected outright (RBAC ForbiddenError)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_below_60_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roles below admin level (60) hit ForbiddenError before the
    tenant-scope resolver runs. Service must never be called.
    """
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )

    common_kw: dict[str, Any] = {
        "record_tenant_id": None, "since": None, "until": None,
        "sort_by": "total_cost",
    }

    # operator = level 40 — rejected
    req = _make_request(role="operator", record_tenant_id=uuid4())
    with pytest.raises(ForbiddenError):
        await admin_analytics.analytics_workspace_aggregate(req, **common_kw)
    assert fake.captured == []

    # user = level 20 — rejected
    req = _make_request(role="user", record_tenant_id=uuid4())
    with pytest.raises(ForbiddenError):
        await admin_analytics.analytics_workspace_aggregate(req, **common_kw)
    assert fake.captured == []

    # guest = level 0 — rejected
    req = _make_request(role="guest", record_tenant_id=uuid4())
    with pytest.raises(ForbiddenError):
        await admin_analytics.analytics_workspace_aggregate(req, **common_kw)
    assert fake.captured == []


# ---------------------------------------------------------------------------
# 4. Per-workspace rows pass through unchanged
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_aggregates_per_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three workspaces within one tenant render as three distinct rows.

    The service has already done the DISTINCT GROUP BY; route's only job
    is to serialise without re-bucketing or de-duping.
    """
    tenant = uuid4()
    fake = _FakeAnalyticsService(rows=[
        _ws_row(
            tenant=tenant, workspace="ws-a", requests=300, cost=3.0,
            bot_count=2, total_tokens=60000, avg_ms=8200.0, p95_ms=17000.0,
        ),
        _ws_row(
            tenant=tenant, workspace="ws-b", requests=150, cost=1.2,
            bot_count=1, total_tokens=30000, avg_ms=6500.0, p95_ms=12000.0,
        ),
        _ws_row(
            tenant=tenant, workspace="ws-c", requests=117, cost=0.92,
            bot_count=3, total_tokens=33456, avg_ms=7100.0, p95_ms=14500.0,
        ),
    ])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(role="tenant_admin", record_tenant_id=tenant)
    resp = await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=None,
        since=None,
        until=None,
        sort_by="total_cost",
    )

    data = resp["data"]
    assert len(data) == 3
    # Order preserved from service
    assert [r["workspace_id"] for r in data] == ["ws-a", "ws-b", "ws-c"]
    # Distinct bot_count per workspace
    assert [r["bot_count"] for r in data] == [2, 1, 3]
    # Totals carry through unchanged
    assert data[0]["total_requests"] == 300
    assert data[0]["total_cost_usd"] == 3.0
    assert data[0]["total_tokens"] == 60000
    # Each row carries its own tenant id
    assert all(r["record_tenant_id"] == str(tenant) for r in data)


# ---------------------------------------------------------------------------
# 5. Default window = 7 days (DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_window_7_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``since`` / ``until`` omitted, the route fills a 7-day window."""
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    tenant = uuid4()
    req = _make_request(role="tenant_admin", record_tenant_id=tenant)
    before = datetime.now(tz=timezone.utc)
    resp = await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=None,
        since=None,
        until=None,
        sort_by="total_cost",
    )
    after = datetime.now(tz=timezone.utc)

    captured = fake.captured[0]
    span = captured["until"] - captured["since"]
    expected = timedelta(days=DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS)
    # Constant must match the spec
    assert DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS == 7
    assert abs(span - expected) < timedelta(seconds=1)
    assert before <= captured["until"] <= after
    # Limit is clamped to the schema-level max — caller cannot widen
    assert captured["limit"] == MAX_ANALYTICS_WORKSPACE_RESULTS
    # Window echoed in response
    window = resp["window"]
    assert window["since"] == captured["since"].isoformat()
    assert window["until"] == captured["until"].isoformat()


# ---------------------------------------------------------------------------
# 6. Default sort_by = total_cost (and explicit values forwarded verbatim)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sort_by_total_cost_desc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default sort_by is ``total_cost``; explicit values pass through.

    Ordering is delegated to the service (SQL ORDER BY <agg> DESC); the
    unit assertion is purely on argument forwarding.
    """
    tenant = uuid4()
    fake = _FakeAnalyticsService(rows=[
        _ws_row(tenant=tenant, workspace="ws-big", cost=50.0, requests=200),
        _ws_row(tenant=tenant, workspace="ws-small", cost=5.0, requests=20),
    ])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(role="tenant_admin", record_tenant_id=tenant)

    # Default — sort_by="total_cost" explicitly
    resp = await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=None,
        since=None,
        until=None,
        sort_by="total_cost",
    )
    assert fake.captured[0]["sort_by"] == "total_cost"
    # Service returned rows already ordered — route preserves order
    assert resp["data"][0]["workspace_id"] == "ws-big"
    assert resp["data"][1]["workspace_id"] == "ws-small"

    # Explicit sort_by="total_requests" — forwarded verbatim
    fake.captured.clear()
    await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=None,
        since=None,
        until=None,
        sort_by="total_requests",
    )
    assert fake.captured[0]["sort_by"] == "total_requests"

    fake.captured.clear()
    await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=None,
        since=None,
        until=None,
        sort_by="avg_latency",
    )
    assert fake.captured[0]["sort_by"] == "avg_latency"


# ---------------------------------------------------------------------------
# 7. p95 percentile field surfaces in the response payload
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_p95_percentile_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``p95_duration_ms`` (computed by Postgres ``percentile_cont(0.95)``
    in the service layer) round-trips through the route serialiser.
    """
    tenant = uuid4()
    fake = _FakeAnalyticsService(rows=[
        _ws_row(
            tenant=tenant, workspace="default",
            requests=567, cost=5.123456,
            bot_count=3, total_tokens=123456,
            avg_ms=8200.0, p95_ms=17000.0,
            first_seen=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_seen=datetime(2026, 1, 8, tzinfo=timezone.utc),
        ),
    ])
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(role="tenant_admin", record_tenant_id=tenant)
    resp = await admin_analytics.analytics_workspace_aggregate(
        req,
        record_tenant_id=None,
        since=None,
        until=None,
        sort_by="total_cost",
    )

    row = resp["data"][0]
    assert row["p95_duration_ms"] == 17000.0
    assert row["avg_duration_ms"] == 8200.0
    # Other fields also surface — pin the full shape so a missing key
    # on the serialiser is caught here.
    assert row["bot_count"] == 3
    assert row["total_requests"] == 567
    assert row["total_cost_usd"] == 5.123456
    assert row["total_tokens"] == 123456
    assert row["first_seen_at"] == "2026-01-01T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-01-08T00:00:00+00:00"
