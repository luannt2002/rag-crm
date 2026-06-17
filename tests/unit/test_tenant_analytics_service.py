"""Unit tests for TenantAnalyticsService — multi-tenant control plane (S5).

Pins the contract that:
  * every method REQUIRES record_tenant_id (None → TenantIsolationViolation),
  * the WHERE filter binds the tenant UUID we passed (no cross-tenant leak),
  * pass-rate / cost / latency aggregations roll up correctly,
  * drift severity classification matches CLAUDE.md threshold constants.

Uses a fake session that captures the compiled SQL clauses + serves
canned rows. NO live DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.tenant_analytics_service import (
    DEFAULT_DRIFT_MAJOR_COST_DELTA_PCT,
    DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP,
    DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP,
    CostStats,
    DriftSignal,
    LatencyStats,
    PassRateStats,
    TenantAnalyticsService,
    _classify_drift,
)
from ragbot.shared.errors import TenantIsolationViolation


# ---------------------------------------------------------------------------
# Test plumbing — fake async session that records statements
# ---------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def one(self) -> Any:
        return self._rows[0]


class _FakeSession:
    """Minimal async-context session that returns canned results.

    ``rows_queue`` is a list of row-lists; each ``execute`` call pops the
    next list and returns it wrapped in :class:`_FakeResult`. ``executed``
    records every statement so tests can introspect the WHERE clause.
    """

    def __init__(self, rows_queue: list[list[Any]]) -> None:
        self._rows_queue = list(rows_queue)
        self.executed: list[Any] = []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed.append(stmt)
        if not self._rows_queue:
            return _FakeResult([])
        return _FakeResult(self._rows_queue.pop(0))


def _make_service(rows_queue: list[list[Any]]) -> tuple[TenantAnalyticsService, _FakeSession]:
    session = _FakeSession(rows_queue)

    def _factory() -> _FakeSession:
        return session

    svc = TenantAnalyticsService(
        session_factory=_factory,  # type: ignore[arg-type]
        request_log_repo=MagicMock(),
        message_repo=MagicMock(),
    )
    return svc, session


def _row(**kwargs: Any) -> Any:
    """Build a row-like object usable as both attribute and label access."""
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# 1. Tenant isolation: None → boundary error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_per_bot_tenant_scoped() -> None:
    """None tenant raises before any session call (defence in depth)."""
    svc, session = _make_service(rows_queue=[[]])
    with pytest.raises(TenantIsolationViolation):
        await svc.pass_rate_per_bot(
            record_tenant_id=None,  # type: ignore[arg-type]
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
            until=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )
    assert session.executed == [], "session must NOT be touched if tenant is None"


# ---------------------------------------------------------------------------
# 1b. Cross-tenant: WHERE filter binds the tenant we passed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_per_bot_filters_by_tenant_in_sql() -> None:
    tenant_a = uuid4()
    svc, session = _make_service(rows_queue=[[]])
    await svc.pass_rate_per_bot(
        record_tenant_id=tenant_a,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )
    assert len(session.executed) == 1
    # Compile to a string and confirm the tenant UUID appears as a bound
    # literal — we use literal_binds to render parameters inline. This
    # proves the SQL really filters on tenant before reaching the DB.
    compiled = str(
        session.executed[0].compile(compile_kwargs={"literal_binds": True})
    )
    # SQLAlchemy default dialect renders UUIDs without hyphens
    # (e.g. '1ee7561d1b4344e097da5c5027eaebb4'). Either form is fine —
    # what matters is the tenant identifier ended up bound to the
    # WHERE clause.
    assert str(tenant_a) in compiled or str(tenant_a).replace("-", "") in compiled, (
        f"compiled SQL must bind tenant_a={tenant_a}, got: {compiled}"
    )


# ---------------------------------------------------------------------------
# 2. Empty data → zeros, not crash
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_per_bot_returns_empty_dict_for_no_data() -> None:
    tenant = uuid4()
    svc, _session = _make_service(rows_queue=[[]])
    result = await svc.pass_rate_per_bot(
        record_tenant_id=tenant,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )
    assert result == {}


# ---------------------------------------------------------------------------
# 3. Cost aggregation — 100 reqs * 0.001 = 0.1; avg = 0.001
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_per_bot_aggregates_correctly() -> None:
    tenant = uuid4()
    bot_a = uuid4()
    bot_b = uuid4()
    rows = [
        _row(bot=bot_a, total_requests=100, total_cost=0.10),
        _row(bot=bot_b, total_requests=50, total_cost=0.25),
    ]
    svc, _ = _make_service(rows_queue=[rows])
    out = await svc.cost_per_bot(
        record_tenant_id=tenant,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )
    assert isinstance(out[bot_a], CostStats)
    assert out[bot_a].total_requests == 100
    assert out[bot_a].total_cost_usd == pytest.approx(0.10)
    assert out[bot_a].avg_cost_per_turn == pytest.approx(0.001)
    assert out[bot_b].avg_cost_per_turn == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# 4. Latency aggregation — service trusts DB percentile, just normalises
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_latency_per_bot_percentile_correctness() -> None:
    tenant = uuid4()
    bot = uuid4()
    rows = [_row(bot=bot, p50=120, p95=480, p99=900, max_ms=1500)]
    svc, _ = _make_service(rows_queue=[rows])
    out = await svc.latency_per_bot(
        record_tenant_id=tenant,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )
    s = out[bot]
    assert isinstance(s, LatencyStats)
    assert (s.p50_ms, s.p95_ms, s.p99_ms, s.max_ms) == (120.0, 480.0, 900.0, 1500.0)


# ---------------------------------------------------------------------------
# 5. PASS-rate computation — math correct for non-empty
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_rate_per_bot_computes_pct_and_breakdown() -> None:
    tenant = uuid4()
    bot = uuid4()
    # 80 pass / 15 refuse / 5 hallu out of 100 → 80% PASS
    rows = [
        _row(bot=bot, total=100, pass_count=80, refuse_count=15, hallu_count=5),
    ]
    svc, _ = _make_service(rows_queue=[rows])
    out = await svc.pass_rate_per_bot(
        record_tenant_id=tenant,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )
    assert isinstance(out[bot], PassRateStats)
    assert out[bot].pass_rate_pct == pytest.approx(80.0)
    assert out[bot].refuse_count == 15
    assert out[bot].hallu_count == 5


# ---------------------------------------------------------------------------
# 6. Drift — flat data → NONE
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_signal_no_drift_returns_none() -> None:
    tenant = uuid4()
    bot = uuid4()
    # current window
    cur = _row(total=100, pass_count=85, total_cost=0.10, p95=500)
    # baseline window (same numbers)
    base = _row(total=100, pass_count=85, total_cost=0.10, p95=500)
    svc, _ = _make_service(rows_queue=[[cur], [base]])
    sig = await svc.drift_signal(
        record_tenant_id=tenant,
        record_bot_id=bot,
        window_days=7,
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert isinstance(sig, DriftSignal)
    assert sig.drift_severity == "NONE"
    assert sig.pass_rate_delta_pp == pytest.approx(0.0)
    assert sig.cost_delta_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 7. Drift — minor pass-rate drop
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_signal_minor_drift() -> None:
    tenant = uuid4()
    bot = uuid4()
    # baseline 90%, current 85% → 5pp drop. MINOR threshold = 5pp.
    cur = _row(total=100, pass_count=85, total_cost=0.10, p95=500)
    base = _row(total=100, pass_count=90, total_cost=0.10, p95=500)
    svc, _ = _make_service(rows_queue=[[cur], [base]])
    sig = await svc.drift_signal(
        record_tenant_id=tenant,
        record_bot_id=bot,
        window_days=7,
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert sig.drift_severity == "MINOR"
    # baseline 90% - current 85% → delta = -5pp
    assert sig.pass_rate_delta_pp == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# 8. Drift — major drop (any dimension crosses MAJOR threshold)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_signal_major_drift_pass_rate() -> None:
    tenant = uuid4()
    bot = uuid4()
    # 12pp drop → MAJOR
    cur = _row(total=100, pass_count=78, total_cost=0.10, p95=500)
    base = _row(total=100, pass_count=90, total_cost=0.10, p95=500)
    svc, _ = _make_service(rows_queue=[[cur], [base]])
    sig = await svc.drift_signal(
        record_tenant_id=tenant,
        record_bot_id=bot,
        window_days=7,
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert sig.drift_severity == "MAJOR"
    assert sig.pass_rate_delta_pp == pytest.approx(-12.0)


# ---------------------------------------------------------------------------
# 9. Drift — major cost spike (other dimension)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_signal_major_drift_cost_spike() -> None:
    tenant = uuid4()
    bot = uuid4()
    # PASS flat, but cost rose 30% → MAJOR via cost dimension
    cur = _row(total=100, pass_count=85, total_cost=0.13, p95=500)  # avg .0013
    base = _row(total=100, pass_count=85, total_cost=0.10, p95=500)  # avg .0010
    svc, _ = _make_service(rows_queue=[[cur], [base]])
    sig = await svc.drift_signal(
        record_tenant_id=tenant,
        record_bot_id=bot,
        window_days=7,
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert sig.drift_severity == "MAJOR"
    assert sig.cost_delta_pct == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# 10. Drift — window split goes [now-2N, now-N) vs [now-N, now]
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_signal_window_split_correct() -> None:
    """Verify the two queries cover non-overlapping adjacent windows."""
    tenant = uuid4()
    bot = uuid4()
    cur = _row(total=10, pass_count=10, total_cost=0.0, p95=0)
    base = _row(total=10, pass_count=10, total_cost=0.0, p95=0)
    svc, session = _make_service(rows_queue=[[cur], [base]])
    fixed_now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await svc.drift_signal(
        record_tenant_id=tenant,
        record_bot_id=bot,
        window_days=7,
        now=fixed_now,
    )
    # Two SELECTs were issued (current window + baseline window)
    assert len(session.executed) == 2
    # Render both compiled statements; baseline must reference now-14d,
    # current must reference now-7d. Cheapest check: literal_binds shows
    # the actual datetimes.
    cur_sql = str(
        session.executed[0].compile(compile_kwargs={"literal_binds": True})
    )
    base_sql = str(
        session.executed[1].compile(compile_kwargs={"literal_binds": True})
    )
    cur_lower = (fixed_now - timedelta(days=7)).isoformat()
    base_lower = (fixed_now - timedelta(days=14)).isoformat()
    assert cur_lower[:19] in cur_sql or cur_lower[:10] in cur_sql
    assert base_lower[:19] in base_sql or base_lower[:10] in base_sql


# ---------------------------------------------------------------------------
# 11. Pure unit on classifier — boundary thresholds
# ---------------------------------------------------------------------------
def test_classify_drift_thresholds() -> None:
    # 0pp / 0% → NONE
    assert _classify_drift(pass_rate_delta_pp=0.0, cost_delta_pct=0.0) == "NONE"
    # exactly MINOR pass-drop
    assert _classify_drift(
        pass_rate_delta_pp=-DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP,
        cost_delta_pct=0.0,
    ) == "MINOR"
    # exactly MAJOR pass-drop
    assert _classify_drift(
        pass_rate_delta_pp=-DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP,
        cost_delta_pct=0.0,
    ) == "MAJOR"
    # MAJOR cost spike beats MINOR pass drop
    assert _classify_drift(
        pass_rate_delta_pp=-DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP,
        cost_delta_pct=DEFAULT_DRIFT_MAJOR_COST_DELTA_PCT,
    ) == "MAJOR"


# ---------------------------------------------------------------------------
# 12. drift_signal rejects None tenant + bad inputs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drift_signal_rejects_invalid_inputs() -> None:
    svc, _ = _make_service(rows_queue=[])
    tenant = uuid4()
    with pytest.raises(TenantIsolationViolation):
        await svc.drift_signal(
            record_tenant_id=None,  # type: ignore[arg-type]
            record_bot_id=uuid4(),
            window_days=7,
        )
    with pytest.raises(ValueError):
        await svc.drift_signal(
            record_tenant_id=tenant,
            record_bot_id=None,  # type: ignore[arg-type]
            window_days=7,
        )
    with pytest.raises(ValueError):
        await svc.drift_signal(
            record_tenant_id=tenant,
            record_bot_id=uuid4(),
            window_days=0,
        )
