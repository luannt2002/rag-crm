"""Unit tests — :mod:`ragbot.interfaces.workers.document_recovery_worker`.

Phase 2 of the upload-flow case study (2026-05-18).

The recovery sweep is a small loop that:

1. Scans for ``state='DRAFT'`` documents older than the stuck threshold
   that do NOT have a fresh outbox row already pending/processed.
2. Inserts a canonical ``document.uploaded.v1`` outbox row per stuck
   doc + writes an ``audit_log`` row for forensic traceability.
3. Increments ``document_recovery_replayed_total{status="success"}``.

These tests cover the contract surface without touching live DB/Redis:

- Stuck doc detected + outbox row inserted.
- Doc with an in-flight outbox row → NOT re-emitted (anti-duplicate).
- Doc state='active' with chunks → skipped; state='active' with ZERO
  chunks past the threshold → stuck (worker crashed between the UPSERT
  and the chunk write — ADR-W1-D4 §2c).
- Doc soft-deleted (``deleted_at`` set) → skipped.
- Per-row DB error → graceful log + counter bump + continue.
- Batch size cap honored.
- Audit row written via ``insert_audit_row``.
- Metrics counter incremented for each success.

The SQL string is matched against a query fixture so we exercise the
real query without a real Postgres backend. The fixture returns the
test-defined row set verbatim; the per-row replay path is monkey-
patched so the test asserts on the captured calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from ragbot.interfaces.workers import document_recovery_worker as drw
from ragbot.interfaces.workers.document_recovery_worker import (
    _build_replay_payload,
    _run_one_sweep,
    run_recovery_loop,
)


# ---------------------------------------------------------------------
# Fixtures — fake session factory + row helpers.
# ---------------------------------------------------------------------


@dataclass
class _StuckRow:
    """Mirror the columns selected by ``_scan_stuck_documents``."""

    id: UUID
    record_tenant_id: UUID
    workspace_id: str
    record_bot_id: UUID
    source_url: str
    document_name: str
    tool_name: str
    mime_type: str


def _make_row(
    *,
    workspace_id: str = "test-workspace",
    source_url: str = "https://example.test/doc",
    document_name: str = "Doc",
    tool_name: str = "Doc",
    mime_type: str = "text/html",
) -> _StuckRow:
    return _StuckRow(
        id=uuid4(),
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        workspace_id=workspace_id,
        source_url=source_url,
        document_name=document_name,
        tool_name=tool_name,
        mime_type=mime_type,
    )


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Async-context-manager-friendly session stub.

    Returns the configured ``rows`` for SELECT scans and records INSERT
    calls. Mirrors the bits of ``AsyncSession`` we actually use.
    """

    def __init__(
        self,
        rows: list[Any] | None = None,
        *,
        raise_on_execute: Exception | None = None,
    ) -> None:
        self._rows = rows or []
        self.executes: list[tuple[Any, dict[str, Any] | None]] = []
        self.committed = False
        self.closed = False
        self._raise = raise_on_execute

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        if self._raise is not None:
            raise self._raise
        self.executes.append((stmt, params))
        return _FakeResult(self._rows)

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        self.closed = True

    async def flush(self) -> None:
        return None

    def add(self, _obj: Any) -> None:
        return None


def _make_session_factory(sessions: list[_FakeSession]) -> Any:
    """Return a callable that yields the queued sessions in order.

    ``session_with_tenant`` calls ``factory()`` to open a session, then
    closes it on exit. The recovery sweep opens one session for the
    scan + one per replay, so the test queue length = 1 + n_rows.
    """
    iterator = iter(sessions)

    def factory() -> _FakeSession:
        try:
            return next(iterator)
        except StopIteration:  # pragma: no cover — guard mis-wired tests
            raise AssertionError("session factory exhausted")

    return factory


# ---------------------------------------------------------------------
# _build_replay_payload — schema correctness.
# ---------------------------------------------------------------------


def test_build_replay_payload_carries_required_keys() -> None:
    """The payload must mirror the producer in test_chat.py — same keys
    the downstream worker reads on the happy path. Missing a key means
    the worker silently parses ``None`` and ingests broken metadata."""
    row = _make_row()
    payload = _build_replay_payload(row, trace_id="trace-1")

    required = {
        "event_id", "event_type", "schema_version", "occurred_at",
        "record_tenant_id", "trace_id", "workspace_id", "job_id",
        "record_bot_id", "document_id", "source_url", "document_name",
        "tool_name", "mime_type", "uploaded_by", "force_reingest",
    }
    assert required.issubset(payload.keys())
    assert payload["event_type"] == "document.uploaded.v1"
    assert payload["schema_version"] == 1
    assert payload["document_id"] == str(row.id)
    assert payload["record_tenant_id"] == str(row.record_tenant_id)
    assert payload["record_bot_id"] == str(row.record_bot_id)
    assert payload["workspace_id"] == row.workspace_id
    assert payload["source_url"] == row.source_url
    # Recovery flag — lets downstream code detect replays for debug
    assert payload.get("recovery_replay") is True


# ---------------------------------------------------------------------
# _run_one_sweep — happy path.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_inserts_outbox_for_each_stuck_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two stuck rows in scan → two replay calls + two success metrics."""
    rows = [_make_row(), _make_row()]
    scan_session = _FakeSession(rows=rows)
    factory = _make_session_factory([scan_session])

    replayed: list[UUID] = []

    async def _fake_replay(*, session_factory: Any, row: Any) -> bool:
        replayed.append(row.id)
        return True

    monkeypatch.setattr(drw, "_replay_one_document", _fake_replay)

    metric_calls: list[str] = []
    monkeypatch.setattr(drw, "_bump_metric", lambda label: metric_calls.append(label))

    success = await _run_one_sweep(
        session_factory=factory,
        stuck_threshold_s=900,
        batch_size=100,
    )

    assert success == 2
    assert replayed == [r.id for r in rows]
    assert metric_calls == ["success", "success"]
    # Scan SQL must reference the canonical anti-duplicate JOIN + filters.
    sql_text = str(scan_session.executes[0][0])
    assert "documents" in sql_text
    assert "LEFT JOIN outbox" in sql_text
    assert "state = 'DRAFT'" in sql_text
    # ADR-W1-D4 §2c — second stuck window: ingest UPSERTed the row to
    # ``active`` synchronously but the worker crashed before any chunk
    # was written. Predicate extension is part of the scan contract.
    assert "d.state = 'active'" in sql_text
    assert "COALESCE(d.chunks_processed, 0) = 0" in sql_text
    assert "NOT EXISTS" in sql_text
    # Anti-dup join must compare against the LAST write, not first insert,
    # so re-ingested (updated_at-bumped) docs are not excluded forever.
    assert "GREATEST(d.created_at, d.updated_at)" in sql_text
    # The suppression MUST be time-bounded by the replay cooldown — otherwise a
    # replay marked 'processed' on publish whose downstream ingest then failed
    # would hide the doc permanently (the xe-3 permanent-DRAFT stuck case).
    assert "make_interval(secs => :replay_cooldown_s)" in sql_text
    assert "deleted_at IS NULL" in sql_text
    assert scan_session.executes[0][1]["stuck_threshold_s"] == 900
    assert scan_session.executes[0][1]["batch_size"] == 100
    assert scan_session.executes[0][1]["replay_cooldown_s"] == 3600


@pytest.mark.asyncio
async def test_sweep_empty_when_no_stuck_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan returns 0 rows → no replay attempt + return 0."""
    scan_session = _FakeSession(rows=[])
    factory = _make_session_factory([scan_session])

    call_count = 0

    async def _fake_replay(**_: Any) -> bool:
        nonlocal call_count
        call_count += 1
        return True

    monkeypatch.setattr(drw, "_replay_one_document", _fake_replay)

    success = await _run_one_sweep(
        session_factory=factory,
        stuck_threshold_s=900,
        batch_size=100,
    )

    assert success == 0
    assert call_count == 0


# ---------------------------------------------------------------------
# Anti-duplicate / state filters — SQL contract.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_sql_excludes_active_and_deleted_and_in_flight() -> None:
    """The canonical scan SQL must filter:

    - ``state = 'DRAFT'`` (NOT active / NOT ingested)
    - ``deleted_at IS NULL`` (NOT soft-deleted)
    - ``o.id IS NULL`` from the LEFT JOIN — anti-duplicate against
      pending/processed outbox rows newer than the doc.

    A regression that drops any of these filters re-emits events for
    healthy documents and DoS's the worker pool. We assert directly on
    the SQL string so a query rewrite cannot drop one silently.
    """
    rows: list[Any] = []
    scan_session = _FakeSession(rows=rows)
    factory = _make_session_factory([scan_session])

    success = await _run_one_sweep(
        session_factory=factory,
        stuck_threshold_s=900,
        batch_size=10,
    )

    assert success == 0
    sql_text = str(scan_session.executes[0][0])
    assert "d.state = 'DRAFT'" in sql_text
    # ADR-W1-D4 §2c — active-0-chunk branch guarded by updated_at age +
    # double-check that no chunk row exists (chunks_processed may be NULL
    # mid-crash while some chunks were already persisted).
    assert "d.state = 'active'" in sql_text
    assert "COALESCE(d.chunks_processed, 0) = 0" in sql_text
    assert "NOT EXISTS" in sql_text
    assert "d.updated_at < now() - make_interval(secs => :stuck_threshold_s)" in sql_text
    assert "GREATEST(d.created_at, d.updated_at)" in sql_text
    assert "d.deleted_at IS NULL" in sql_text
    assert "o.id IS NULL" in sql_text
    # Bound parameters — anti-duplicate join also keys on subject.
    assert scan_session.executes[0][1]["subject"] == "document.uploaded.v1"


@pytest.mark.asyncio
async def test_batch_size_cap_honored() -> None:
    """``batch_size`` flows through to the bound parameter so the SQL
    LIMIT bounds runaway DoS during recovery storms."""
    scan_session = _FakeSession(rows=[])
    factory = _make_session_factory([scan_session])

    await _run_one_sweep(
        session_factory=factory,
        stuck_threshold_s=900,
        batch_size=37,
    )

    sql_text = str(scan_session.executes[0][0])
    assert ":batch_size" in sql_text
    assert scan_session.executes[0][1]["batch_size"] == 37


# ---------------------------------------------------------------------
# Failure isolation — per-row + sweep-level.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_continues_on_per_row_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SQLAlchemyError replaying one row must NOT stop the sweep —
    next rows still replay and the failed row bumps the ``failed``
    metric."""
    rows = [_make_row(), _make_row(), _make_row()]
    scan_session = _FakeSession(rows=rows)
    factory = _make_session_factory([scan_session])

    seen: list[UUID] = []

    async def _fake_replay(*, session_factory: Any, row: Any) -> bool:
        seen.append(row.id)
        if row.id == rows[1].id:
            raise SQLAlchemyError("simulated DB failure on row 2")
        return True

    monkeypatch.setattr(drw, "_replay_one_document", _fake_replay)

    metric_calls: list[str] = []
    monkeypatch.setattr(drw, "_bump_metric", lambda label: metric_calls.append(label))

    success = await _run_one_sweep(
        session_factory=factory,
        stuck_threshold_s=900,
        batch_size=100,
    )

    assert success == 2  # rows 0 and 2 succeed; row 1 failed
    assert seen == [r.id for r in rows]  # all 3 attempted
    assert metric_calls.count("success") == 2
    assert metric_calls.count("failed") == 1


@pytest.mark.asyncio
async def test_sweep_propagates_programmer_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A programmer bug (``AttributeError`` / ``TypeError``) inside
    ``_replay_one_document`` MUST escape the sweep — CLAUDE.md fail-loud
    rule. Only narrow transient classes are isolated."""
    rows = [_make_row()]
    scan_session = _FakeSession(rows=rows)
    factory = _make_session_factory([scan_session])

    async def _fake_replay(**_: Any) -> bool:
        raise AttributeError("typo in code")

    monkeypatch.setattr(drw, "_replay_one_document", _fake_replay)

    with pytest.raises(AttributeError):
        await _run_one_sweep(
            session_factory=factory,
            stuck_threshold_s=900,
            batch_size=10,
        )


# ---------------------------------------------------------------------
# Replay path — outbox INSERT + audit row.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_one_document_inserts_outbox_and_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-row replay opens a tenant-scoped session, executes the outbox
    INSERT, invokes ``insert_audit_row`` with action label
    ``recovery_replay_emitted``, and commits. We patch
    ``session_with_tenant`` to a plain async-context that yields our
    fake session — the real one needs a live RLS connection."""
    from contextlib import asynccontextmanager  # noqa: PLC0415

    row = _make_row()
    fake_session = _FakeSession()

    @asynccontextmanager
    async def _fake_swt(_factory: Any, *, record_tenant_id: Any):  # noqa: ANN202
        assert record_tenant_id == row.record_tenant_id
        yield fake_session

    monkeypatch.setattr(drw, "session_with_tenant", _fake_swt)

    audit_calls: list[dict[str, Any]] = []

    async def _fake_audit(_session: Any, **kwargs: Any) -> Any:
        audit_calls.append(kwargs)
        return MagicMock()

    monkeypatch.setattr(drw, "insert_audit_row", _fake_audit)

    from ragbot.interfaces.workers.document_recovery_worker import (
        _replay_one_document,
    )

    ok = await _replay_one_document(session_factory=MagicMock(), row=row)
    assert ok is True
    assert fake_session.committed is True
    # Outbox INSERT was executed — exactly one execute call on the fake.
    assert len(fake_session.executes) == 1
    sql_text = str(fake_session.executes[0][0])
    assert "INSERT INTO outbox" in sql_text
    params = fake_session.executes[0][1]
    assert params["subject"] == "document.uploaded.v1"
    assert params["tenant_id"] == row.record_tenant_id
    assert params["workspace_id"] == row.workspace_id
    # Audit row written with the canonical action label.
    assert len(audit_calls) == 1
    audit = audit_calls[0]
    assert audit["action"] == "recovery_replay_emitted"
    assert audit["resource_type"] == "document"
    assert audit["resource_id"] == str(row.id)
    assert audit["record_tenant_id"] == row.record_tenant_id


# ---------------------------------------------------------------------
# Loop wrapper — stop_event + cadence.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_recovery_loop_exits_on_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_recovery_loop`` exits cleanly when ``stop_event.set()`` is
    called between sweeps. We make the sweep a no-op so the test is fast."""

    async def _no_op_sweep(**_: Any) -> int:
        return 0

    monkeypatch.setattr(drw, "_run_one_sweep", _no_op_sweep)

    container = MagicMock()
    container.session_factory.return_value = MagicMock()
    stop_event = asyncio.Event()

    async def _trigger_stop() -> None:
        await asyncio.sleep(0.01)
        stop_event.set()

    # interval_s tiny so wait_for returns near-immediately and the
    # second iteration sees stop_event set.
    runner = asyncio.create_task(
        run_recovery_loop(
            container,
            stop_event=stop_event,
            interval_s=1,
            stuck_threshold_s=900,
            batch_size=100,
        ),
    )
    await _trigger_stop()
    await asyncio.wait_for(runner, timeout=2.0)
    assert runner.done() and not runner.cancelled()


@pytest.mark.asyncio
async def test_run_recovery_loop_isolates_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SQLAlchemyError raised by the sweep MUST be swallowed by the
    outer ``while`` so a transient DB blip does not crash the worker
    supervisor. The loop continues to the next sleep + retry."""

    call_count = 0

    async def _flaky_sweep(**_: Any) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise SQLAlchemyError("transient DB blip")
        return 0

    monkeypatch.setattr(drw, "_run_one_sweep", _flaky_sweep)

    container = MagicMock()
    container.session_factory.return_value = MagicMock()
    stop_event = asyncio.Event()

    async def _stop_soon() -> None:
        # Wait for at least 2 sweep attempts.
        for _ in range(50):
            if call_count >= 2:
                break
            await asyncio.sleep(0.01)
        stop_event.set()

    runner = asyncio.create_task(
        run_recovery_loop(
            container,
            stop_event=stop_event,
            interval_s=0,  # immediate next iteration
            stuck_threshold_s=900,
            batch_size=100,
        ),
    )
    await _stop_soon()
    await asyncio.wait_for(runner, timeout=2.0)
    assert call_count >= 2  # loop kept going after the failure
