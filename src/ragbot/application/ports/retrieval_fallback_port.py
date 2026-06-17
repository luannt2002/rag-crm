"""RetrievalFallbackPort — contract for multi-stage retrieval fallback strategies.

Phase-2 (Stream S8) adds a chain of pluggable retrieval stages that the
``retrieve`` node can walk through until enough high-confidence chunks
emerge. Each stage gets the original query plus the prior stage's result
(so e.g. a parent-expand stage can lift the previous BM25-only hit list
to its parent chunks).

Contract is intentionally narrow:
- Stateless ``retrieve`` returning the same chunk-dict shape that
  ``query_graph._run_hybrid_for_query`` already produces (so the wrapper
  can swap fallback stages in/out without touching downstream nodes).
- Default OFF: Null Object (``NullRetrievalStage``) returns ``[]`` when
  the system-config flag ``retrieval_multistage_enabled`` is ``false``.

Implementations live in ``infrastructure/retrieval_fallback/`` and are
registered in ``infrastructure/retrieval_fallback/registry.py``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class RetrievalFallbackPort(Protocol):
    """Retrieval stage abstraction.

    Each stage is an idempotent callable: same input -> same output, no
    cross-turn state. The orchestrator decides when to early-exit based
    on the score of the highest chunk returned.
    """

    @property
    def stage_name(self) -> str:
        """Identifier for observability (e.g. ``"hybrid_stage1"``)."""
        ...

    async def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float],
        record_bot_id: UUID,
        top_k: int,
        prior_stage_result: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return candidate chunks for this stage.

        @param query: user query (after rewrite / abbreviation expansion).
        @param query_embedding: dense vector matching the active embedding
            column. Empty list = embedder dead; stages can still serve BM25.
        @param record_bot_id: tenant-scoped bot UUID for ``document_chunks`` filter.
        @param top_k: maximum chunks to return.
        @param prior_stage_result: chunks emitted by the previous stage in
            the chain. ``None`` on the first stage. Stages that extend the
            prior result (e.g. parent-expand) consume this.
        @param kwargs: stage-specific extras (vector_store handle,
            session_factory, channel_type, etc.) — kept generic so the
            wrapper can pass everything without changing the Port.
        @return: list of chunk dicts with at minimum
            ``{"chunk_id", "content", "text", "score"}``. Empty list when
            the stage has nothing to contribute (NOT an error).
        """
        ...


__all__ = ["RetrievalFallbackPort"]
