"""Corpus version service — derives a stable cache-bust tag per bot.

Why this exists
---------------
The semantic cache key includes a ``corpus_version`` field so that when
the underlying document corpus changes (bot owner uploads / replaces /
deletes a document) old cached answers stop matching and rebuild on
the next turn. Historically the orchestrator hard-coded the literal
``"latest"`` here, which meant the corpus_version NEVER changed and
cached answers from yesterday's corpus were happily served against
today's docs — both stale-data risk AND a dead cache-bust mechanism.

This service replaces the literal with a deterministic 12-char hash
of ``(record_bot_id, MAX(documents.updated_at))``:

* Same docs → same hash → cache key stable → real cache hit possible.
* Doc updated → ``updated_at`` bumps → hash flips → cache key changes
  → next turn misses → fresh answer → old rows TTL-expire naturally.
* Bot with zero docs → ``DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL`` so
  the key is still stable rather than churning on NULL.

Caching strategy
----------------
Redis-cached for ``DEFAULT_CORPUS_VERSION_CACHE_TTL_S`` (5 min) so the
hot path doesn't issue a DB query every turn. After an upload the new
version becomes visible within one TTL window — that lag is acceptable
for a cache-bust signal because a slightly delayed bust merely keeps
serving fresh answers from the newly-built rows; staleness window is
bounded and never exceeds the TTL.

Failure mode
------------
Redis or DB error → log + return the legacy ``"latest"`` tag so the
pipeline keeps working (worst case = older behaviour, never a 500).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.shared.constants import (
    CACHE_KEY_CORPUS_VERSION_PREFIX,
    DEFAULT_BOT_CACHE_VERSION_HASH_LEN,
    DEFAULT_CORPUS_VERSION_CACHE_TTL_S,
    DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL,
    LEGACY_CORPUS_VERSION_TAG,
)
from ragbot.shared.types import BotId, TenantId

logger = structlog.get_logger(__name__)


def _redis_key(record_tenant_id: TenantId | str, record_bot_id: BotId | str) -> str:
    """Per-bot Redis key. Tenant prefix preserves multi-tenant isolation
    even at the cache layer (a stray cross-tenant lookup can't read another
    tenant's version)."""
    return (
        f"{CACHE_KEY_CORPUS_VERSION_PREFIX}{record_tenant_id!s}:{record_bot_id!s}"
    )


def _hash_payload(record_bot_id: BotId | str, marker: str) -> str:
    """Deterministic 12-char hex hash of ``(bot_id, marker)``.

    ``marker`` is ``str(MAX(updated_at))`` or the empty sentinel — any
    monotonic, change-on-update string works; we don't depend on the
    timestamp shape, only on it changing iff the corpus changed.
    """
    payload = f"{record_bot_id!s}|{marker}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:DEFAULT_BOT_CACHE_VERSION_HASH_LEN]


class CorpusVersionService:
    """Per-bot derived cache-bust tag with Redis-backed memoisation.

    Lifetime: singleton (DI-managed). All state lives in Redis + DB; the
    instance itself is stateless beyond its injected collaborators.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        redis_client: Any,
        cache_ttl_s: int = DEFAULT_CORPUS_VERSION_CACHE_TTL_S,
    ) -> None:
        """Wire in collaborators.

        @param session_factory: async sessionmaker for the read-only
            ``SELECT MAX(updated_at)`` query.
        @param redis_client: ``redis.asyncio.Redis``-shaped client
            (must expose ``get(key)`` + ``set(key, value, ex=...)``).
        @param cache_ttl_s: TTL for the per-bot Redis entry. Shorter
            TTL = faster propagation after upload, more DB hits; default
            is a reasonable middle ground.
        """
        self._sf = session_factory
        self._redis = redis_client
        self._ttl = max(1, int(cache_ttl_s))

    async def get_for_bot(
        self,
        record_tenant_id: TenantId | str | None,
        record_bot_id: BotId | str | None,
    ) -> str:
        """Return the 12-char corpus_version tag for ``record_bot_id``.

        Order: Redis cache → DB ``MAX(updated_at)`` → legacy tag fallback.

        @param record_tenant_id: internal tenant UUID — required for the
            Redis key isolation. ``None`` skips caching but still queries.
        @param record_bot_id: internal bot UUID. ``None`` short-circuits
            to the legacy tag (no bot context = no derivation possible).
        @return: 12-char hex hash, or ``DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL``
            for empty-corpus bots, or ``LEGACY_CORPUS_VERSION_TAG`` on
            any error.
        """
        if record_bot_id is None:
            # Without a bot we cannot derive — preserve legacy behaviour
            # so the cache lookup is still well-formed (and matches old rows).
            return LEGACY_CORPUS_VERSION_TAG

        cache_key: str | None = None
        if record_tenant_id is not None and self._redis is not None:
            cache_key = _redis_key(record_tenant_id, record_bot_id)
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached

        try:
            marker = await self._fetch_marker(record_bot_id)
        except (SQLAlchemyError, OSError, asyncio.TimeoutError) as exc:
            # DB outage: fall back to the legacy literal so the pipeline
            # keeps serving. Logged at warning so ops can correlate.
            logger.warning(
                "corpus_version_db_error",
                record_bot_id=str(record_bot_id),
                error_type=type(exc).__name__,
            )
            return LEGACY_CORPUS_VERSION_TAG

        if marker is None:
            # Bot has zero docs (or all soft-deleted). Stable sentinel
            # keeps the cache key from churning across empty turns.
            version = DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL
        else:
            version = _hash_payload(record_bot_id, marker)

        if cache_key is not None:
            await self._cache_set(cache_key, version)
        return version

    async def invalidate(
        self,
        record_tenant_id: TenantId | str,
        record_bot_id: BotId | str,
    ) -> None:
        """Force-bust the per-bot Redis entry (e.g. right after ingest).

        Optional — the TTL fallback already eventually-consistent. Call
        from the ingest pipeline if you want sub-TTL freshness.
        """
        if self._redis is None:
            return
        try:
            await self._redis.delete(_redis_key(record_tenant_id, record_bot_id))
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.debug(
                "corpus_version_invalidate_failed",
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _cache_get(self, key: str) -> str | None:
        """Best-effort Redis read. Any failure returns None and falls
        through to DB — cache outage degrades latency, never correctness."""
        try:
            raw = await self._redis.get(key)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.debug(
                "corpus_version_cache_get_failed",
                error_type=type(exc).__name__,
            )
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    async def _cache_set(self, key: str, value: str) -> None:
        """Best-effort Redis write with TTL. Failure is silent."""
        try:
            await self._redis.set(key, value.encode("utf-8"), ex=self._ttl)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.debug(
                "corpus_version_cache_set_failed",
                error_type=type(exc).__name__,
            )

    async def _fetch_marker(self, record_bot_id: BotId | str) -> str | None:
        """Read the ``MAX(updated_at)`` marker from ``documents``.

        Returns ``None`` when the bot has no live documents. We exclude
        soft-deleted rows so a deletion also bumps the version (the row
        no longer contributes its updated_at to the max — but the deleted
        timestamp itself counts via ``GREATEST(updated_at, deleted_at)``
        so a pure-delete still flips the hash).
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT MAX(GREATEST(updated_at, COALESCE(deleted_at, updated_at)))
                    FROM documents
                    WHERE record_bot_id = :bot_id
                    """
                ),
                {"bot_id": str(record_bot_id)},
            )
            row = result.first()
            if row is None or row[0] is None:
                return None
            # ``str(datetime)`` is stable per-Postgres-row — same row reads
            # produce the same string, so the hash is reproducible.
            return str(row[0])


__all__ = ["CorpusVersionService"]
