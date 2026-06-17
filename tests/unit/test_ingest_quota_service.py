"""Unit tests — :class:`IngestQuotaService` (per-tenant daily document quota).

Multi-tenant fairness gate. Covers:

- Quota under cap → increment, return new count.
- Quota at cap → :class:`QuotaExceeded`.
- ``limit=0`` → unlimited (premium tenant).
- Missing quotas row → fail loud (mis-provisioned tenant).
- Daily rollover when reset_at < now → counter zeros, then increment.

Fake AsyncSession pattern follows the existing test idioms in
``test_session_with_tenant_helper.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.ingest_quota_service import IngestQuotaService
from ragbot.shared.errors import QuotaExceeded


# ---------------------------------------------------------------------
# Fake AsyncSession + Result objects covering the 2-step protocol the
# service uses (SELECT FOR UPDATE + UPDATE).
# ---------------------------------------------------------------------


class _FakeResult:
    """Stub for the ``Result`` returned by ``session.execute(SELECT ...)``."""

    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def fetchone(self) -> tuple | None:
        return self._row


class _FakeSession:
    """Records executed statements + serves canned SELECT results.

    The service issues exactly 2 statements per ``check_and_increment``
    call:

    1. ``SELECT ... FOR UPDATE`` — we hand back the canned row tuple.
    2. ``UPDATE quotas SET ...`` — we record the params for assertion.
    """

    def __init__(self, *, select_row: tuple | None) -> None:
        self._select_row = select_row
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        # ``str(text("UPDATE ..."))`` round-trips the raw SQL string so
        # we branch on the first token. Order matters: SELECT FOR UPDATE
        # contains "UPDATE" too, so we test for SELECT first.
        sql_text = str(stmt).strip()
        sql_upper = sql_text.upper()
        self.executed.append((sql_upper, dict(params or {})))
        first_token = sql_upper.split(maxsplit=1)[0] if sql_upper else ""
        if first_token == "SELECT":
            return _FakeResult(self._select_row)
        return _FakeResult(None)  # UPDATE path returns empty result


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------


def _utc_future() -> datetime:
    """Reset anchor strictly in the future (no rollover branch)."""
    return datetime.now(tz=timezone.utc) + timedelta(hours=12)


def _utc_past() -> datetime:
    """Reset anchor strictly in the past (rollover branch)."""
    return datetime.now(tz=timezone.utc) - timedelta(hours=1)


# ---------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------


def _update_params(session: _FakeSession) -> dict[str, Any]:
    """Pick the UPDATE statement's params. SELECT FOR UPDATE contains
    'UPDATE' too — filter on first token == UPDATE."""
    for sql, params in session.executed:
        first = sql.split(maxsplit=1)[0] if sql else ""
        if first == "UPDATE":
            return params
    raise AssertionError("no UPDATE statement recorded")


@pytest.mark.asyncio
async def test_under_cap_increments_and_returns_new_count() -> None:
    svc = IngestQuotaService()
    session = _FakeSession(select_row=(100, 42, _utc_future()))
    tenant = uuid4()

    new_count, limit = await svc.check_and_increment(
        session, record_tenant_id=tenant,
    )
    assert (new_count, limit) == (43, 100)

    update_params = _update_params(session)
    assert update_params["count"] == 43
    assert update_params["tenant_id"] == tenant


@pytest.mark.asyncio
async def test_at_cap_raises_quota_exceeded() -> None:
    """count + 1 > limit → reject. UPDATE must NOT run (counter not bumped)."""
    svc = IngestQuotaService()
    session = _FakeSession(select_row=(100, 100, _utc_future()))

    with pytest.raises(QuotaExceeded):
        await svc.check_and_increment(session, record_tenant_id=uuid4())

    # The SELECT ran; no UPDATE statement was emitted.
    first_tokens = [sql.split(maxsplit=1)[0] for sql, _ in session.executed if sql]
    assert "SELECT" in first_tokens
    assert "UPDATE" not in first_tokens


@pytest.mark.asyncio
async def test_batch_increment_pre_check_against_cap() -> None:
    """``increment_by=N`` allows the batch ingest endpoint to atomically
    pre-check N docs in one round trip — partial accept is not allowed
    (all-or-nothing semantic mirrors Stripe's batch contract)."""
    svc = IngestQuotaService()
    session = _FakeSession(select_row=(100, 95, _utc_future()))

    with pytest.raises(QuotaExceeded):
        await svc.check_and_increment(
            session, record_tenant_id=uuid4(), increment_by=10,
        )

    # Confirmed no partial UPDATE.
    first_tokens = [sql.split(maxsplit=1)[0] for sql, _ in session.executed if sql]
    assert "UPDATE" not in first_tokens


@pytest.mark.asyncio
async def test_unlimited_limit_zero_bypasses_check() -> None:
    """``documents_per_day_limit = 0`` is the premium-tenant override."""
    svc = IngestQuotaService()
    # 1M used, limit 0 → still allowed (unlimited)
    session = _FakeSession(select_row=(0, 1_000_000, _utc_future()))
    tenant = uuid4()

    new_count, limit = await svc.check_and_increment(
        session, record_tenant_id=tenant,
    )
    assert limit == 0
    assert new_count == 1_000_001


@pytest.mark.asyncio
async def test_missing_quota_row_fails_loud() -> None:
    """No ``quotas`` row for tenant → mis-provisioned → loud error."""
    svc = IngestQuotaService()
    session = _FakeSession(select_row=None)

    with pytest.raises(QuotaExceeded) as exc_info:
        await svc.check_and_increment(session, record_tenant_id=uuid4())
    assert "not fully provisioned" in str(exc_info.value)


@pytest.mark.asyncio
async def test_daily_rollover_zeros_counter_then_increments() -> None:
    """reset_at < now → counter rolls over to 0, then ``+ increment_by``."""
    svc = IngestQuotaService()
    session = _FakeSession(select_row=(100, 999, _utc_past()))
    tenant = uuid4()

    new_count, limit = await svc.check_and_increment(
        session, record_tenant_id=tenant,
    )
    # Was 999 yesterday → today resets to 0 → after this call = 1
    assert new_count == 1
    assert limit == 100

    update_params = _update_params(session)
    assert update_params["count"] == 1
    # reset_at advanced to a future timestamp (next midnight)
    assert update_params["reset_at"] > datetime.now(tz=timezone.utc)


@pytest.mark.asyncio
async def test_rollover_then_immediately_at_new_cap() -> None:
    """Edge: rollover happens AND batch is larger than limit → reject."""
    svc = IngestQuotaService()
    session = _FakeSession(select_row=(50, 999, _utc_past()))

    with pytest.raises(QuotaExceeded):
        await svc.check_and_increment(
            session, record_tenant_id=uuid4(), increment_by=51,
        )
