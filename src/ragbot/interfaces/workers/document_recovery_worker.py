"""Document recovery worker — auto-sweep stuck DRAFT documents.

Phase 2 of the upload-flow case study (2026-05-16 / 2026-05-18 ship).

Evidence: a tenant document stuck ``state=DRAFT`` for 6h because the
document worker crashed after
the bus delivered ``document.uploaded.v1`` but before the ingest service
updated the document row. The outbox row was already marked
``processed`` by the publisher, so no second delivery happened. Operator
fix was a manual SQL ``INSERT outbox`` to replay the event.

Phase 1 (``redis_streams_bus.publish_raw`` XADD-verify) closes the most
common silent-fail window — XADD acks but the entry is not durable. The
worker-crash-after-XACK-before-state-update window cannot be closed
purely by the publisher; we need a sweeper that detects any DRAFT
document older than the cold-start ceiling and re-emits the canonical
event so the ingest pipeline gets a second chance.

Design
------

- Background loop, cadence ``DEFAULT_RECOVERY_INTERVAL_S`` (300 s).
- Per sweep, scan up to ``DEFAULT_RECOVERY_BATCH_SIZE`` rows that are
  either ``state='DRAFT'`` older than ``DEFAULT_RECOVERY_STUCK_THRESHOLD_S``
  or ``state='active'`` with zero chunks past the same threshold (worker
  crashed between the ingest UPSERT and the chunk write), AND no outbox
  row already pending for the document (anti-duplicate).
- Per stuck row: build the canonical ``document.uploaded.v1`` payload
  (matching the schema emitted by Action 1 in ``test_chat.py``) and
  INSERT a new outbox row. ``outbox_publisher`` picks it up on the next
  poll and the existing worker handles ingest exactly as if the user
  re-uploaded.
- Audit-log every replay via ``insert_audit_row`` (HMAC chain) for
  forensic traceability of platform-initiated retries.
- Prometheus counter ``document_recovery_replayed_total{status=...}``
  surfaces operator dashboards.

Exception policy
----------------

The outer loop catches ONLY transient classes (``RedisError``,
``OSError``, ``asyncio.TimeoutError``, ``SQLAlchemyError``,
``RuntimeError`` — the last because ``session_with_tenant`` raises
``RuntimeError`` when the tenant ctx is unbound, a recoverable
mis-wire). Programmer bugs (``AttributeError`` / ``TypeError``) bubble
out so they surface loud in dev / CI per CLAUDE.md fail-loud rule.

Run modes
---------

- **Embedded** (default) — the FastAPI lifespan spawns this loop via
  :func:`ragbot.interfaces.http.embedded_workers.start_embedded_workers`.
- **Standalone** — DevOps that want horizontal scale run ``python -m
  ragbot.interfaces.workers.document_recovery_worker`` under their own
  process manager; the ``main()`` entry point sets up logging and
  reuses the DI container.
"""

from __future__ import annotations

import asyncio
import json as _json
import signal
import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import text as _sql_text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.config.logging import setup_logging
from ragbot.config.settings import get_settings
from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.infrastructure.observability.metrics import (
    document_recovery_replayed_total,
)
from ragbot.infrastructure.repositories.audit_chain_writer import (
    insert_audit_row,
)
from ragbot.shared.constants import (
    DEFAULT_RECOVERY_BATCH_SIZE,
    DEFAULT_RECOVERY_INTERVAL_S,
    DEFAULT_RECOVERY_REPLAY_COOLDOWN_S,
    DEFAULT_RECOVERY_STUCK_THRESHOLD_S,
    SUBJECT_DOCUMENT_UPLOADED,
    WORKSPACE_SYSTEM_SLUG,
)

if TYPE_CHECKING:
    from ragbot.bootstrap import Container

logger = structlog.get_logger(__name__)

# Audit action label — keep stable across versions so admin dashboards
# can join on it.
_AUDIT_ACTION_RECOVERY_REPLAY = "recovery_replay_emitted"
# Audit actor sentinel — platform-initiated events are NOT tied to a
# specific user; the literal "system" matches the platform-system slug
# convention used elsewhere (``WORKSPACE_SYSTEM_SLUG``).
_AUDIT_ACTOR_SYSTEM = "system"
# Audit resource type label.
_AUDIT_RESOURCE_DOCUMENT = "document"
# Recovery event payload tag — lets the downstream worker know this came
# from the sweep (idempotency / debugging). Plain metadata, not a new
# event subject.
_RECOVERY_REPLAY_FLAG = "recovery_replay"


def _bump_metric(label: str) -> None:
    """Best-effort prometheus counter increment.

    Mirrors the helper in ``outbox_publisher.py`` — a metrics-client
    blowup must NOT take down the recovery sweep. Narrow to the actual
    classes the prometheus client raises: ``ValueError`` (unknown
    label value), ``TypeError`` (non-numeric increment), ``KeyError``
    (collector deregistered mid-loop). Programmer bugs propagate.
    """
    try:
        document_recovery_replayed_total.labels(status=label).inc()
    except (ValueError, TypeError, KeyError):
        # Metric path is observability-only; do not break recovery.
        logger.debug("document_recovery_metric_bump_failed", label=label)


async def _scan_stuck_documents(
    *,
    session: Any,
    stuck_threshold_s: int,
    batch_size: int,
    replay_cooldown_s: int,
) -> list[Any]:
    """Return up to ``batch_size`` rows stuck in DRAFT or active-0-chunk.

    Anti-duplicate: a doc with a fresh ``document.uploaded.v1`` outbox
    row created AFTER the document was first inserted is already in
    flight (publisher hasn't drained yet or worker is retrying) — skip
    it. The ``LEFT JOIN ... WHERE o.id IS NULL`` pattern is the
    canonical "not exists" SQL.

    The anti-dup is TIME-BOUNDED by ``replay_cooldown_s``: only a recent
    replay suppresses the sweep. A replay row is marked ``processed`` on
    publish-to-stream, so without the bound a replay whose downstream
    ingest then FAILED would hide the doc forever (permanent DRAFT). The
    cooldown turns that into retry-with-backoff — after the window the
    still-stuck doc is swept again.
    """
    # ``outbox.payload`` is ``bytea`` (binary-safe JSON storage), so we
    # decode via ``convert_from(..., 'UTF8')`` before casting to jsonb.
    # Direct ``payload::jsonb`` raises ``cannot cast type bytea to jsonb``
    # on Postgres 14+. The recovery sweep tolerates a few millis extra
    # CPU per scan in exchange for correctness (was previously throwing
    # and skipping the dedup join, which made the sweep replay docs that
    # the publisher had already enqueued).
    # Two stuck windows (ADR-W1-D4 §2c):
    #   1. DRAFT older than threshold — original crash window (bus
    #      delivered, worker died before the ingest UPSERT).
    #   2. 'active' with ZERO chunks — ingest UPSERTs state='active'
    #      synchronously BEFORE the async chunk+embed; a crash in
    #      between leaves the row "active" but unanswerable. The branch
    #      keys on ``updated_at`` (UPSERT re-ingest bumps updated_at,
    #      not created_at — created_at would false-positive old docs on
    #      the first sweep) and double-checks document_chunks because
    #      ``chunks_processed`` is only written by the terminal flip
    #      (NULL mid-crash even when some chunks already persisted).
    # Anti-dup join compares against GREATEST(created_at, updated_at):
    # the original upload event always predates the worker's UPSERT
    # bump, so a crashed re-ingest is NOT masked by its own stale event;
    # replay rows emitted by this sweeper (created_at > updated_at)
    # still dedup later sweeps. DRAFT rows are untouched by the UPSERT,
    # so their behaviour is unchanged.
    sql = """
        SELECT d.id, d.record_tenant_id, d.workspace_id, d.record_bot_id,
               d.source_url, d.document_name, d.tool_name, d.mime_type
        FROM documents d
        LEFT JOIN outbox o
            ON convert_from(o.payload, 'UTF8')::jsonb->>'document_id' = d.id::text
            AND o.subject = :subject
            AND o.status IN ('pending', 'processed')
            AND o.created_at > GREATEST(d.created_at, d.updated_at)
            AND o.created_at > now() - make_interval(secs => :replay_cooldown_s)
        WHERE d.deleted_at IS NULL
          AND o.id IS NULL
          AND (
                (d.state = 'DRAFT'
                 AND d.created_at < now() - make_interval(secs => :stuck_threshold_s))
             OR (d.state = 'active'
                 AND COALESCE(d.chunks_processed, 0) = 0
                 AND NOT EXISTS (SELECT 1 FROM document_chunks dc
                                  WHERE dc.record_document_id = d.id)
                 AND d.updated_at < now() - make_interval(secs => :stuck_threshold_s))
              )
        ORDER BY d.created_at ASC
        LIMIT :batch_size
    """
    result = await session.execute(
        _sql_text(sql),
        {
            "subject": SUBJECT_DOCUMENT_UPLOADED,
            "stuck_threshold_s": stuck_threshold_s,
            "batch_size": batch_size,
            "replay_cooldown_s": replay_cooldown_s,
        },
    )
    return list(result.fetchall())


def _build_replay_payload(row: Any, *, trace_id: str) -> dict[str, Any]:
    """Build the canonical ``document.uploaded.v1`` payload.

    Schema mirrors the producer in ``test_chat.py`` (Action 1) exactly
    so the downstream worker handles the replay with no special case.
    Optional fields (``uploaded_by``, ``force_reingest``) fall back to
    stable defaults — the recovery sweep does not know who originally
    uploaded the doc.
    """
    return {
        "event_id": str(_uuid.uuid4()),
        "event_type": SUBJECT_DOCUMENT_UPLOADED,
        "schema_version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "record_tenant_id": str(row.record_tenant_id),
        "trace_id": trace_id,
        "workspace_id": row.workspace_id,
        "job_id": str(_uuid.uuid4()),
        "record_bot_id": str(row.record_bot_id),
        "document_id": str(row.id),
        "source_url": row.source_url,
        "document_name": row.document_name,
        "tool_name": row.tool_name,
        "mime_type": row.mime_type,
        "uploaded_by": _AUDIT_ACTOR_SYSTEM,
        "force_reingest": False,
        _RECOVERY_REPLAY_FLAG: True,
    }


async def _replay_one_document(
    *,
    session_factory: Any,
    row: Any,
) -> bool:
    """Insert a fresh ``document.uploaded.v1`` outbox row + audit trail.

    All writes happen inside a single ``session_with_tenant`` context so
    RLS is enforced (cross-tenant write leak prevented) AND the outbox
    INSERT + audit INSERT commit atomically.

    Returns True on success, False on a transient failure that the
    caller should count as a failed replay. Programmer bugs propagate.
    """
    trace_id = f"recovery-{row.id}"
    payload = _build_replay_payload(row, trace_id=trace_id)
    outbox_id = _uuid.uuid4()

    async with session_with_tenant(
        session_factory, record_tenant_id=row.record_tenant_id,
    ) as session:
        await session.execute(
            _sql_text("""
                INSERT INTO outbox (
                    id, subject, payload, headers, trace_id,
                    record_tenant_id, workspace_id, channel_type,
                    retry_count, status, metadata_json
                ) VALUES (
                    :id, :subject, :payload,
                    CAST('{}' AS jsonb), :trace_id,
                    :tenant_id, :workspace_id, NULL, 0, 'pending',
                    CAST(:metadata AS jsonb)
                )
            """),
            {
                "id": outbox_id,
                "subject": SUBJECT_DOCUMENT_UPLOADED,
                "payload": _json.dumps(payload).encode("utf-8"),
                "trace_id": trace_id,
                "tenant_id": row.record_tenant_id,
                "workspace_id": row.workspace_id,
                "metadata": _json.dumps({_RECOVERY_REPLAY_FLAG: True}),
            },
        )
        await insert_audit_row(
            session,
            record_tenant_id=row.record_tenant_id,
            workspace_id=row.workspace_id or WORKSPACE_SYSTEM_SLUG,
            actor_user_id=_AUDIT_ACTOR_SYSTEM,
            action=_AUDIT_ACTION_RECOVERY_REPLAY,
            resource_type=_AUDIT_RESOURCE_DOCUMENT,
            resource_id=str(row.id),
            after_json={
                "outbox_id": str(outbox_id),
                "subject": SUBJECT_DOCUMENT_UPLOADED,
                "trace_id": trace_id,
            },
            reason="stuck DRAFT exceeded recovery threshold",
            trace_id=trace_id,
        )
        await session.commit()
    return True


async def _run_one_sweep(
    *,
    session_factory: Any,
    stuck_threshold_s: int,
    batch_size: int,
    replay_cooldown_s: int = DEFAULT_RECOVERY_REPLAY_COOLDOWN_S,
) -> int:
    """Scan + replay one batch. Returns count of successful replays.

    The scan runs in its own short-lived tenant-less session (the LEFT
    JOIN query is platform-wide forensic — no row mutation, so RLS
    bypass is acceptable for the read). Each per-document replay opens
    its own ``session_with_tenant`` so writes stay scoped.
    """
    session = session_factory()
    try:
        rows = await _scan_stuck_documents(
            session=session,
            stuck_threshold_s=stuck_threshold_s,
            batch_size=batch_size,
            replay_cooldown_s=replay_cooldown_s,
        )
    finally:
        await session.close()

    if not rows:
        return 0

    success = 0
    for row in rows:
        try:
            replayed = await _replay_one_document(
                session_factory=session_factory, row=row,
            )
        except (
            RedisError, OSError, asyncio.TimeoutError,
            SQLAlchemyError, RuntimeError,
        ) as exc:
            _bump_metric("failed")
            logger.warning(
                "document_recovery_replay_failed",
                document_id=str(row.id),
                record_tenant_id=str(row.record_tenant_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            continue
        if replayed:
            success += 1
            _bump_metric("success")
            logger.info(
                "document_recovery_replayed",
                document_id=str(row.id),
                record_tenant_id=str(row.record_tenant_id),
                workspace_id=row.workspace_id,
            )
    return success


async def run_recovery_loop(
    container: "Container",
    *,
    stop_event: asyncio.Event | None = None,
    interval_s: int = DEFAULT_RECOVERY_INTERVAL_S,
    stuck_threshold_s: int = DEFAULT_RECOVERY_STUCK_THRESHOLD_S,
    batch_size: int = DEFAULT_RECOVERY_BATCH_SIZE,
) -> None:
    """Main loop — sweep stuck documents on cadence.

    @param container: DI container providing ``system_session_factory()``
        (the BYPASSRLS engine — the cross-tenant stuck-doc scan must not be
        RLS-filtered, or it would see zero rows under the request role).
    @param stop_event: optional external stop signal; when set the loop
        exits before the next sleep.
    @param interval_s: seconds between sweeps.
    @param stuck_threshold_s: minimum age (seconds) for a DRAFT doc to
        qualify for replay.
    @param batch_size: max rows per sweep.
    """
    # Cross-tenant forensic scan over documents/outbox (RLS-forced) → BYPASSRLS
    # system factory so the scan sees stuck docs across every tenant. The
    # per-doc replay below uses session_with_tenant (SET LOCAL is harmless on
    # the BYPASSRLS role) so writes stay tenant-attributed.
    session_factory = container.system_session_factory()
    stop_event = stop_event or asyncio.Event()

    logger.info(
        "document_recovery_worker_started",
        interval_s=interval_s,
        stuck_threshold_s=stuck_threshold_s,
        batch_size=batch_size,
    )

    while not stop_event.is_set():
        sweep_started = time.perf_counter()
        try:
            replayed = await _run_one_sweep(
                session_factory=session_factory,
                stuck_threshold_s=stuck_threshold_s,
                batch_size=batch_size,
            )
            logger.debug(
                "document_recovery_sweep_done",
                replayed=replayed,
                duration_s=time.perf_counter() - sweep_started,
            )
        except (
            RedisError, OSError, asyncio.TimeoutError,
            SQLAlchemyError, RuntimeError,
        ) as exc:
            # Narrow top-level catch — transient infra. Sleep + continue
            # so a transient DB blip does not kill the supervisor.
            logger.warning(
                "document_recovery_sweep_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            # Normal cadence — interval expired without stop_event being
            # set. Loop continues.
            continue

    logger.info("document_recovery_worker_stopped")


async def main() -> None:
    """Standalone entry point — run the recovery worker under a process manager."""
    from ragbot.bootstrap import Container  # noqa: PLC0415

    settings = get_settings()
    setup_logging(level=settings.observability.log_level, json=True)
    container = Container()
    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    await run_recovery_loop(container, stop_event=stop_event)


__all__ = [
    "run_recovery_loop",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
