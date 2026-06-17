"""Nightly purge worker for Redis Streams + semantic_cache.

Trims old Redis Stream entries and deletes expired ``semantic_cache`` rows.
Keeps Redis memory bounded (Phase A capped maxmemory; this prevents the LRU
eviction from kicking in unnecessarily) and stops pgvector's ``semantic_cache``
table + HNSW index from growing unbounded.

CLI / cron entry::

    # Hourly (heavy traffic), daily, or whatever fits — script is idempotent.
    0 *  * * *  /path/to/venv/bin/python /path/to/repo/scripts/purge_stale_data.py

Flags::

    --dry-run            Print what WOULD be trimmed/deleted; no writes.
    --maxlen N           Override stream MAXLEN (default DEFAULT_STREAM_MAXLEN).
    --grace-hours N      Delete semantic_cache rows whose ``expires_at`` is
                         older than ``now() - N hours`` (default 24). Keeps
                         a small buffer so the grace period absorbs clock
                         skew between workers.

Exit code:
    0 always (purge failures logged but don't fail the cron job — Redis being
    unreachable should not turn a cron green->red overnight).

Domain neutral: no tenant literals; uses .env DATABASE_URL + REDIS_URL.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

# Allow `python scripts/purge_stale_data.py` from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_STREAM_MAXLEN,
    SUBJECT_BOT_CONFIG_UPDATED,
    SUBJECT_CHAT_ANSWERED,
    SUBJECT_CHAT_DELIVERY_FAILED,
    SUBJECT_CHAT_FAILED,
    SUBJECT_CHAT_RECEIVED,
    SUBJECT_CORPUS_VERSION_CHANGED,
    SUBJECT_DOCUMENT_FAILED,
    SUBJECT_DOCUMENT_INGESTED,
    SUBJECT_DOCUMENT_UPLOADED,
    SUBJECT_FEEDBACK_GIVEN,
    SUBJECT_SECURITY_INCIDENT,
)

LOG = logging.getLogger("ragbot.purge_stale_data")

# All known subjects published by the platform — keep in lockstep with
# RedisStreamsEventBus._stream_key(). Adding a new subject elsewhere requires
# adding it here so the trim worker knows about it.
ALL_SUBJECTS: tuple[str, ...] = (
    SUBJECT_CHAT_RECEIVED,
    SUBJECT_CHAT_ANSWERED,
    SUBJECT_CHAT_FAILED,
    SUBJECT_CHAT_DELIVERY_FAILED,
    SUBJECT_DOCUMENT_UPLOADED,
    SUBJECT_DOCUMENT_INGESTED,
    SUBJECT_DOCUMENT_FAILED,
    SUBJECT_CORPUS_VERSION_CHANGED,
    SUBJECT_BOT_CONFIG_UPDATED,
    SUBJECT_FEEDBACK_GIVEN,
    SUBJECT_SECURITY_INCIDENT,
)

# Match the prefix used by RedisStreamsEventBus.__init__(stream_prefix=...).
STREAM_PREFIX = "ragbot"


async def _trim_streams(maxlen: int, *, dry_run: bool) -> dict[str, int]:
    """XTRIM each known stream to ``maxlen`` (approximate). Returns per-stream
    count of entries trimmed (0 if dry-run).
    """
    redis_url = os.getenv("REDIS_URL") or os.getenv("REDIS_DSN")
    if not redis_url:
        LOG.warning("REDIS_URL not set — skipping stream trim")
        return {}

    try:
        from redis.asyncio import from_url
    except Exception:  # noqa: BLE001
        LOG.warning("redis package unavailable — skipping stream trim")
        return {}

    redis = from_url(redis_url, decode_responses=False)
    counts: dict[str, int] = {}
    try:
        for subject in ALL_SUBJECTS:
            key = f"{STREAM_PREFIX}:{subject}"
            try:
                length_before = await redis.xlen(key)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("xlen_failed", extra={"key": key, "err": str(exc)})
                continue
            if length_before <= maxlen:
                counts[key] = 0
                continue
            if dry_run:
                counts[key] = length_before - maxlen
                LOG.info(
                    "would_trim",
                    extra={"key": key, "current": length_before, "target": maxlen},
                )
                continue
            try:
                trimmed = await redis.xtrim(
                    key, maxlen=maxlen, approximate=True,
                )
                counts[key] = int(trimmed or 0)
                LOG.info(
                    "trimmed",
                    extra={"key": key, "trimmed": counts[key]},
                )
            except Exception as exc:  # noqa: BLE001
                LOG.warning("xtrim_failed", extra={"key": key, "err": str(exc)})
    finally:
        try:
            await redis.aclose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return counts


async def _purge_semantic_cache(grace_hours: int, *, dry_run: bool) -> int:
    """DELETE expired ``semantic_cache`` rows older than the grace window.

    Returns the row count deleted (or that WOULD be deleted in dry-run).
    """
    dsn = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC")
    if not dsn:
        LOG.warning("DATABASE_URL not set — skipping semantic_cache purge")
        return 0

    # Some envs ship the sync DSN; coerce to async driver for the engine.
    if dsn.startswith("postgresql://") and "+asyncpg" not in dsn:
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(dsn, future=True, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            count_q = text(
                "SELECT count(*) FROM semantic_cache "
                "WHERE expires_at IS NOT NULL "
                "  AND expires_at < now() - (:grace || ' hours')::interval"
            )
            result = await conn.execute(count_q, {"grace": int(grace_hours)})
            count = int(result.scalar_one() or 0)
            if count == 0:
                LOG.info("semantic_cache_purge_skipped", extra={"reason": "no_expired_rows"})
                return 0
            if dry_run:
                LOG.info(
                    "would_purge_semantic_cache",
                    extra={"count": count, "grace_hours": grace_hours},
                )
                return count
            del_q = text(
                "DELETE FROM semantic_cache "
                "WHERE expires_at IS NOT NULL "
                "  AND expires_at < now() - (:grace || ' hours')::interval"
            )
            res = await conn.execute(del_q, {"grace": int(grace_hours)})
            deleted = int(res.rowcount or 0)
            LOG.info("semantic_cache_purged", extra={"deleted": deleted})
            return deleted
    except Exception as exc:  # noqa: BLE001
        LOG.warning("semantic_cache_purge_failed", extra={"err": str(exc)})
        return 0
    finally:
        await engine.dispose()


async def _amain(args: argparse.Namespace) -> dict[str, Any]:
    trim_counts = await _trim_streams(args.maxlen, dry_run=args.dry_run)
    cache_deleted = await _purge_semantic_cache(args.grace_hours, dry_run=args.dry_run)
    return {
        "stream_trim": trim_counts,
        "semantic_cache_deleted": cache_deleted,
        "dry_run": args.dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge stale Redis Streams + semantic_cache rows.")
    parser.add_argument("--dry-run", action="store_true", help="No writes; print only.")
    parser.add_argument(
        "--maxlen", type=int, default=DEFAULT_STREAM_MAXLEN,
        help=f"Stream MAXLEN (default {DEFAULT_STREAM_MAXLEN}).",
    )
    parser.add_argument(
        "--grace-hours", type=int, default=24,
        help="semantic_cache rows whose expires_at < now()-N hours are deleted.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        result = asyncio.run(_amain(args))
        LOG.info("purge_complete", extra={"result": result})
    except Exception:  # noqa: BLE001
        LOG.exception("purge_failed_unexpectedly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
