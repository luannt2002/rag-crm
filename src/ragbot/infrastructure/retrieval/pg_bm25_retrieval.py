"""PgBM25Retrieval — Postgres ``tsvector`` + GIN BM25-approx adapter.

Reads from ``document_chunks.search_vector`` (populated by the alembic
0028 trigger ``trg_chunk_search_vector``). Tenant isolation via a JOIN
on ``documents.record_bot_id`` (``document_chunks`` itself does not carry
``record_bot_id`` post-0034 column drop). Soft-deleted documents are
excluded via ``documents.deleted_at IS NULL``.

The query side uses ``websearch_to_tsquery('simple', ...)`` so the
caller can pass phrase-quoted text, ``-negation`` and ``OR`` operators
without an explicit parser pass. ``ts_rank_cd`` (cover density) is the
canonical BM25-approx rank function in Postgres FTS.

When the caller passes ``cr_enhanced=True`` (lifted from per-bot
``plan_limits.cr_enhanced_enabled``) the adapter re-tokenizes
``content || ' ' || coalesce(chunk_context, '')`` on the fly so the
BM25 surface includes the situated-context string written by the
Anthropic Contextual Retrieval enricher (alembic 010l). The functional
GIN index ``idx_chunks_search_vector_combined`` (alembic 010n) keeps
this path fast; bots that have not opted in keep the legacy
``dc.search_vector`` indexed path bit-exact.

Fail-soft: any DB / driver exception is swallowed and logged — lexical
retrieval is an auxiliary signal next to the vector branch and must
NEVER take down the retrieve node (see CLAUDE.md graceful-degradation).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sa_text

from ragbot.shared.constants import (
    DEFAULT_BM25_NORMALIZATION_FLAGS,
    DEFAULT_LEXICAL_TOP_K,
)
from ragbot.shared.errors import RetrievalError

logger = structlog.get_logger(__name__)


class PgBM25Retrieval:
    """Postgres tsvector BM25-approx adapter (LexicalRetrievalPort).

    @param session_factory: async SQLAlchemy session factory (sync callable
        returning an ``AsyncSession`` context manager). Injected at DI
        boot time so the adapter is decoupled from the engine wiring.
    @param normalization_flags: ts_rank_cd bitmask (0..63). Default mirrors
        the platform-wide ``DEFAULT_BM25_NORMALIZATION_FLAGS`` so admin
        tuning lives in ``shared/constants.py`` only.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        normalization_flags: int = DEFAULT_BM25_NORMALIZATION_FLAGS,
    ) -> None:
        self._sf = session_factory
        # Clamp to bitmask range (0..63) up-front so the SQL f-string stays
        # safe-by-construction (no bind-param for SET-style scalars).
        self._norm = max(0, min(int(normalization_flags), 63))

    @staticmethod
    def get_provider_name() -> str:
        return "pg_textsearch"

    async def search(
        self,
        query: str,
        record_bot_id: UUID,
        top_k: int = DEFAULT_LEXICAL_TOP_K,
        cr_enhanced: bool = False,
    ) -> list[dict[str, Any]]:
        """Run BM25-approx tsvector search scoped to ``record_bot_id``.

        @param cr_enhanced: when True the tsvector is rebuilt at query
            time over ``content || ' ' || coalesce(chunk_context, '')``
            so BM25 sees the Anthropic-CR situated-context string written
            by ``ChunkContextEnricher``. The functional GIN index added
            in alembic 010n (``idx_chunks_search_vector_combined``) keeps
            this path fast. NULL ``chunk_context`` rows fall back to
            ``content``-only tokens via ``coalesce`` — no exclusion.
            Default ``False`` preserves the legacy indexed path bit-exact.
        """
        if not query or not query.strip():
            return []
        if record_bot_id is None:
            logger.warning("pg_bm25_missing_record_bot_id")
            return []

        # Tsvector surface — opt-in path widens to (content + chunk_context)
        # so the BM25 rank covers the situated-context tokens. Legacy path
        # keeps the indexed ``dc.search_vector`` column so query plans on
        # opted-out bots are byte-identical to pre-CR shipping.
        if cr_enhanced:
            _ts_expr = (
                "to_tsvector('simple', "
                "coalesce(dc.content, '') || ' ' || "
                "coalesce(dc.chunk_context, ''))"
            )
        else:
            _ts_expr = "dc.search_vector"

        sql = sa_text(
            f"""
            SELECT dc.id, dc.record_document_id, dc.chunk_index,
                   dc.content, dc.metadata_json,
                   ts_rank_cd({_ts_expr},
                              websearch_to_tsquery('simple', :query),
                              {self._norm}) AS score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.record_document_id
            WHERE d.record_bot_id = :rbid
              AND d.deleted_at IS NULL
              AND {_ts_expr} @@ websearch_to_tsquery('simple', :query)
            ORDER BY score DESC
            LIMIT :top_k
            """,
        )
        try:
            async with self._sf() as session:
                result = await session.execute(
                    sql,
                    {
                        "query": query,
                        "rbid": record_bot_id,
                        "top_k": int(top_k),
                    },
                )
                rows = result.mappings().all()
        except (RetrievalError, ValueError, TypeError):
            logger.warning("pg_bm25_query_failed", exc_info=True)
            return []
        # Narrow whitelist above covers SQLAlchemy + value errors; the
        # broad-except below is the driver-heterogeneity escape valve
        # (asyncpg / psycopg subclasses outside our narrow set). Lexical
        # retrieval is auxiliary — never crash the retrieve node.
        except Exception:  # noqa: BLE001 — DB driver heterogeneity; never crash retrieve
            logger.warning("pg_bm25_unexpected_error", exc_info=True)
            return []

        return [
            {
                "chunk_id": str(r["id"]),
                "document_id": str(r["record_document_id"]),
                "chunk_index": r["chunk_index"],
                "content": r["content"],
                "text": r["content"],
                "score": float(r["score"]) if r["score"] is not None else 0.0,
                "metadata": dict(r["metadata_json"] or {}),
                "source": "lexical",
            }
            for r in rows
        ]

    async def health_check(self) -> bool:
        """Lightweight SELECT 1 to verify the session factory is alive."""
        try:
            async with self._sf() as session:
                await session.execute(sa_text("SELECT 1"))
        except Exception:  # noqa: BLE001 — health probe; bool result swallows
            return False
        return True


__all__ = ["PgBM25Retrieval"]
