"""Stage-3 — structural keyword filter.

For corpora with strong structural anchors (legal documents with
"Điều N", "Khoản M", "Chương X"; tariffs with "Mục", "Điểm"; product
catalogues with "SKU"), neither dense embeddings nor BM25 tokenisation
always preserves the anchor as a discrete token. Stage 3 falls back to
a regex match on the original (un-segmented) content column, returning
chunks whose surface text contains the same anchor the query asks for.

Default pattern: ``DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN`` from
``shared/constants.py``. Bot owners override via per-bot pipeline_config
(``retrieval_keyword_stage_pattern``); operators override globally via
``system_config.retrieval_keyword_stage_pattern``.

Domain-neutral: any corpus whose query carries an anchor matching the
configured regex benefits. Empty match -> stage returns [] and the chain
falls through.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sa_text

from ragbot.shared.constants import (
    DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN,
    DEFAULT_RETRIEVAL_KEYWORD_STAGE_SCORE,
    DEFAULT_TOP_K,
)
from ragbot.shared.errors import RetrievalError

logger = structlog.get_logger(__name__)


class KeywordStage3Retriever:
    """Regex-anchor lookup over ``document_chunks.content``."""

    def __init__(self, *, pattern: str | None = None, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._pattern_str = pattern or DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN
        try:
            self._pattern = re.compile(self._pattern_str)
        except re.error:
            logger.warning(
                "keyword_stage3_invalid_pattern",
                pattern=self._pattern_str,
            )
            self._pattern = re.compile(DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN)

    @property
    def stage_name(self) -> str:
        return "keyword_stage3"

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
            logger.debug("keyword_stage3_no_session_factory")
            return []
        if not query or not query.strip():
            return []
        if record_bot_id is None:
            logger.warning("keyword_stage3_missing_record_bot_id")
            return []

        # Bot owner may override the pattern at call time via kwargs.
        runtime_pat = kwargs.get("keyword_pattern")
        pattern = self._pattern
        if runtime_pat and isinstance(runtime_pat, str):
            try:
                pattern = re.compile(runtime_pat)
            except re.error:
                logger.warning("keyword_stage3_runtime_pattern_invalid", pat=runtime_pat)

        match = pattern.search(query)
        if not match:
            logger.debug("keyword_stage3_no_anchor_in_query")
            return []
        anchor = match.group(0)

        # Escape LIKE wildcards in the captured anchor.
        like_pat = (
            f"%{anchor.replace(chr(92), chr(92) + chr(92)).replace('%', '\\%').replace('_', '\\_')}%"
        )

        sql = sa_text(
            """
            SELECT dc.id, dc.record_document_id, dc.chunk_index,
                   dc.content, dc.metadata_json
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.record_document_id
            WHERE d.record_bot_id = :rbid
              AND d.deleted_at IS NULL
              AND dc.content ILIKE :like_pat ESCAPE '\\'
            ORDER BY dc.chunk_index ASC
            LIMIT :top_k
            """
        )

        try:
            async with session_factory() as session:
                result = await session.execute(
                    sql,
                    {
                        "rbid": record_bot_id,
                        "like_pat": like_pat,
                        "top_k": int(top_k),
                    },
                )
                rows = result.mappings().all()
        except (RetrievalError, ValueError, TypeError):
            logger.warning("keyword_stage3_query_failed", exc_info=True)
            return []
        except Exception:  # noqa: BLE001 — DB driver heterogeneity; never crash retrieve
            logger.warning("keyword_stage3_unexpected_error", exc_info=True)
            return []

        # Static recall score — chain consumer typically reranks downstream.
        # Held below the early-exit threshold so a keyword-only match never
        # short-circuits the chain (see DEFAULT_RETRIEVAL_KEYWORD_STAGE_SCORE doc).
        keyword_score = DEFAULT_RETRIEVAL_KEYWORD_STAGE_SCORE
        return [
            {
                "chunk_id": str(r["id"]),
                "document_id": str(r["record_document_id"]),
                "chunk_index": r["chunk_index"],
                "content": r["content"],
                "text": r["content"],
                "score": keyword_score,
                "metadata": dict(r["metadata_json"] or {}),
                "stage": "keyword_stage3",
                "anchor": anchor,
            }
            for r in rows
        ]


__all__ = ["KeywordStage3Retriever"]
