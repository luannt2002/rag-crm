"""Unit tests for ``cost_cap_alerter.evaluate_tenants`` — mock-only.

Five gates exercised against a fake AsyncSession + ``structlog.testing
.capture_logs``:

  a. usage 0 → no event, no log line
  b. usage 79% → no event (below warn ratio)
  c. usage 80-99% → ``cost_cap_warning`` event + warning log call
  d. usage 100%+ → ``cost_cap_exceeded`` event + error log call
  e. ``quota_monthly_tokens`` IS NULL → tenant skipped entirely

The session is faked rather than mocked piecemeal: a single ``execute``
return value carrying ``Row``-like ``SimpleNamespace`` rows is enough
because the service only consumes the row's attributes, not the SQL
itself. This keeps the unit test pure (zero DB driver, zero engine).

All assertions are STRONG — exact tenant id, exact severity, exact
ratio bounds, exact log_level — never the truthy-only weak forms.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import structlog
from structlog.testing import capture_logs

from ragbot.application.services.cost_cap_alerter import (
    COST_CAP_EXCEEDED_EVENT,
    COST_CAP_WARNING_EVENT,
    CostCapEvent,
    evaluate_tenants,
)
from ragbot.shared.constants import (
    DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS,
    DEFAULT_COST_CAP_WARN_RATIO,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #
def _make_row(
    *,
    tenant_id: UUID,
    tenant_name: str,
    quota: int | None,
    used: int,
) -> SimpleNamespace:
    """Mimic the Row.* attribute access ``evaluate_tenants`` performs.

    The service reads ``row.tenant_id``, ``row.tenant_name``, ``row.quota``
    and ``row.used`` — a ``SimpleNamespace`` matches that surface exactly.
    """
    return SimpleNamespace(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        quota=quota,
        used=used,
    )


def _make_session(rows: list[SimpleNamespace]) -> AsyncMock:
    """Fake AsyncSession: ``await session.execute(stmt)`` → result.all() = rows.

    The service performs ``(await session.execute(stmt)).all()`` so the
    awaited result must be a sync object whose ``.all()`` returns the
    pre-canned row list.
    """
    result = SimpleNamespace(all=lambda: rows)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


# --------------------------------------------------------------------------- #
# Cases (a) usage 0 → no event                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_zero_usage_yields_no_event() -> None:
    tid = uuid4()
    rows = [_make_row(tenant_id=tid, tenant_name="t-zero", quota=10_000, used=0)]
    session = _make_session(rows)
    logger = structlog.get_logger("test.cost_cap.zero")

    with capture_logs() as caps:
        events = await evaluate_tenants(session=session, logger=logger)

    assert events == []
    assert caps == []
    session.execute.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Case (b) usage 79% → below warn → no event                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_usage_below_warn_yields_no_event() -> None:
    tid = uuid4()
    rows = [_make_row(tenant_id=tid, tenant_name="t-79", quota=10_000, used=7_900)]
    session = _make_session(rows)
    logger = structlog.get_logger("test.cost_cap.below")

    with capture_logs() as caps:
        events = await evaluate_tenants(session=session, logger=logger)

    assert events == []
    # Below warn → no log entries even at ratio 0.79.
    assert caps == []


# --------------------------------------------------------------------------- #
# Case (c) usage 80-99% → cost_cap_warning event                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_usage_in_warn_band_yields_warning_event() -> None:
    tid = uuid4()
    quota = 10_000
    used = 8_500  # ratio 0.85 — between warn (0.8) and exceed (1.0).
    rows = [_make_row(tenant_id=tid, tenant_name="t-85", quota=quota, used=used)]
    session = _make_session(rows)
    logger = structlog.get_logger("test.cost_cap.warn")

    with capture_logs() as caps:
        events = await evaluate_tenants(session=session, logger=logger)

    assert len(events) == 1
    evt: CostCapEvent = events[0]
    assert evt.severity == COST_CAP_WARNING_EVENT
    assert evt.record_tenant_id == tid
    assert evt.tenant_name == "t-85"
    assert evt.used_tokens == used
    assert evt.quota_tokens == quota
    assert evt.ratio == pytest.approx(0.85)
    assert evt.window_days == DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS

    # Exactly one warning log line with the right payload + level.
    assert len(caps) == 1
    log_entry = caps[0]
    assert log_entry["event"] == COST_CAP_WARNING_EVENT
    assert log_entry["log_level"] == "warning"
    assert log_entry["record_tenant_id"] == str(tid)
    assert log_entry["used_tokens"] == used
    assert log_entry["quota_tokens"] == quota
    assert log_entry["ratio"] == pytest.approx(0.85)
    assert log_entry["window_days"] == DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS


# --------------------------------------------------------------------------- #
# Case (d) usage 100%+ → cost_cap_exceeded event                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_usage_at_or_over_exceed_ratio_yields_exceeded_event() -> None:
    tid = uuid4()
    quota = 5_000
    used = 6_250  # ratio 1.25 — above exceed (1.0).
    rows = [_make_row(tenant_id=tid, tenant_name="t-125", quota=quota, used=used)]
    session = _make_session(rows)
    logger = structlog.get_logger("test.cost_cap.exceed")

    with capture_logs() as caps:
        events = await evaluate_tenants(session=session, logger=logger)

    assert len(events) == 1
    evt = events[0]
    assert evt.severity == COST_CAP_EXCEEDED_EVENT
    assert evt.record_tenant_id == tid
    assert evt.tenant_name == "t-125"
    assert evt.used_tokens == used
    assert evt.quota_tokens == quota
    assert evt.ratio == pytest.approx(1.25)

    # Single error-level entry — louder severity than warn band.
    assert len(caps) == 1
    log_entry = caps[0]
    assert log_entry["event"] == COST_CAP_EXCEEDED_EVENT
    assert log_entry["log_level"] == "error"
    assert log_entry["record_tenant_id"] == str(tid)
    assert log_entry["used_tokens"] == used
    assert log_entry["quota_tokens"] == quota
    assert log_entry["ratio"] == pytest.approx(1.25)


# --------------------------------------------------------------------------- #
# Case (e) NULL quota → skipped, no event                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_null_quota_tenant_is_skipped() -> None:
    # Two tenants — one with NULL quota (must skip), one with normal warn.
    skip_tid = uuid4()
    keep_tid = uuid4()
    rows = [
        _make_row(tenant_id=skip_tid, tenant_name="t-null", quota=None, used=999_999),
        _make_row(tenant_id=keep_tid, tenant_name="t-warn", quota=10_000, used=8_500),
    ]
    session = _make_session(rows)
    logger = structlog.get_logger("test.cost_cap.null")

    with capture_logs() as caps:
        events = await evaluate_tenants(session=session, logger=logger)

    # Only the warn-band tenant survives.
    assert len(events) == 1
    assert events[0].record_tenant_id == keep_tid
    assert events[0].severity == COST_CAP_WARNING_EVENT

    # And exactly one log line — for the warn-band tenant only.
    assert len(caps) == 1
    assert caps[0]["record_tenant_id"] == str(keep_tid)
    assert caps[0]["event"] == COST_CAP_WARNING_EVENT


# --------------------------------------------------------------------------- #
# Bonus: input-validation guards (cheap to test, prevents silent misuse)      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_invalid_since_days_raises_value_error() -> None:
    session = _make_session([])
    logger = structlog.get_logger("test.cost_cap.invalid_days")
    with pytest.raises(ValueError, match="since_days"):
        await evaluate_tenants(session=session, logger=logger, since_days=0)


@pytest.mark.asyncio
async def test_exceed_below_warn_raises_value_error() -> None:
    session = _make_session([])
    logger = structlog.get_logger("test.cost_cap.invalid_ratio")
    with pytest.raises(ValueError, match="exceed_ratio"):
        await evaluate_tenants(
            session=session,
            logger=logger,
            warn_ratio=DEFAULT_COST_CAP_WARN_RATIO,
            exceed_ratio=DEFAULT_COST_CAP_WARN_RATIO / 2.0,
        )
