"""Outbox publisher — polls outbox table, publishes to Redis Streams.

Run as: `python -m ragbot.interfaces.workers.outbox_publisher`

Exactly-once delivery (Agent N, 2026-05-16): the legacy loop polled a
batch of pending rows, published each to Redis Streams, then bulk-
``mark_processed`` AFTER the batch. A crash between publish and mark
left rows pending and they were re-published on next start →
duplicate Redis Stream events.

The refactored loop holds a Postgres ``FOR UPDATE SKIP LOCKED`` row
lock for the entire publish + mark_processed window. Two publisher
replicas calling ``poll_one_for_update`` simultaneously each receive a
different row (SKIP LOCKED skips peer-held rows), and only the replica
that successfully commits its lock-tx mutates the row. If a replica
crashes mid-publish, the lock auto-releases on tx death and the row
remains visible to peers — still pending, not lost.
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

import structlog
from redis.exceptions import RedisError

from ragbot.bootstrap import Container
from ragbot.config.logging import setup_logging
from ragbot.config.settings import get_settings
from ragbot.infrastructure.observability.metrics import outbox_published_total
from ragbot.shared.errors import BusError

if TYPE_CHECKING:
    from ragbot.application.ports.bus_port import EventBusPort
    from ragbot.application.ports.outbox_port import OutboxRecord, OutboxRepositoryPort

logger = structlog.get_logger(__name__)


async def run_outbox_loop(
    *,
    repo: OutboxRepositoryPort,
    bus: EventBusPort,
    poll_interval_s: float = 0.2,
    batch_size: int = 100,
    max_retries: int = 5,
) -> None:
    """Vòng lặp chính — poll outbox, publish lên Redis Streams, xử lý retry/DLQ.

    Mỗi vòng outer wake-up sẽ drain tối đa ``batch_size`` rows theo cơ chế
    per-row tx + FOR UPDATE SKIP LOCKED (exactly-once). Khi queue rỗng
    sleeper ``poll_interval_s``.

    @param repo: outbox repository (must expose poll_one_for_update)
    @param bus: event bus để publish
    @param max_retries: số lần retry tối đa trước khi chuyển DLQ
    """
    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _stop)
        except NotImplementedError:  # Windows
            pass

    while not stop_event.is_set():
        try:
            drained = await _drain_batch_per_row(
                repo=repo, bus=bus, batch_size=batch_size, max_retries=max_retries,
            )
            if not drained:
                await asyncio.sleep(poll_interval_s)
        except Exception:  # noqa: BLE001 — top-level outbox publish loop; log + sleep + continue across DB/Redis blips
            logger.exception("outbox_loop_error")
            await asyncio.sleep(1)

    logger.info("outbox_publisher_stopped")


def _bump_metric(label: str) -> None:
    """Best-effort prometheus counter increment.

    Centralised so each call site does NOT need its own
    ``try/except Exception`` block (broad-except count).
    """
    try:
        outbox_published_total.labels(status=label).inc()
    except Exception:  # noqa: BLE001 — prometheus client failure must not break publish loop
        pass


async def _drain_batch_per_row(
    *,
    repo: OutboxRepositoryPort,
    bus: EventBusPort,
    batch_size: int,
    max_retries: int,
) -> int:
    """Drain up to ``batch_size`` rows using per-row tx + FOR UPDATE SKIP LOCKED.

    Each iteration: poll one locked row → publish → mark_processed →
    commit (single atomic tx). On publish error: rollback the lock tx,
    then open a fresh tx to bump retry_count / DLQ the row.

    Returns the count of rows attempted (success + failure). 0 means the
    queue is empty for this replica.
    """
    attempted = 0
    for _ in range(batch_size):
        try:
            cm = repo.poll_one_for_update()
        except AttributeError:
            # Repo without the exactly-once API — fall back to batch mode.
            return await _drain_batch_fallback(
                repo=repo, bus=bus, batch_size=batch_size, max_retries=max_retries,
            )
        async with cm as (session, rec):
            if rec is None:
                return attempted
            attempted += 1
            try:
                entry_id = await _publish_one(bus=bus, rec=rec)
                await repo.mark_processed_in_session(  # type: ignore[attr-defined]
                    session, rec.id, redis_entry_id=entry_id,
                )
                await session.commit()
                _bump_metric("success")
                logger.debug(
                    "outbox_published",
                    id=str(rec.id),
                    subject=rec.subject,
                    redis_entry_id=entry_id,
                )
            except (BusError, RedisError, OSError, asyncio.TimeoutError) as exc:
                # Lock-tx rolls back so the row stays pending visible
                # to peers; a fresh tx bumps retry/DLQ.
                await session.rollback()
                await _record_publish_failure(
                    repo=repo, rec=rec, exc=exc, max_retries=max_retries,
                )
    return attempted


async def _publish_one(*, bus: EventBusPort, rec: OutboxRecord) -> str:
    """Publish one outbox row to the bus (raw bytes when supported).

    Returns the bus-assigned entry id (Redis Stream id when the bus is
    Redis Streams; some test fakes return an empty string). The publisher
    loop threads this id back to ``mark_processed_in_session`` so the
    outbox row carries a join key to the actual stream entry for
    forensic replay.
    """
    from ragbot.domain.events.base import DomainEvent

    class _RawEvent(DomainEvent):
        event_type = rec.subject  # type: ignore[assignment]

    if hasattr(bus, "publish_raw"):
        result = await bus.publish_raw(
            rec.subject, rec.payload,
            headers={**rec.headers, "Msg-Id": str(rec.id)},
            msg_id=str(rec.id),
        )
    else:
        fake_event = _RawEvent(
            occurred_at=rec.created_at,
            record_tenant_id=rec.record_tenant_id,  # type: ignore[arg-type]
            trace_id=rec.trace_id,  # type: ignore[arg-type]
        )
        result = await bus.publish(
            fake_event, headers=rec.headers, msg_id=str(rec.id),
        )
    return result if isinstance(result, str) and result else ""


async def _record_publish_failure(
    *,
    repo: OutboxRepositoryPort,
    rec: OutboxRecord,
    exc: BaseException,
    max_retries: int,
) -> None:
    """Bump retry_count or DLQ the row in a fresh tx (lock already released)."""
    if rec.retry_count >= max_retries:
        await repo.mark_dlq(rec.id, reason=str(exc))
        _bump_metric("dlq")
        logger.error(
            "outbox_dlq",
            id=str(rec.id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
    else:
        await repo.mark_retry(rec.id, error=str(exc))
        _bump_metric("retry")
        logger.warning(
            "outbox_retry",
            id=str(rec.id),
            retry=rec.retry_count + 1,
            error_type=type(exc).__name__,
        )


async def _drain_batch_fallback(
    *,
    repo: OutboxRepositoryPort,
    bus: EventBusPort,
    batch_size: int,
    max_retries: int,
) -> int:
    """Legacy batch poll → publish → mark_processed loop.

    Kept ONLY for repo implementations that do not yet expose
    ``poll_one_for_update`` (mainly fakes in tests). Not used in
    production: lacks exactly-once guarantees.
    """
    records = await repo.poll_unprocessed(limit=batch_size)
    if not records:
        return 0
    processed_ids = []
    for rec in records:
        try:
            _entry_id = await _publish_one(bus=bus, rec=rec)
            processed_ids.append(rec.id)
            _bump_metric("success")
        except (BusError, RedisError, OSError, asyncio.TimeoutError) as exc:
            await _record_publish_failure(
                repo=repo, rec=rec, exc=exc, max_retries=max_retries,
            )
    if processed_ids:
        await repo.mark_processed(processed_ids)
    return len(records)


async def main() -> None:
    """Khởi chạy outbox publisher worker."""
    settings = get_settings()
    setup_logging(level=settings.observability.log_level, json=True)
    container = Container()
    bus = container.bus()
    await bus.ensure_streams()
    repo = container.outbox_repo()
    await run_outbox_loop(repo=repo, bus=bus)


if __name__ == "__main__":
    asyncio.run(main())
