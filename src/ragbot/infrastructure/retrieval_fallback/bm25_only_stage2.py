"""Stage-2 — BM25-only sparse retrieval, skips the vector branch.

Stage 1 (hybrid) interleaves dense + sparse signals via RRF, which can let
a strong-but-irrelevant dense neighbour suppress an exact-keyword sparse
hit. Stage 2 strips out the dense branch entirely: only tsvector BM25
remains. This is the natural fallback when the user asks for a verbatim
quote ("Điều 8 quy định gì?") and the dense channel diluted the signal.

Runs against ``document_chunks.search_vector`` (already indexed). No
embedding required, so this is also the graceful-degradation path when
the embedder is dead.

Operates via a session_factory kwarg passed in by the orchestrator,
so we don't depend on a particular ``vector_store`` adapter shape and the
stage is composable with any RDBMS-backed corpus.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sa_text

from ragbot.shared.constants import (
    DEFAULT_BM25_NORMALIZATION_FLAGS,
    DEFAULT_TOP_K,
)
from ragbot.shared.errors import RetrievalError

logger = structlog.get_logger(__name__)


class BM25OnlyStage2Retriever:
    """BM25 tsvector search only — dense channel intentionally skipped."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    @property
    def stage_name(self) -> str:
        return "bm25_only_stage2"

    async def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float],
        record_bot_id: UUID,
        top_k: int = DEFAULT_TOP_K,
        prior_stage_result: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        session_factory = kwargs.get("session_factory")
        if session_factory is None:
            logger.debug("bm25_only_stage2_no_session_factory")
            return []
        if not query or not query.strip():
            return []
        if record_bot_id is None:
            logger.warning("bm25_only_stage2_missing_record_bot_id")
            return []

        norm_flags = int(
            kwargs.get("bm25_normalization_flags", DEFAULT_BM25_NORMALIZATION_FLAGS),
        )
        norm_flags = max(0, min(norm_flags, 63))

        sql = sa_text(
            f"""
            SELECT dc.id, dc.record_document_id, dc.chunk_index,
                   dc.content, dc.metadata_json, dc.parent_chunk_id,
                   ts_rank_cd(dc.search_vector,
                              websearch_to_tsquery('simple', :query),
                              {norm_flags}) AS score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.record_document_id
            WHERE d.record_bot_id = :rbid
              AND d.deleted_at IS NULL
              AND dc.search_vector @@ websearch_to_tsquery('simple', :query)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        try:
            async with session_factory() as session:
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
            logger.warning("bm25_only_stage2_query_failed", exc_info=True)
            return []
        # Guard a permissive broad-except only at the runtime SQL layer
        # because pgvector / asyncpg raise driver-specific subclasses that
        # are not all in our narrow whitelist. The stage must NEVER bring
        # down the retrieve node.
        except Exception:  # noqa: BLE001 — DB driver heterogeneity; never crash retrieve
            logger.warning("bm25_only_stage2_unexpected_error", exc_info=True)
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
                # Carried so stage-4 parent-expand can resolve the parent group.
                "parent_chunk_id": r["parent_chunk_id"],
                "stage": "bm25_only_stage2",
            }
            for r in rows
        ]


__all__ = ["BM25OnlyStage2Retriever"]
