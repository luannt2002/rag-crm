"""Stage-4 — parent-chunk expansion.

When the prior stages match a tight child chunk but the answer requires
broader context (e.g. a paragraph that cites Điều 8 in passing but the
full clause sits in the parent), Stage 4 fetches each child chunk's
``parent_chunk_id`` and emits parent chunks as additional candidates.

Distinguishing feature from the inline ``parent_child_enabled`` block in
``query_graph.retrieve``:
- Inline block **swaps** child for parent.
- This stage **appends** parents to the candidate pool so reranking
  can choose between child-precision and parent-context.

If no chunk in the prior result carries a ``parent_chunk_id``, the stage
is a no-op (returns the prior result unchanged so the chain continues).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sa_text

from ragbot.shared.constants import DEFAULT_TOP_K
from ragbot.shared.errors import RetrievalError

logger = structlog.get_logger(__name__)


class ParentExpandStage4Retriever:
    """Append parent chunks for any child in the prior stage's result."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    @property
    def stage_name(self) -> str:
        return "parent_expand_stage4"

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
            logger.debug("parent_expand_stage4_no_session_factory")
            return list(prior_stage_result or [])
        if not prior_stage_result:
            logger.debug("parent_expand_stage4_no_prior_result")
            return []
        if record_bot_id is None:
            return list(prior_stage_result)

        parent_ids = [
            c.get("parent_chunk_id")
            for c in prior_stage_result
            if c.get("parent_chunk_id")
        ]
        # Deduplicate preserving order.
        seen: set[str] = set()
        unique_parent_ids: list[Any] = []
        for pid in parent_ids:
            spid = str(pid)
            if spid not in seen:
                seen.add(spid)
                unique_parent_ids.append(pid)

        if not unique_parent_ids:
            logger.debug("parent_expand_stage4_no_parent_links")
            return list(prior_stage_result)

        try:
            async with session_factory() as session:
                result = await session.execute(
                    sa_text(
                        """
                        SELECT dc.id, dc.record_document_id, dc.chunk_index,
                               dc.content, dc.metadata_json
                        FROM document_chunks dc
                        JOIN documents d ON d.id = dc.record_document_id
                        WHERE dc.id = ANY(:ids) AND d.record_bot_id = :rbid
                        """
                    ),
                    {"ids": unique_parent_ids, "rbid": record_bot_id},
                )
                rows = result.mappings().all()
        except (RetrievalError, ValueError, TypeError):
            logger.warning("parent_expand_stage4_query_failed", exc_info=True)
            return list(prior_stage_result)
        except Exception:  # noqa: BLE001 — DB driver heterogeneity; never crash retrieve
            logger.warning("parent_expand_stage4_unexpected_error", exc_info=True)
            return list(prior_stage_result)

        # Anchor the parent score off the max score in the prior result so
        # the chain does NOT prematurely early-exit on a parent chunk.
        prior_max_score = max(
            (float(c.get("score", 0) or 0) for c in prior_stage_result),
            default=0.0,
        )
        parents: list[dict[str, Any]] = [
            {
                "chunk_id": str(r["id"]),
                "document_id": str(r["record_document_id"]),
                "chunk_index": r["chunk_index"],
                "content": r["content"],
                "text": r["content"],
                "score": prior_max_score,
                "metadata": dict(r["metadata_json"] or {}),
                "stage": "parent_expand_stage4",
                "is_parent_expanded": True,
            }
            for r in rows
        ]

        # Merge: prior result first, then parents (avoid duplicate chunk_ids).
        existing_ids = {str(c.get("chunk_id") or c.get("id") or "") for c in prior_stage_result}
        merged: list[dict[str, Any]] = list(prior_stage_result)
        for p in parents:
            if p["chunk_id"] not in existing_ids:
                merged.append(p)
                existing_ids.add(p["chunk_id"])

        # Cap to top_k so the chain doesn't explode.
        return merged[: int(top_k)]


__all__ = ["ParentExpandStage4Retriever"]
