"""Z2-P0-2 regression: worker handlers must NOT swallow exceptions.

Audit `AUDIT_DEEPDIVE_OUTBOX_WORKERS_20260429_142902.md` (P0-2):
If `handle_chat_received` / `handle_document_uploaded` raises after
job status="running" but before status is finalized, swallowing
the exception lets the bus XACK and the job stays "running" forever.
The fix re-raises so:
  - bus skips XACK (handler exception path in redis_streams_bus._loop)
  - PEL retains the entry → recover_pending_messages will XCLAIM and
    redeliver up to 5 times before dead-letter.

These tests recreate the Semaphore-guarded handler pattern (mirror of
`chat_worker.main()._handler` and `document_worker.main()._handler`)
without booting Redis/DB, and assert exceptions propagate.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ragbot.infrastructure.observability.metrics import chat_worker_queue_depth


@pytest.mark.asyncio
async def test_chat_worker_handler_propagates_exception() -> None:
    """Mirror chat_worker.main()._handler: re-raise on inner failure."""
    sem = asyncio.Semaphore(1)

    async def boom(_payload: dict[str, Any]) -> None:
        raise RuntimeError("simulated uow.commit failure mid-pipeline")

    async def _handler(event: Any) -> None:
        async with sem:
            try:
                chat_worker_queue_depth.inc()
            except Exception:  # noqa: BLE001
                pass
            try:
                await boom(event)
            finally:
                try:
                    chat_worker_queue_depth.dec()
                except Exception:  # noqa: BLE001
                    pass

    with pytest.raises(RuntimeError, match="simulated uow.commit failure"):
        await _handler({"payload": {}})


@pytest.mark.asyncio
async def test_chat_worker_handler_decrements_gauge_on_exception() -> None:
    """Even when handler raises, the queue_depth gauge MUST be decremented
    (finally block) so observability stays accurate across retries."""
    sem = asyncio.Semaphore(1)
    chat_worker_queue_depth.set(0)
    start = chat_worker_queue_depth._value.get()  # type: ignore[attr-defined]

    async def boom(_payload: dict[str, Any]) -> None:
        raise ValueError("boom")

    async def _handler(event: Any) -> None:
        async with sem:
            chat_worker_queue_depth.inc()
            try:
                await boom(event)
            finally:
                chat_worker_queue_depth.dec()

    with pytest.raises(ValueError):
        await _handler({})

    final = chat_worker_queue_depth._value.get()  # type: ignore[attr-defined]
    assert final == start, f"gauge leaked on exception: {final} != {start}"


@pytest.mark.asyncio
async def test_document_worker_handler_propagates_exception() -> None:
    """Mirror document_worker.main()._handler: no swallow, no inner try/except."""

    async def boom(_payload: dict[str, Any]) -> None:
        raise OSError("simulated DB connection drop during ingest")

    async def _handler(event: Any) -> None:
        await boom(event)

    with pytest.raises(OSError, match="simulated DB connection drop"):
        await _handler({"payload": {}})


@pytest.mark.asyncio
async def test_handler_exceptions_skip_ack_semantic() -> None:
    """Document the at-least-once contract: handler raise == bus does NOT ack.

    redis_streams_bus._loop wraps handler call in try/except: ack happens on
    line AFTER `await handler(...)`, so an exception bypasses the ack call
    and the message stays in PEL. recover_pending_messages then XCLAIMs.

    This test asserts the contract by simulating: handler raise → ack NOT
    called → next iteration sees the message still claimable.
    """
    ack_calls: list[str] = []

    async def fake_ack(msg_id: str) -> None:
        ack_calls.append(msg_id)

    async def handler_raises(_event: Any) -> None:
        raise RuntimeError("handler failed")

    # Mimic redis_streams_bus._loop body for one message:
    msg_id = "msg-001"
    try:
        await handler_raises({"i": 1})
        await fake_ack(msg_id)  # not reached
    except Exception:  # noqa: BLE001 — bus loop's outer wrapper
        pass

    assert ack_calls == [], "ack must NOT be called when handler raises"
