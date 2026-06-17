"""Embedded background workers — single-process deployment.

Per case study 2026-05-16 (decision recorded in this commit message):
the dev / small-tenant operator model is **one** systemd unit
(``ragbot-api.service``) running api + document consumer + outbox
publisher inside the same Python process so they share the lib stack
(ZeroEntropy SDK, langgraph, sqlalchemy, redis, structlog, pydantic) and
the host RAM pool. Restart = ``systemctl restart ragbot-api`` — 1
command, no fan-out to 3 services.

The legacy ``ragbot.interfaces.workers.document_worker`` and
``ragbot.interfaces.workers.outbox_publisher`` ``__main__`` entry points
stay untouched: DevOps that want **horizontal scale** (multiple worker
processes, separate K8s deployments, ECS tasks) still ``python -m
ragbot.interfaces.workers.document_worker`` directly. The toggle is
``APP_EMBED_WORKERS_ENABLED`` (default ``true``).

Supervisor pattern
------------------
Each worker runs as an ``asyncio.create_task`` inside the FastAPI
lifespan. We do NOT replicate the legacy ``main()`` setup_logging /
signal handler / asyncio.run wrapper — logging is already configured by
the FastAPI lifespan, and SIGINT / SIGTERM are owned by uvicorn (the
lifespan's ``finally`` block cancels our tasks via ``CancelledError``).

Failure isolation: a worker exception logs ``embedded_worker_crashed``
with ``exc_info=True`` and the task ends. The supervisor here does NOT
auto-restart; an operator-visible API health probe + Prometheus
``document_ingest_total{status="failed"}`` counter surface the regression.
Auto-restart would risk a tight crash loop hiding a config bug; the
explicit ``systemctl restart ragbot-api`` is the recovery path.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from redis.exceptions import RedisError

from ragbot.application.services.cost_cap_alerter import evaluate_tenants
from ragbot.interfaces.workers.document_recovery_worker import run_recovery_loop
from ragbot.interfaces.workers.document_worker import handle_document_uploaded
from ragbot.interfaces.workers.outbox_publisher import run_outbox_loop
from ragbot.shared.constants import (
    DEFAULT_COST_CAP_ALERT_INTERVAL_S,
    SUBJECT_DOCUMENT_UPLOADED,
)

if TYPE_CHECKING:
    from ragbot.bootstrap import Container

logger = structlog.get_logger(__name__)


async def run_embedded_document_consumer(container: "Container") -> None:
    """Subscribe ``document.uploaded.v1`` and dispatch to the ingest pipeline.

    Mirrors the legacy ``document_worker.main()`` subscribe path but
    skips its setup_logging / signal handler / asyncio.run wrapper — the
    FastAPI lifespan owns those. Cancellation propagates from lifespan
    teardown: ``await sub.unsubscribe()`` cancels the underlying
    XREADGROUP loop and the function returns cleanly.
    """
    bus = container.bus()
    await bus.ensure_streams()

    async def _handler(event: Any) -> None:  # noqa: ANN401 — bus delivers opaque event
        await handle_document_uploaded(event.payload, container)

    sub = await bus.subscribe(
        SUBJECT_DOCUMENT_UPLOADED,
        _handler,
        durable_name="document-worker",
        queue_group="documents",
    )
    logger.info("embedded_document_consumer_started")

    try:
        # Idle wait — the subscribe task runs the XREADGROUP loop in the
        # background. We only need to keep this coroutine alive until
        # cancellation arrives from the lifespan teardown.
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("embedded_document_consumer_stopping")
        raise
    finally:
        try:
            await sub.unsubscribe()
        except (OSError, RuntimeError, RedisError, asyncio.TimeoutError):
            # Narrow shutdown drain: only network / runtime classes need
            # isolation; a programmer error here would surface loud at
            # development and we want it visible, not swallowed.
            logger.warning("embedded_document_consumer_unsubscribe_failed", exc_info=True)


async def run_embedded_outbox_publisher(container: "Container") -> None:
    """Drain the outbox table to Redis Streams while the API process runs.

    Delegates to the existing :func:`run_outbox_loop` so the
    exactly-once FOR UPDATE SKIP LOCKED publish + XADD verify pipeline
    (Phase 1 of the case study) is reused unchanged. Cancellation propagates
    via :class:`asyncio.CancelledError`; the loop's outer ``try/except``
    treats it as a stop signal.
    """
    bus = container.bus()
    await bus.ensure_streams()
    repo = container.outbox_repo()
    logger.info("embedded_outbox_publisher_started")

    try:
        await run_outbox_loop(repo=repo, bus=bus)
    except asyncio.CancelledError:
        logger.info("embedded_outbox_publisher_stopping")
        raise


async def run_embedded_recovery_worker(container: "Container") -> None:
    """Sweep stuck DRAFT documents and re-emit ``document.uploaded.v1``.

    Phase 2 of the upload-flow case study (2026-05-18). Delegates to the
    standalone loop in :mod:`ragbot.interfaces.workers.document_recovery_worker`
    so embedded + systemd deployments share one code path. Cancellation
    propagates through :class:`asyncio.CancelledError`; the loop is
    woken via the ``stop_event`` Future to skip the next sleep.
    """
    logger.info("embedded_recovery_worker_started")
    stop_event = asyncio.Event()
    try:
        await run_recovery_loop(container, stop_event=stop_event)
    except asyncio.CancelledError:
        logger.info("embedded_recovery_worker_stopping")
        stop_event.set()
        raise


async def run_embedded_cost_cap_alerter(container: "Container") -> None:
    """Periodically flag tenants near/over their monthly token cap (D11).

    Closes P2-J "the alerter is correct but only an offline script calls it —
    no scheduler". Every ``DEFAULT_COST_CAP_ALERT_INTERVAL_S`` it opens a
    read-only session and runs :func:`evaluate_tenants`, which emits one
    structured ``cost_cap_warning`` / ``cost_cap_exceeded`` event per flagged
    tenant at warn/error level so alert rules can route on severity. Read-only
    + best-effort: a sweep error is logged and the loop continues (the next
    sweep retries) — a transient DB blip must not kill the API process.
    """
    logger.info("embedded_cost_cap_alerter_started")
    factory = container.session_factory()
    while True:
        try:
            async with factory() as session:
                events = await evaluate_tenants(session=session, logger=logger)
            logger.info("cost_cap_sweep_done", flagged=len(events))
        except asyncio.CancelledError:
            logger.info("embedded_cost_cap_alerter_stopping")
            raise
        except (OSError, RuntimeError, RedisError, asyncio.TimeoutError) as exc:
            # Best-effort sweep — never crash the API on a transient DB error.
            logger.warning(
                "cost_cap_sweep_failed", error_type=type(exc).__name__,
            )
        await asyncio.sleep(DEFAULT_COST_CAP_ALERT_INTERVAL_S)


async def _supervise(name: str, coro_factory: Any, container: "Container") -> None:
    """Run a single embedded worker with crash isolation.

    The coroutine is expected to be a long-running supervisor that
    exits only via :class:`asyncio.CancelledError`. Any other exception
    is logged with full traceback and the function returns; the lifespan
    teardown will cancel the sibling worker.

    @param name: human-readable worker name for log events.
    @param coro_factory: callable returning the worker coroutine.
    @param container: DI container shared with the API process.
    """
    try:
        await coro_factory(container)
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, RedisError, asyncio.TimeoutError) as exc:
        # Narrow top-level supervisor wrapper — connection/runtime errors
        # from the bus loop / publisher loop. We do NOT auto-restart;
        # operator-visible journalctl + Prometheus counter surface the
        # regression. A NameError / ImportError style failure (programmer
        # bug) bypasses this handler and crashes the process loud,
        # matching CLAUDE.md fail-loud rule.
        logger.exception(
            "embedded_worker_crashed",
            worker=name,
            error_type=type(exc).__name__,
        )


def start_embedded_workers(container: "Container") -> list[asyncio.Task[None]]:
    """Spawn the embedded document_consumer + outbox_publisher + recovery tasks.

    Returns the task handles so the lifespan teardown can cancel + drain
    them in :func:`stop_embedded_workers`. Callers MUST keep a reference
    to the list — letting the tasks be garbage-collected before
    cancellation will surface as ``Task was destroyed but it is pending!``
    warnings.

    Phase 2 (2026-05-18): adds ``embedded_recovery_worker`` so a stuck
    DRAFT document is auto-replayed within ``DEFAULT_RECOVERY_INTERVAL_S``
    of crossing ``DEFAULT_RECOVERY_STUCK_THRESHOLD_S``.
    """
    consumer_task = asyncio.create_task(
        _supervise("document_consumer", run_embedded_document_consumer, container),
        name="embedded_document_consumer",
    )
    outbox_task = asyncio.create_task(
        _supervise("outbox_publisher", run_embedded_outbox_publisher, container),
        name="embedded_outbox_publisher",
    )
    recovery_task = asyncio.create_task(
        _supervise("recovery_worker", run_embedded_recovery_worker, container),
        name="embedded_recovery_worker",
    )
    cost_cap_task = asyncio.create_task(
        _supervise("cost_cap_alerter", run_embedded_cost_cap_alerter, container),
        name="embedded_cost_cap_alerter",
    )
    return [consumer_task, outbox_task, recovery_task, cost_cap_task]


async def stop_embedded_workers(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel + await the embedded worker tasks during lifespan teardown.

    Cancellation surfaces as :class:`asyncio.CancelledError` inside each
    supervisor; the workers clean up (unsubscribe / commit pending tx)
    in their own ``finally`` blocks. Errors during cancellation are
    isolated so one slow shutdown does not block the other.
    """
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except (OSError, RuntimeError, RedisError, asyncio.TimeoutError):
            logger.warning(
                "embedded_worker_teardown_error",
                worker=task.get_name(),
                exc_info=True,
            )


__all__ = [
    "run_embedded_document_consumer",
    "run_embedded_outbox_publisher",
    "run_embedded_recovery_worker",
    "start_embedded_workers",
    "stop_embedded_workers",
]
