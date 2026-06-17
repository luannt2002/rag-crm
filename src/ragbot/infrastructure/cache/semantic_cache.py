"""Semantic cache — 2-tier: exact SHA256 hash match → cosine similarity fallback.

Uses Postgres pgvector instead of Redis hash-only approach.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import weakref
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.ports.cache_port import (
    CachedResponse,
    SemanticCachePort,
)
from ragbot.shared.constants import (
    DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S,
    DEFAULT_SEMANTIC_CACHE_HIT_LOG_ENABLED,
    DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S,
    DEFAULT_SEMANTIC_CACHE_WAIT_RETRY_S,
    SEMANTIC_CACHE_THRESHOLD,
)
from ragbot.shared.types import (
    BotId,
    BotVersion,
    CorpusVersion,
    TenantId,
)

# Single vector column ``query_embedding`` on the ``semantic_cache`` table.
# The whitelist below preserves the SQL-injection defence around f-string
# substitution (mirrors ``pgvector_store._validate_embedding_column``) even
# though only one name is currently allowed — adding a future column
# requires explicit opt-in.
QUERY_EMBEDDING_COLUMN: str = "query_embedding"
_VALID_QUERY_EMBEDDING_COLUMNS: frozenset[str] = frozenset(
    {QUERY_EMBEDDING_COLUMN},
)


def _validate_query_embedding_column(column: str) -> str:
    """Reject any column not on the whitelist; return the safe name."""
    if column not in _VALID_QUERY_EMBEDDING_COLUMNS:
        raise ValueError(
            f"unsupported query embedding column {column!r}; "
            f"allowed: {sorted(_VALID_QUERY_EMBEDDING_COLUMNS)}",
        )
    return column


def _data_to_cache_column(data_column: str | None) -> str:
    """Map a data-table embedding column name onto the cache-table name.

    The cache table has a single ``query_embedding`` column; the helper
    exists so callers can keep passing the state-key data column name
    without each callsite knowing the cache-table naming.
    """
    return QUERY_EMBEDDING_COLUMN

logger = structlog.get_logger(__name__)

# Warn-not-raise: prometheus_client is an observability dependency.
# Cache hits / misses still work — only the Prometheus counter exports go
# dark. Sacred paths (retrieval / embed / answer) tolerate zero loss when
# metrics are missing; operator just loses visibility into stampede-avoid
# counts. If you see this warning at startup, `pip install prometheus_client`.
try:  # pragma: no cover — optional metrics import (tests may not load app)
    from ragbot.infrastructure.observability.metrics import (
        cache_stampede_avoided_total,
        inflight_locks_size,
    )
except ImportError as _metrics_exc:
    logger.warning(
        "feature_disabled_dep_missing",
        module="semantic_cache",
        feature="cache_metrics_export",
        missing_pkg="prometheus_client",
        degraded_to="no_metrics_counters",
        error=str(_metrics_exc)[:100],
    )
    cache_stampede_avoided_total = None  # type: ignore[assignment]
    inflight_locks_size = None  # type: ignore[assignment]


class _NullStepCtx:
    """No-op stand-in returned by ``_maybe_step`` when step_tracker is None.

    Lets call sites unconditionally invoke ``set_metadata(...)`` without an
    extra `if ctx:` guard at every site (see ``_find_similar_impl``).
    """

    def set_metadata(self, **_kwargs: object) -> None:
        return None


_NULL_STEP_CTX = _NullStepCtx()


class PgSemanticCache(SemanticCachePort):
    """2-tier semantic cache backed by pgvector.

    Fast path: exact SHA256 hash match on normalised query text.
    Slow path: cosine similarity via HNSW index on query_embedding.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        threshold: float = SEMANTIC_CACHE_THRESHOLD,
    ) -> None:
        self._sf = session_factory
        self._threshold = threshold
        # In-process single-flight locks keyed by (bot, hash). When N
        # coroutines race for the same query miss, only the first acquires
        # the lock and queries the DB; the rest await the lock (bounded by
        # DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S) and re-check the cache,
        # avoiding a thundering-herd against pgvector. Cluster-wide
        # protection (across worker processes) needs a Redis SETNX lock —
        # this in-process layer is the cheap first line.
        #
        # WeakValueDictionary so an unused lock is garbage-collected once
        # no caller holds a strong reference (i.e. when the last
        # ``async with lock`` block exits). Prevents unbounded growth
        # for a long-running process with high-cardinality keys.
        self._inflight_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    @staticmethod
    def _query_hash(query_text: str) -> str:
        return hashlib.sha256(query_text.strip().lower().encode("utf-8")).hexdigest()

    def _get_inflight_lock(self, key: str) -> asyncio.Lock:
        """Return (lazy-create) the in-process Lock for ``key``.

        Caller MUST bind the returned lock to a local variable before
        any ``await`` so the WeakValueDictionary entry stays alive for
        the critical section. Once no caller holds a strong reference
        the entry is collected — bounding the dict by the live working
        set, not by lifetime cardinality.
        """
        lock = self._inflight_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight_locks[key] = lock
        if inflight_locks_size is not None:
            try:
                inflight_locks_size.labels(pool="semantic_cache").set(
                    len(self._inflight_locks),
                )
            except (ValueError, RuntimeError):
                # Metrics never break pipeline (label mismatch / shutdown).
                pass
        return lock

    async def find_similar(
        self,
        query_embedding: list[float],
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        bot_version: BotVersion,
        corpus_version: CorpusVersion,
        threshold: float = SEMANTIC_CACHE_THRESHOLD,
        embedding_column: str | None = None,
    ) -> CachedResponse | None:
        # We need query_text for hash lookup — derive from embedding is impossible,
        # so hash path requires store() to have been called with same normalised text.
        # For hash path we need the actual query; the caller only passes embedding.
        # We'll skip hash path here and rely on cosine similarity.
        # However, to support hash path we accept an optional _query_text via a
        # wrapper — see find_similar_with_text below.
        return await self._find_similar_impl(
            query_embedding=query_embedding,
            query_text=None,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            bot_version=bot_version,
            corpus_version=corpus_version,
            threshold=threshold,
            embedding_column=embedding_column,
        )

    async def find_similar_with_text(
        self,
        query_embedding: list[float],
        query_text: str,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        bot_version: BotVersion,
        corpus_version: CorpusVersion,
        threshold: float = SEMANTIC_CACHE_THRESHOLD,
        step_tracker: object | None = None,
        embedding_column: str | None = None,
        redis_client: object | None = None,
    ) -> CachedResponse | None:
        """Extended lookup that also tries exact hash match on query_text.

        Single-flight with two-layer locking. 1000 concurrent identical
        queries that all miss should issue ONE pgvector query, not 1000.

        Layer 1 — cross-process (preferred, when ``redis_client`` provided):
            ``SET <key> 1 NX EX <ttl>`` is the canonical Redis-backed mutex.
            The acquiring worker performs the lookup + ``store()``; other
            workers (different uvicorn process or different event loop)
            see ``SET NX`` return ``None``, sleep ``DEFAULT_SEMANTIC_CACHE_
            WAIT_RETRY_S``, then re-enter — by which time the holder has
            populated the cache and the waiter hits the exact-hash fast
            path. The Redis key auto-expires after
            ``DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S`` so a crashed holder
            cannot deadlock the pool.

        Layer 2 — in-process (fallback, when Redis absent or raised):
            ``asyncio.Lock`` keyed on (bot_id, query_hash) serialises
            coroutines inside ONE event loop. Adequate for single-worker
            deploys; preserved as graceful degrade so a transient Redis
            outage never breaks chat (per CLAUDE.md graceful-degradation).
        """
        qhash = self._query_hash(query_text)

        # --- Layer 1: cross-process Redis lock -----------------------------
        if redis_client is not None:
            lock_key = f"ragbot:cache:lock:{record_bot_id}:{qhash}"
            try:
                acquired = await redis_client.set(
                    lock_key,
                    "1",
                    nx=True,
                    ex=DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S,
                )
            except Exception as exc:  # noqa: BLE001 — Redis aux must not break chat
                # Redis outage: degrade silently to the in-process lock.
                # Sacred path (chat) must never fail because Redis is down.
                logger.warning(
                    "semantic_cache_redis_lock_unavailable",
                    bot_id=str(record_bot_id),
                    hash=qhash[:12],
                    error=type(exc).__name__,
                )
                acquired = None  # forces fallback to in-process path below

            if acquired:
                # Winner — perform lookup, then release the lock so the next
                # query proceeds without waiting for TTL expiry.
                try:
                    return await self._find_similar_impl(
                        query_embedding=query_embedding,
                        query_text=query_text,
                        record_tenant_id=record_tenant_id,
                        record_bot_id=record_bot_id,
                        bot_version=bot_version,
                        corpus_version=corpus_version,
                        threshold=threshold,
                        step_tracker=step_tracker,
                        embedding_column=embedding_column,
                    )
                finally:
                    try:
                        await redis_client.delete(lock_key)
                    except Exception:  # noqa: BLE001 — release best-effort
                        # Worst case the TTL reaps the lock; subsequent
                        # waiters wait one extra retry tick. Do NOT raise.
                        pass
            elif acquired is None:
                # SET raised earlier (Redis transient) — fall through to the
                # in-process path so the request still completes.
                pass
            else:
                # Loser: another worker holds the lock. Bump the stampede-
                # avoided counter, sleep, then recurse. The recursive call
                # is bounded by the TTL — once the holder writes the row
                # (or the lock auto-expires) the next attempt either hits
                # the exact-hash fast path or wins the lock itself.
                if cache_stampede_avoided_total is not None:
                    try:
                        cache_stampede_avoided_total.labels(
                            cache_name="semantic",
                        ).inc()
                    except (ValueError, RuntimeError):
                        pass
                await asyncio.sleep(DEFAULT_SEMANTIC_CACHE_WAIT_RETRY_S)
                return await self.find_similar_with_text(
                    query_embedding=query_embedding,
                    query_text=query_text,
                    record_tenant_id=record_tenant_id,
                    record_bot_id=record_bot_id,
                    bot_version=bot_version,
                    corpus_version=corpus_version,
                    threshold=threshold,
                    step_tracker=step_tracker,
                    embedding_column=embedding_column,
                    redis_client=redis_client,
                )

        # --- Layer 2: in-process asyncio.Lock (fallback) -------------------
        lock_key = f"{record_bot_id}:{qhash}"
        lock = self._get_inflight_lock(lock_key)

        # Fast path: lock free → take it, fetch, return.
        if not lock.locked():
            async with lock:
                return await self._find_similar_impl(
                    query_embedding=query_embedding,
                    query_text=query_text,
                    record_tenant_id=record_tenant_id,
                    record_bot_id=record_bot_id,
                    bot_version=bot_version,
                    corpus_version=corpus_version,
                    threshold=threshold,
                    step_tracker=step_tracker,
                    embedding_column=embedding_column,
                )

        # Slow path: another coroutine is already fetching the same key.
        # Wait for it (with timeout), then re-check; the writer should have
        # populated the cache by then.
        if cache_stampede_avoided_total is not None:
            try:
                cache_stampede_avoided_total.labels(cache_name="semantic").inc()
            except (ValueError, RuntimeError):
                # prometheus client may raise on label mismatch / shutdown;
                # metrics never break pipeline.
                pass
        try:
            await asyncio.wait_for(
                lock.acquire(),
                timeout=DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "semantic_cache_stampede_timeout",
                bot_id=str(record_bot_id),
                hash=qhash[:12],
            )
            # Fallback: independent fetch (caller still gets correct data).
            return await self._find_similar_impl(
                query_embedding=query_embedding,
                query_text=query_text,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                bot_version=bot_version,
                corpus_version=corpus_version,
                threshold=threshold,
                step_tracker=step_tracker,
                embedding_column=embedding_column,
            )
        try:
            return await self._find_similar_impl(
                query_embedding=query_embedding,
                query_text=query_text,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                bot_version=bot_version,
                corpus_version=corpus_version,
                threshold=threshold,
                step_tracker=step_tracker,
                embedding_column=embedding_column,
            )
        finally:
            lock.release()

    async def _find_similar_impl(
        self,
        *,
        query_embedding: list[float],
        query_text: str | None,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        bot_version: BotVersion,
        corpus_version: CorpusVersion,
        threshold: float = SEMANTIC_CACHE_THRESHOLD,
        step_tracker: object | None = None,
        embedding_column: str | None = None,
    ) -> CachedResponse | None:
        effective_threshold = threshold or self._threshold
        # Resolve cache-table column from caller hint (data column name) and
        # validate against the whitelist before f-string substitution.
        # The mapper currently always returns ``query_embedding``; the
        # indirection keeps a future multi-column expansion local.
        col = _validate_query_embedding_column(
            _data_to_cache_column(embedding_column),
        )

        # Phase B instrumentation: when caller provides a step_tracker, emit
        # a dedicated row per SQL phase so analyzers can split exact-hash
        # latency from pgvector cosine latency. No-op when tracker is None.
        # The cache itself does NOT depend on the tracker class — it only
        # invokes ``.step(name)`` if attribute exists, keeping DI surface clean.
        _step = getattr(step_tracker, "step", None) if step_tracker is not None else None

        @asynccontextmanager
        async def _maybe_step(name: str):
            """Yield a step ctx when tracker present, else a no-op stub."""
            if _step is None:
                yield _NULL_STEP_CTX
                return
            async with _step(name) as ctx:
                yield ctx

        async with self._sf() as session:
            # --- Fast path: exact hash match ---
            if query_text:
                qhash = self._query_hash(query_text)
                # SECURITY: strict tenant scoping; NULL-tenant legacy rows are
                # ignored at read time and soft-expired by ``_cleanup_null_rows``.
                async with _maybe_step("hash_lookup_cache") as h_ctx:
                    result = await session.execute(
                        text("""
                            SELECT answer, citations, model_name, cached_at_ts, metadata_json
                            FROM semantic_cache
                            WHERE record_bot_id = :record_bot_id
                              AND record_tenant_id = :record_tenant_id
                              AND query_hash = :hash
                              AND bot_version = :bv
                              AND corpus_version = :cv
                              AND (expires_at IS NULL OR expires_at > now())
                            ORDER BY created_at DESC
                            LIMIT 1
                        """),
                        {
                            "record_bot_id": str(record_bot_id),
                            "record_tenant_id": str(record_tenant_id),
                            "hash": qhash,
                            "bv": str(bot_version),
                            "cv": str(corpus_version),
                        },
                    )
                    row = result.mappings().first()
                    h_ctx.set_metadata(hit=bool(row), source="exact_hash")
                if row:
                    if DEFAULT_SEMANTIC_CACHE_HIT_LOG_ENABLED:
                        # Exact-hash path: similarity == 1.0 by construction
                        # (SHA256 hash match on normalised query text). Emit
                        # threshold_active so the diagnostic harness can
                        # distinguish hash hits from cosine hits at any
                        # threshold value.
                        logger.info(
                            "semantic_cache_hit",
                            source="exact_hash",
                            bot_id=str(record_bot_id),
                            query_hash=qhash[:12],
                            similarity_score=1.0,
                            threshold_active=float(effective_threshold),
                        )
                    return CachedResponse(
                        answer=row["answer"],
                        citations=list(row["citations"] or []),
                        model_name=row["model_name"] or "",
                        cached_at_ts=int(row["cached_at_ts"] or 0),
                        chunks=tuple((row["metadata_json"] or {}).get("chunks") or ()),
                    )

            # --- Slow path: cosine similarity via pgvector ---
            if not query_embedding:
                return None

            # asyncpg-safe: use CAST(:emb AS vector) instead of :emb::vector.
            # The double-colon ``::`` cast operator confuses asyncpg's
            # parameter parser (it sees ``:emb`` then ``:vector`` as two
            # placeholders), so we go through standard SQL CAST().
            #
            # The column name ``col`` is whitelisted above; SQL injection
            # from an attacker-controlled ``embedding_column`` is impossible
            # because only known column names are permitted.
            async with _maybe_step("semantic_cache_check") as s_ctx:
                result = await session.execute(
                    text(f"""
                        SELECT answer, citations, model_name, cached_at_ts, metadata_json,
                               1 - ({col} <=> CAST(:emb AS vector)) AS score
                        FROM semantic_cache
                        WHERE record_bot_id = :record_bot_id
                          AND record_tenant_id = :record_tenant_id
                          AND bot_version = :bv
                          AND corpus_version = :cv
                          AND (expires_at IS NULL OR expires_at > now())
                          AND {col} IS NOT NULL
                          AND 1 - ({col} <=> CAST(:emb AS vector)) >= :threshold
                        ORDER BY {col} <=> CAST(:emb AS vector)
                        LIMIT 1
                    """),
                    {
                        "record_bot_id": str(record_bot_id),
                        "record_tenant_id": str(record_tenant_id),
                        "emb": str(query_embedding),
                        "bv": str(bot_version),
                        "cv": str(corpus_version),
                        "threshold": effective_threshold,
                    },
                )
                row = result.mappings().first()
                s_ctx.set_metadata(
                    hit=bool(row),
                    threshold=float(effective_threshold),
                    score=float(row["score"]) if row else 0.0,
                    embedding_column=col,
                )
            if row:
                if DEFAULT_SEMANTIC_CACHE_HIT_LOG_ENABLED:
                    # Cosine path: similarity_score is the observed cosine
                    # score; threshold_active is the cut-off applied at the
                    # SQL ``>= :threshold`` clause. ``score`` kept as alias
                    # for backward-compat with existing dashboards.
                    logger.info(
                        "semantic_cache_hit",
                        source="cosine_sim",
                        score=float(row["score"]),
                        similarity_score=float(row["score"]),
                        threshold_active=float(effective_threshold),
                        bot_id=str(record_bot_id),
                    )
                return CachedResponse(
                    answer=row["answer"],
                    citations=list(row["citations"] or []),
                    model_name=row["model_name"] or "",
                    cached_at_ts=int(row["cached_at_ts"] or 0),
                    chunks=tuple((row["metadata_json"] or {}).get("chunks") or ()),
                )

            return None

    async def store(
        self,
        *,
        query: str,
        query_embedding: list[float],
        response: CachedResponse,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        workspace_id: str,
        bot_version: BotVersion,
        corpus_version: CorpusVersion,
        ttl_s: int = 3600,
        embedding_column: str | None = None,
    ) -> None:
        # SECURITY: refuse to write a NULL-tenant row. Skip-and-warn rather
        # than raise so a missing tenant doesn't fail user-facing chat.
        if record_tenant_id is None:
            logger.warning(
                "semantic_cache.store skipped: record_tenant_id is None",
                record_bot_id=str(record_bot_id),
                bot_version=str(bot_version),
                corpus_version=str(corpus_version),
            )
            return
        qhash = self._query_hash(query)
        expires_clause = f"now() + interval '{int(ttl_s)} seconds'" if ttl_s else "NULL"
        # Route INSERT to the column matching the bot's embedding spec.
        # Whitelisted name → safe to interpolate into SQL
        # (see ``_validate_query_embedding_column`` above).
        col = _validate_query_embedding_column(
            _data_to_cache_column(embedding_column),
        )

        async with self._sf() as session:
            # asyncpg-safe: CAST(:p AS T) instead of :p::T (see find note above).
            # 2026-05-27 — also persist chunks snapshot into metadata_json so
            # cache_hit responses can rebuild API ``sources`` (RAGAS judge,
            # audit tools). Snapshot capped at first 8 chunks × ~2KB preview
            # to keep row size bounded (semantic_cache rows shouldn't bloat
            # pgvector index scans). chunks shape mirrors graded_chunks: keys
            # document_name, source_url, chunk_index, score, content.
            _chunks_snap = [dict(c) for c in (response.chunks or ())][:8]
            await session.execute(
                text(f"""
                    INSERT INTO semantic_cache
                        (record_bot_id, record_tenant_id, workspace_id, bot_version, corpus_version,
                         {col}, query_hash, answer, citations,
                         model_name, cached_at_ts, metadata_json, expires_at)
                    VALUES
                        (:record_bot_id, :tid, :ws, :bv, :cv,
                         CAST(:emb AS vector), :hash, :answer, CAST(:citations AS jsonb),
                         :model_name, :cached_at_ts, CAST(:metadata_json AS jsonb), {expires_clause})
                """),
                {
                    "record_bot_id": str(record_bot_id),
                    "tid": str(record_tenant_id),
                    "ws": workspace_id,
                    "bv": str(bot_version),
                    "cv": str(corpus_version),
                    "emb": str(query_embedding) if query_embedding else None,
                    "hash": qhash,
                    "answer": response.answer,
                    "citations": json.dumps(response.citations or []),
                    "model_name": response.model_name or "",
                    "cached_at_ts": response.cached_at_ts or int(time.time()),
                    "metadata_json": json.dumps({"chunks": _chunks_snap}) if _chunks_snap else None,
                },
            )
            await session.commit()


__all__ = ["PgSemanticCache"]
