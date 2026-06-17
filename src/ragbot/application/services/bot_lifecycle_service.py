"""Bot lifecycle purge — irreversible second phase of the two-phase delete.

ADR-W1-D4. ``BotManagementService.delete_bot`` is a SOFT delete (grace
window for undo); the FK ``ON DELETE CASCADE`` chain to the child tables
(documents, document_chunks, semantic_cache, conversations, messages,
request_logs, bot_model_bindings, ...) therefore never fired and orphan
rows + Redis keys accumulated forever. This service is the explicit
purge step that an admin (or a future retention cron) runs AFTER the
grace window.

Saga order (each step idempotent — re-run from the top after a crash
converges):

S1  guard      — SELECT the bot 4-key snapshot, tenant-scoped. No row →
                 report ``purged=False`` but still run the Redis steps
                 (covers crash-between-S2-and-S3 re-runs). Live row
                 (``is_deleted=false``) → :class:`BotNotPurgeableError`.
S2  hard-delete — single transaction: ``DELETE FROM bots`` (FK CASCADE
                 wipes the child tables — verified live, no manual
                 child DELETEs) + forensic audit row + ``bot.purged.v1``
                 outbox row on the SAME session, one commit.
S3  corpus bust — ``CorpusVersionService.invalidate`` (best-effort,
                 300s TTL backstop).
S4  registry bust — 4-key invalidate from the S1 snapshot. Skipped on
                 re-run (snapshot gone; soft-delete already invalidated).
S5  uq-cache bust — SCAN ``ragbot:uq:v*:{record_bot_id}:*`` + UNLINK.
S6  deliberate skips — embedding L1 cache is content-keyed and SHARED
                 across bots (``ragbot:emb:{model}:{dim}:{sha}``):
                 purging it would evict OTHER bots' entries, so it is
                 intentionally untouched. Outbox dedup keys are
                 msg-UUID-keyed with their own TTL. Both reported in
                 ``BotPurgeReport.skipped`` so tests assert intent.

Redis steps run AFTER the S2 commit: busting first and then failing the
DELETE would be harmless (cache rebuilds from the still-live DB), but
the reverse order keeps the saga re-runnable with TTL backstops.
"""

from __future__ import annotations

import json
import uuid as _uuid
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import text as _sql_text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.application.services.corpus_version_service import CorpusVersionService
from ragbot.shared.constants import (
    CACHE_KEY_UQ_PREFIX,
    DEFAULT_PURGE_UQ_SCAN_COUNT,
    SUBJECT_BOT_PURGED,
)

logger = structlog.get_logger(__name__)

# Deliberate-skip markers surfaced in BotPurgeReport.skipped — stable
# labels so gate tests + dashboards can join on them.
SKIP_EMBEDDING_CACHE = "embedding_cache"
SKIP_OUTBOX_DEDUP = "outbox_dedup"
# Re-run after the bots row is gone: the 4-key snapshot no longer exists,
# so the registry invalidate cannot be addressed. The registry was already
# invalidated at soft-delete time (delete_bot) — TTL/reload backstop.
SKIP_REGISTRY_NO_SNAPSHOT = "registry_4key_snapshot_missing"

# Audit action label — keep stable for admin dashboards.
_AUDIT_ACTION_PURGE = "purge"
_AUDIT_RESOURCE_BOT = "bot"


class BotNotPurgeableError(Exception):
    """Purge requested on a bot that is not soft-deleted yet (guard S1)."""


class BotPurgeReport(BaseModel):
    """Measured outcome of one purge_bot run (no-guess: real counts)."""

    record_bot_id: UUID
    purged: bool                # False = guard declined (no row / re-run)
    db_rows_bots: int           # rowcount of DELETE FROM bots (0 on re-run)
    redis_uq_keys: int          # understand-query keys UNLINKed
    skipped: list[str]          # deliberate no-ops (see SKIP_* markers)


class BotLifecycleService:
    """Purge saga over collaborators that all pre-exist (EVOLVE — no new infra)."""

    def __init__(
        self,
        *,
        session_factory: Any,
        registry: BotRegistryService,
        corpus_version_service: CorpusVersionService,
        redis_client: Any,
        tenant_session: Any,
        audit_writer: Any,
        tenant_repository_factory: Any,
    ) -> None:
        """@param session_factory: async_sessionmaker — every DB write goes
            through ``tenant_session`` (RLS GUC bound, R2).
        @param registry: 4-key bot registry — S4 bust.
        @param corpus_version_service: per-bot corpus tag — S3 bust (this
            wires the previously dead ``invalidate()``).
        @param redis_client: raw client for the S5 SCAN+UNLINK.
        @param tenant_session: ``session_with_tenant``-shaped async context
            manager ``(factory, *, record_tenant_id) -> AsyncSession``.
            Injected by bootstrap (hexagonal boundary: application/ MUST
            NOT import infrastructure/ — gate
            ``tests/unit/test_hexagonal_boundary.py``).
        @param audit_writer: ``insert_audit_row``-shaped coroutine —
            forensic HMAC-chain write, same-session (Async Rule 8).
        @param tenant_repository_factory: ``(session) -> repo`` exposing
            ``soft_delete_tenant`` (raises ``TenantHasActiveBotsError``
            when a live bot remains).
        """
        self._sf = session_factory
        self._registry = registry
        self._corpus = corpus_version_service
        self._redis = redis_client
        self._tenant_session = tenant_session
        self._audit_writer = audit_writer
        self._tenant_repository_factory = tenant_repository_factory

    # ── Public API ──────────────────────────────────────────────────────

    async def purge_bot(
        self,
        record_bot_id: UUID,
        *,
        record_tenant_id: UUID,
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> BotPurgeReport:
        """Hard-delete one soft-deleted bot + bust every derived cache.

        ``record_tenant_id`` is REQUIRED (not Optional): with RLS active
        a tenant-less DELETE silently matches 0 rows — the signature
        makes that misuse unrepresentable (ADR R2).
        """
        snapshot: dict[str, Any] | None = None
        db_rows = 0

        # S1 + S2 — guard, hard-delete, audit, outbox: ONE transaction.
        async with self._tenant_session(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(
                _sql_text(
                    """
                    SELECT id, workspace_id, bot_id, channel_type, is_deleted
                    FROM bots
                    WHERE id = :record_bot_id
                      AND record_tenant_id = :record_tenant_id
                    """,
                ),
                {
                    "record_bot_id": record_bot_id,
                    "record_tenant_id": record_tenant_id,
                },
            )
            row = result.fetchone()
            if row is not None:
                if not row.is_deleted:
                    raise BotNotPurgeableError(
                        "bot is not soft-deleted — purge requires the "
                        "grace-window delete first",
                    )
                snapshot = {
                    "bot_uuid": str(row.id),
                    "workspace_id": row.workspace_id,
                    "bot_id": row.bot_id,
                    "channel_type": row.channel_type,
                    "record_tenant_id": str(record_tenant_id),
                }
                delete_result = await session.execute(
                    _sql_text(
                        """
                        DELETE FROM bots
                        WHERE id = :record_bot_id
                          AND record_tenant_id = :record_tenant_id
                          AND is_deleted = true
                        """,
                    ),
                    {
                        "record_bot_id": record_bot_id,
                        "record_tenant_id": record_tenant_id,
                    },
                )
                db_rows = int(delete_result.rowcount or 0)
                # Audit inline + fail-loud (Async Rule 8) — bot vanishes
                # iff the forensic row commits with it.
                await self._audit_writer(
                    session,
                    record_tenant_id=record_tenant_id,
                    workspace_id=row.workspace_id,
                    actor_user_id=actor_user_id,
                    action=_AUDIT_ACTION_PURGE,
                    resource_type=_AUDIT_RESOURCE_BOT,
                    resource_id=str(record_bot_id),
                    before_json=snapshot,
                    after_json=None,
                    reason="lifecycle purge after soft-delete grace window",
                    trace_id=trace_id,
                )
                # Outbox on the SAME session — uow_factory would open a
                # second session (= second tx) and break atomicity.
                await self._insert_outbox_purged(
                    session,
                    snapshot=snapshot,
                    record_tenant_id=record_tenant_id,
                    trace_id=trace_id,
                )
                await session.commit()

        # S3 — corpus version bust (callee swallows Redis errors).
        await self._corpus.invalidate(record_tenant_id, record_bot_id)

        # S4 — registry bust (needs the 4-key snapshot from S1).
        skipped: list[str] = []
        if snapshot is not None:
            await self._registry.invalidate(
                record_tenant_id,
                snapshot["workspace_id"],
                snapshot["bot_id"],
                snapshot["channel_type"],
            )
        else:
            skipped.append(SKIP_REGISTRY_NO_SNAPSHOT)

        # S5 — understand-query cache bust.
        uq_keys = await self._unlink_uq_keys(record_bot_id)

        # S6 — deliberate skips (see module docstring for WHY).
        skipped.extend((SKIP_EMBEDDING_CACHE, SKIP_OUTBOX_DEDUP))

        report = BotPurgeReport(
            record_bot_id=record_bot_id,
            purged=snapshot is not None,
            db_rows_bots=db_rows,
            redis_uq_keys=uq_keys,
            skipped=skipped,
        )
        logger.info(
            "bot_purged",
            record_bot_id=str(record_bot_id),
            record_tenant_id=str(record_tenant_id),
            purged=report.purged,
            db_rows_bots=report.db_rows_bots,
            redis_uq_keys=report.redis_uq_keys,
            actor_user_id=actor_user_id,
            trace_id=trace_id,
        )
        return report

    async def purge_tenant(
        self,
        record_tenant_id: UUID,
        *,
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> list[BotPurgeReport]:
        """Purge every bot of a tenant, then soft-delete the tenant row.

        Fan-out is SEQUENTIAL by design (Async Rule 7: each purge_bot is
        a heavy cascade-DELETE transaction on the shared pool — gathering
        N of them spikes pool + row locks for zero user-visible win on an
        admin path). The tenant row itself stays (soft-deleted) as the FK
        anchor for the forensic audit_log chain — hard tenant delete is
        a retention-policy decision deferred to D11.
        """
        async with self._tenant_session(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(
                _sql_text(
                    """
                    SELECT id FROM bots
                    WHERE record_tenant_id = :record_tenant_id
                    ORDER BY created_at
                    """,
                ),
                {"record_tenant_id": record_tenant_id},
            )
            bot_ids = [r[0] for r in result.fetchall()]

        reports: list[BotPurgeReport] = []
        for bot_uuid in bot_ids:
            try:
                reports.append(
                    await self.purge_bot(
                        bot_uuid,
                        record_tenant_id=record_tenant_id,
                        actor_user_id=actor_user_id,
                        trace_id=trace_id,
                    ),
                )
            except (
                TimeoutError, BotNotPurgeableError,
                SQLAlchemyError, RedisError, OSError,
            ) as exc:
                # Per-bot saga independent (R6): report + continue; a
                # re-run of purge_tenant converges the leftovers.
                logger.warning(
                    "bot_purge_failed_in_tenant_fanout",
                    record_bot_id=str(bot_uuid),
                    record_tenant_id=str(record_tenant_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                reports.append(
                    BotPurgeReport(
                        record_bot_id=bot_uuid,
                        purged=False,
                        db_rows_bots=0,
                        redis_uq_keys=0,
                        skipped=[],
                    ),
                )

        # Soft-delete the tenant AFTER the drain. If a live (non-soft-
        # deleted) bot remains, TenantHasActiveBotsError propagates —
        # fail loud, the operator must delete_bot first.
        async with self._tenant_session(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            repo = self._tenant_repository_factory(session)
            await repo.soft_delete_tenant(record_tenant_id)

        logger.info(
            "tenant_purged",
            record_tenant_id=str(record_tenant_id),
            bots_total=len(bot_ids),
            bots_purged=sum(1 for r in reports if r.purged),
            actor_user_id=actor_user_id,
            trace_id=trace_id,
        )
        return reports

    # ── Internal helpers ────────────────────────────────────────────────

    async def _insert_outbox_purged(
        self,
        session: Any,
        *,
        snapshot: dict[str, Any],
        record_tenant_id: UUID,
        trace_id: str | None,
    ) -> None:
        """Raw outbox INSERT on the caller's session (atomic with S2)."""
        payload = {
            "event_id": str(_uuid.uuid4()),
            "event_type": SUBJECT_BOT_PURGED,
            "record_tenant_id": str(record_tenant_id),
            "workspace_id": snapshot["workspace_id"],
            "bot_id": snapshot["bot_id"],
            "channel_type": snapshot["channel_type"],
            "bot_uuid": snapshot["bot_uuid"],
            "trace_id": trace_id or "",
        }
        await session.execute(
            _sql_text(
                """
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
                """,
            ),
            {
                "id": _uuid.uuid4(),
                "subject": SUBJECT_BOT_PURGED,
                "payload": json.dumps(payload).encode("utf-8"),
                "trace_id": trace_id or "",
                "tenant_id": record_tenant_id,
                "workspace_id": snapshot["workspace_id"],
                "metadata": json.dumps({"event_type": SUBJECT_BOT_PURGED}),
            },
        )

    async def _unlink_uq_keys(self, record_bot_id: UUID) -> int:
        """SCAN + UNLINK understand-query keys for this bot.

        Wildcard on the prompt-version segment because ``prompt_version``
        changes with config. Best-effort: every key has its own TTL, so a
        Redis blip here only delays the cleanup.
        """
        if self._redis is None:
            return 0
        pattern = f"{CACHE_KEY_UQ_PREFIX}*:{record_bot_id}:*"
        total = 0
        cursor = 0
        try:
            while True:
                cursor, keys = await self._redis.scan(
                    cursor=cursor,
                    match=pattern,
                    count=DEFAULT_PURGE_UQ_SCAN_COUNT,
                )
                if keys:
                    total += int(await self._redis.unlink(*keys) or 0)
                if not cursor:
                    break
        except (TimeoutError, RedisError, OSError) as exc:
            logger.warning(
                "bot_purge_uq_scan_failed",
                record_bot_id=str(record_bot_id),
                error_type=type(exc).__name__,
            )
        return total


__all__ = [
    "SKIP_EMBEDDING_CACHE",
    "SKIP_OUTBOX_DEDUP",
    "SKIP_REGISTRY_NO_SNAPSHOT",
    "BotLifecycleService",
    "BotNotPurgeableError",
    "BotPurgeReport",
]
