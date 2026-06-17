"""Stage-1 — hybrid vector + BM25.

Mirrors the single-shot retrieve that ``query_graph.retrieve`` already runs.
The wrapper exists so the chain (stage1 -> stage2 -> ... -> stageN) is
uniform: every stage implements the same Port and returns chunk dicts in
the same shape.

This stage is **always the first** in the default chain and is the only
one that requires a non-empty ``query_embedding``. Subsequent stages are
purpose-built fallbacks that can run even when the embedder is dead
(stage 2 = BM25-only) or when the prior stage retrieved low-score chunks
that need parent-chunk lift (stage 4).

Dependencies (passed via ``kwargs`` from orchestrator):
- ``vector_store`` — pgvector store implementing ``hybrid_search``.
- ``channel_type`` (optional) — propagated to ``hybrid_search`` when the
  backend accepts it (port variant); legacy backends ignore.
- ``embedding_column`` (optional) — runtime-resolved column matching the
  embedder dimension.
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID

import structlog

from ragbot.shared.constants import DEFAULT_TOP_K
from ragbot.shared.errors import RetrievalError

logger = structlog.get_logger(__name__)


class HybridStage1Retriever:
    """Vector + BM25 hybrid search wrapper (RRF-merged at the DB layer)."""

    def __init__(self, **kwargs: Any) -> None:
        # Stateless — kwargs accepted for registry compat.
        self._kwargs = kwargs

    @property
    def stage_name(self) -> str:
        return "hybrid_stage1"

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
        vector_store = kwargs.get("vector_store")
        if vector_store is None or not hasattr(vector_store, "hybrid_search"):
            logger.debug("hybrid_stage1_no_vector_store")
            return []
        if not query_embedding:
            logger.debug("hybrid_stage1_no_embedding")
            return []

        # Probe signature so we send only kwargs the backend understands.
        # A ``**kwargs`` (VAR_KEYWORD) parameter means the backend will accept
        # anything we send, so skip the per-key filter entirely.
        sig = inspect.signature(vector_store.hybrid_search)
        params = set(sig.parameters.keys())
        accepts_anything = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )

        def _accepted(name: str) -> bool:
            return accepts_anything or name in params

        hs_kwargs: dict[str, Any] = {
            "top_k": int(top_k),
        }
        if _accepted("query_text"):
            hs_kwargs["query_text"] = query
        if _accepted("query_embedding"):
            hs_kwargs["query_embedding"] = query_embedding
        if _accepted("record_bot_id"):
            hs_kwargs["record_bot_id"] = record_bot_id
        if _accepted("channel_type") and kwargs.get("channel_type"):
            hs_kwargs["channel_type"] = kwargs["channel_type"]
        if _accepted("embedding_column") and kwargs.get("embedding_column"):
            hs_kwargs["embedding_column"] = kwargs["embedding_column"]
        if _accepted("metadata_filter") and kwargs.get("metadata_filter"):
            hs_kwargs["metadata_filter"] = kwargs["metadata_filter"]
        # mega-sprint-G1: thread tenant so SET LOCAL app.tenant_id binds for RLS.
        if _accepted("record_tenant_id") and kwargs.get("record_tenant_id") is not None:
            hs_kwargs["record_tenant_id"] = kwargs["record_tenant_id"]

        try:
            raw = await vector_store.hybrid_search(**hs_kwargs)
        except (RetrievalError, ValueError, TypeError):
            logger.warning("hybrid_stage1_failed", exc_info=True)
            return []
        return list(raw or [])


__all__ = ["HybridStage1Retriever"]
