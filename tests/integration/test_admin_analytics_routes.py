"""Integration tests for /admin/analytics/* routes (S5 multi-tenant).

End-to-end at the route layer with a stubbed TenantAnalyticsService —
we pin:
  * level-60 RBAC enforcement (lower roles → 403),
  * tenant resolution from JWT (request.state.tenant_id), not body,
  * cross-tenant probe blocked,
  * 401 when tenant context is missing,
  * the per-bot dict serialises UUIDs as strings.

NO live DB — service is replaced by a fake recording calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ragbot.application.services.tenant_analytics_service import (
    CostStats,
    DriftSignal,
    LatencyStats,
    PassRateStats,
)
from ragbot.interfaces.http.routes import admin_analytics


# ---------------------------------------------------------------------------
# Test plumbing
# ---------------------------------------------------------------------------
def _make_request(
    *,
    tenant_uuid: UUID | None,
    role: str = "tenant_admin",
    fake_service: Any | None = None,
) -> Any:
    """Build a minimal Request stub.

    The route's ``_build_service`` reads ``container.session_factory()``,
    ``container.request_log_repo()``, ``container.message_repo()``. We
    monkey-patch ``_build_service`` per-test instead of stubbing all
    three so the test failure mode is obvious.
    """
    container = MagicMock()
    container.session_factory = MagicMock(return_value=MagicMock())
    container.request_log_repo = MagicMock(return_value=MagicMock())
    container.message_repo = MagicMock(return_value=MagicMock())
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    state_kwargs: dict[str, Any] = {"role": role}
    if tenant_uuid is not None:
        state_kwargs["tenant_id"] = tenant_uuid
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(**state_kwargs),
    )


class _FakeAnalyticsService:
    """Records every call so tests can assert tenant scoping."""

    def __init__(self, *, pass_data: dict | None = None,
                 cost_data: dict | None = None,
                 latency_data: dict | None = None,
                 drift: DriftSignal | None = None) -> None:
        self.pass_data = pass_data or {}
        self.cost_data = cost_data or {}
        self.latency_data = latency_data or {}
        self.drift = drift
        self.captured: list[dict[str, Any]] = []

    async def pass_rate_per_bot(self, **kwargs: Any) -> dict:
        self.captured.append({"method": "pass_rate", **kwargs})
        return self.pass_data

    async def cost_per_bot(self, **kwargs: Any) -> dict:
        self.captured.append({"method": "cost", **kwargs})
        return self.cost_data

    async def latency_per_bot(self, **kwargs: Any) -> dict:
        self.captured.append({"method": "latency", **kwargs})
        return self.latency_data

    async def drift_signal(self, **kwargs: Any) -> DriftSignal:
        self.captured.append({"method": "drift", **kwargs})
        assert self.drift is not None
        return self.drift


# ---------------------------------------------------------------------------
# 1. Pass-rate route: forwards JWT tenant + serialises per-bot dict
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_route_returns_per_bot_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = uuid4()
    bot_a = uuid4()
    fake = _FakeAnalyticsService(
        pass_data={
            bot_a: PassRateStats(
                record_bot_id=bot_a, total=10, pass_count=8,
                refuse_count=1, hallu_count=1, pass_rate_pct=80.0,
            ),
        },
    )
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )

    req = _make_request(tenant_uuid=tenant, role="tenant_admin")
    resp = await admin_analytics.analytics_pass_rate(
        req,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert resp["ok"] is True
    data = resp["data"]
    assert str(bot_a) in data
    assert data[str(bot_a)]["pass_rate_pct"] == 80.0
    # Service received the JWT tenant — never the body's
    assert fake.captured[0]["record_tenant_id"] == tenant


# ---------------------------------------------------------------------------
# 2. Pass-rate route blocks under-privileged role
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_route_403_for_low_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    # role="user" → level 20 < 60
    req = _make_request(tenant_uuid=uuid4(), role="user")
    with pytest.raises(Exception) as excinfo:
        await admin_analytics.analytics_pass_rate(req)
    # ForbiddenError is mapped to 403 by the global handler; here we
    # just confirm the call raised and the service was NOT touched.
    assert "permission" in str(excinfo.value).lower() or \
        "forbidden" in str(type(excinfo.value)).lower()
    assert fake.captured == []


# ---------------------------------------------------------------------------
# 3. Pass-rate route 401 when tenant context missing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_route_401_when_no_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(tenant_uuid=None, role="tenant_admin")
    with pytest.raises(HTTPException) as excinfo:
        await admin_analytics.analytics_pass_rate(req)
    assert excinfo.value.status_code == 401
    assert fake.captured == []


# ---------------------------------------------------------------------------
# 4. Cross-tenant probe: tenant A's JWT is what the service sees,
#    not anything from query / path.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_route_uses_jwt_tenant_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_a = uuid4()
    fake = _FakeAnalyticsService(cost_data={})
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(tenant_uuid=tenant_a, role="tenant_admin")
    await admin_analytics.analytics_cost(req, since=None, until=None)
    # Only the JWT tenant should ever reach the service
    assert fake.captured[0]["record_tenant_id"] == tenant_a


# ---------------------------------------------------------------------------
# 5. Latency route returns percentile dict per bot
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_latency_route_serialises_percentiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = uuid4()
    bot = uuid4()
    fake = _FakeAnalyticsService(
        latency_data={
            bot: LatencyStats(
                record_bot_id=bot, p50_ms=100.0, p95_ms=400.0,
                p99_ms=900.0, max_ms=2000.0,
            ),
        },
    )
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(tenant_uuid=tenant, role="tenant_admin")
    resp = await admin_analytics.analytics_latency(req, since=None, until=None)
    data = resp["data"][str(bot)]
    assert data["p50_ms"] == 100.0
    assert data["p95_ms"] == 400.0
    assert data["p99_ms"] == 900.0
    assert data["max_ms"] == 2000.0


# ---------------------------------------------------------------------------
# 6. Drift route returns severity string + deltas
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_route_returns_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = uuid4()
    bot = uuid4()
    fake = _FakeAnalyticsService(
        drift=DriftSignal(
            record_bot_id=bot,
            pass_rate_delta_pp=-12.0,
            cost_delta_pct=8.0,
            p95_delta_ms=200.0,
            drift_severity="MAJOR",
        ),
    )
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(tenant_uuid=tenant, role="tenant_admin")
    resp = await admin_analytics.analytics_drift(req, record_bot_id=bot, window_days=7)
    assert resp["ok"] is True
    data = resp["data"]
    assert data["record_bot_id"] == str(bot)
    assert data["drift_severity"] == "MAJOR"
    assert data["pass_rate_delta_pp"] == -12.0
    assert data["cost_delta_pct"] == 8.0
    # Service received the JWT tenant + the path bot
    assert fake.captured[0]["record_tenant_id"] == tenant
    assert fake.captured[0]["record_bot_id"] == bot


# ---------------------------------------------------------------------------
# 7. Bad window (since > until) → 400
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_route_rejects_inverted_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeAnalyticsService()
    monkeypatch.setattr(
        admin_analytics, "_build_service", lambda _req: fake,
    )
    req = _make_request(tenant_uuid=uuid4(), role="tenant_admin")
    with pytest.raises(HTTPException) as excinfo:
        await admin_analytics.analytics_pass_rate(
            req,
            since=datetime(2026, 5, 1, tzinfo=timezone.utc),
            until=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    assert excinfo.value.status_code == 400
