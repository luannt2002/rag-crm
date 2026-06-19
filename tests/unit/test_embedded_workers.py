"""Unit tests — :mod:`ragbot.interfaces.http.embedded_workers`.

Single-process supervisor (case study 2026-05-16) — runs document
consumer + outbox publisher as background asyncio tasks inside the
FastAPI lifespan so dev / small-tenant operators get the whole ingest
pipeline from one ``systemctl restart ragbot-api``.

These tests cover the lifecycle surfaces:

- ``_supervise``: top-level wrapper isolates exceptions from siblings.
- ``start_embedded_workers``: spawns named asyncio tasks.
- ``stop_embedded_workers``: cancels + drains gracefully.

The actual subscribe loop / outbox poll loop is covered by the existing
``test_redis_streams_recovery`` and ``test_outbox_publish_verify`` suites
— we don't re-test them here.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from ragbot.interfaces.http.embedded_workers import (
    _supervise,
    start_embedded_workers,
    stop_embedded_workers,
)


# ---------------------------------------------------------------------
# _supervise — exception isolation.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_runs_coro_to_completion() -> None:
    """Normal exit → supervisor returns cleanly."""
    observed: list[str] = []

    async def _ok(_container: Any) -> None:
        observed.append("ran")

    await _supervise("test_worker", _ok, MagicMock())
    assert observed == ["ran"]


@pytest.mark.asyncio
async def test_supervise_propagates_cancellation() -> None:
    """``asyncio.CancelledError`` must propagate so lifespan teardown
    knows the worker shut down rather than crashed."""

    async def _hang(_container: Any) -> None:
        await asyncio.Event().wait()  # never set → blocks until cancelled

    task = asyncio.create_task(_supervise("hang_worker", _hang, MagicMock()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_supervise_isolates_runtime_error() -> None:
    """A handler RuntimeError logs + returns cleanly — does NOT propagate
    to the lifespan (which would crash the whole API process)."""

    async def _boom(_container: Any) -> None:
        raise RuntimeError("simulated worker crash")

    # _supervise narrows to (OSError, RuntimeError, RedisError, TimeoutError).
    # RuntimeError is inside the tuple → swallowed + logged.
    await _supervise("crashy_worker", _boom, MagicMock())  # must NOT raise


@pytest.mark.asyncio
async def test_supervise_isolates_os_error() -> None:
    """OSError (covers connection drops, file errors) is in the narrow tuple."""

    async def _io_drop(_container: Any) -> None:
        raise OSError("EPIPE during worker drain")

    await _supervise("io_worker", _io_drop, MagicMock())  # must NOT raise


@pytest.mark.asyncio
async def test_supervise_lets_programmer_bug_surface_loud() -> None:
    """``TypeError`` / ``AttributeError`` (programmer bugs) MUST propagate
    so they show up in dev / CI instead of being silently swallowed.
    CLAUDE.md fail-loud rule.
    """

    async def _typo(_container: Any) -> None:
        # Simulate programmer mistake — attribute lookup on None.
        _x: Any = None
        _x.does_not_exist  # raises AttributeError

    with pytest.raises(AttributeError):
        await _supervise("buggy_worker", _typo, MagicMock())


# ---------------------------------------------------------------------
# start_embedded_workers — spawns 2 named tasks.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_embedded_workers_returns_four_named_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spawns 1 consumer + 1 outbox + 1 recovery + 1 cost-cap task (D11)."""

    async def _stub(_c: Any) -> None:
        await asyncio.sleep(0)

    # Patch the worker coros so we don't touch live Redis / DB.
    from ragbot.interfaces.http import embedded_workers as ew_mod
    monkeypatch.setattr(ew_mod, "run_embedded_document_consumer", _stub)
    monkeypatch.setattr(ew_mod, "run_embedded_outbox_publisher", _stub)
    monkeypatch.setattr(ew_mod, "run_embedded_recovery_worker", _stub)
    monkeypatch.setattr(ew_mod, "run_embedded_cost_cap_alerter", _stub)
    monkeypatch.setattr(ew_mod, "run_embedded_cache_purge", _stub)

    tasks = start_embedded_workers(MagicMock())
    try:
        assert len(tasks) == 5
        names = {t.get_name() for t in tasks}
        assert names == {
            "embedded_document_consumer",
            "embedded_outbox_publisher",
            "embedded_recovery_worker",
            "embedded_cost_cap_alerter",
            "embedded_cache_purge",
        }
    finally:
        await stop_embedded_workers(tasks)


# ---------------------------------------------------------------------
# stop_embedded_workers — cancels + drains.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_embedded_workers_cancels_pending_tasks() -> None:
    """Pending tasks are cancelled cleanly; CancelledError is swallowed
    so the lifespan teardown continues to dispose other resources."""

    async def _forever() -> None:
        await asyncio.Event().wait()

    tasks = [
        asyncio.create_task(_forever(), name="t1"),
        asyncio.create_task(_forever(), name="t2"),
    ]
    await asyncio.sleep(0.01)
    await stop_embedded_workers(tasks)
    assert all(t.done() for t in tasks)
    assert all(t.cancelled() for t in tasks)


@pytest.mark.asyncio
async def test_stop_embedded_workers_handles_already_done_task() -> None:
    """A task that finished BEFORE shutdown must not raise on
    ``await task`` — pattern allows the supervisor to exit early on
    programmer-bug propagation while siblings remain running.
    """

    async def _quick() -> None:
        return None

    tasks = [asyncio.create_task(_quick(), name="quick")]
    await asyncio.sleep(0.05)  # let it complete
    assert tasks[0].done()
    # stop_embedded_workers cancels (no-op on done task) and awaits — no error
    await stop_embedded_workers(tasks)
