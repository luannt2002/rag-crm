"""Decoupled, non-blocking DB sink for the token ledger.

``emit`` only pushes the entry onto a bounded in-memory queue and returns
immediately — it never touches the DB on the caller's LLM coroutine (CLAUDE.md
Async Rule 7: don't share the LLM-path session; Rule 6: bounded). A single
background drainer batch-INSERTs rows on its OWN session factory. If the queue
is full (burst of ingest enrichment calls) the entry is dropped + counted — an
audit sink must degrade silently and never stall or kill the money-path.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Final

import structlog
from sqlalchemy import text

from ragbot.application.ports.token_ledger_port import TokenLedgerEntry, TokenLedgerPort

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Pure-technical tuning (CLAUDE.md whitelist: queue/batch/flush).
_QUEUE_MAX: Final[int] = 10000
_BATCH_N: Final[int] = 200
_FLUSH_INTERVAL_S: Final[float] = 1.0

_INSERT_SQL = text("""
    INSERT INTO token_ledger (
        mode, action, purpose, provider, model,
        record_tenant_id, record_bot_id, bot_id, workspace_id, channel_type,
        request_id, document_id, trace_id,
        input_tokens, output_tokens, total_tokens, cached_tokens,
        started_at, finished_at, duration_ms,
        input_unit_price, output_unit_price, cached_unit_price, cost_usd,
        status, finish_reason
    ) VALUES (
        :mode, :action, :purpose, :provider, :model,
        :record_tenant_id, :record_bot_id, :bot_id, :workspace_id, :channel_type,
        :request_id, :document_id, :trace_id,
        :input_tokens, :output_tokens, :total_tokens, :cached_tokens,
        :started_at, :finished_at, :duration_ms,
        :input_unit_price, :output_unit_price, :cached_unit_price, :cost_usd,
        :status, :finish_reason
    )
""")


def _entry_params(e: TokenLedgerEntry) -> dict:
    return {
        "mode": e.mode, "action": e.action, "purpose": e.purpose,
        "provider": e.provider, "model": e.model,
        "record_tenant_id": e.record_tenant_id, "record_bot_id": e.record_bot_id,
        "bot_id": e.bot_id, "workspace_id": e.workspace_id, "channel_type": e.channel_type,
        "request_id": e.request_id, "document_id": e.document_id, "trace_id": e.trace_id,
        "input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
        "total_tokens": e.total_tokens, "cached_tokens": e.cached_tokens,
        "started_at": e.started_at, "finished_at": e.finished_at, "duration_ms": e.duration_ms,
        "input_unit_price": e.input_unit_price, "output_unit_price": e.output_unit_price,
        "cached_unit_price": e.cached_unit_price, "cost_usd": e.cost_usd,
        "status": e.status, "finish_reason": e.finish_reason,
    }


class AsyncDBTokenLedger(TokenLedgerPort):
    """Bounded-queue + background-drainer token ledger."""

    def __init__(self, session_factory: "Callable[[], AsyncSession]") -> None:
        self._sf = session_factory
        self._queue: asyncio.Queue[TokenLedgerEntry] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._task: asyncio.Task | None = None
        self._dropped = 0

    def emit(self, entry: TokenLedgerEntry) -> None:
        """Fire-and-forget: enqueue, never block. Drop + count if full.

        Lazily starts the background drainer on the first emit (we are inside an
        async LLM coroutine, so a running loop exists). No app-lifespan wiring
        needed; if there is no running loop (sync test), the entry is dropped.
        """
        if self._task is None or self._task.done():
            try:
                self.start()
            except RuntimeError:
                return
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning("token_ledger_queue_full_dropped", dropped=self._dropped)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _drain_loop(self) -> None:
        while True:
            batch: list[TokenLedgerEntry] = []
            try:
                # Block for the first item, then greedily drain up to _BATCH_N.
                first = await asyncio.wait_for(self._queue.get(), timeout=_FLUSH_INTERVAL_S)
                batch.append(first)
                while len(batch) < _BATCH_N:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            if batch:
                await self._flush(batch)

    async def _flush(self, batch: list[TokenLedgerEntry]) -> None:
        try:
            async with self._sf() as session:
                await session.execute(_INSERT_SQL, [_entry_params(e) for e in batch])
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — aux sink must never kill app
            logger.warning("token_ledger_flush_failed",
                           error=str(exc)[:200], error_type=type(exc).__name__, n=len(batch))
