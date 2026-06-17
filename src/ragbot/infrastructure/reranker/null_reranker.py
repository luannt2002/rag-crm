"""NullReranker — Null Object pattern for the reranker strategy.

Used as the default when the operator has not configured a reranker provider
(`reranker_provider="null"` in system_config). Returns the input chunks
truncated to top_n with their existing scores intact so the rerank node in
``query_graph`` keeps working without any provider call.

Caller contract (mirrors ``LiteLLMReranker.rerank``):
    rerank(query, chunks, *, top_n, model=None) -> list[chunk_dict]

Selecting NullReranker via the registry is a *deliberate* operator choice;
``query_graph.rerank`` still emits ``mode="rerank"`` (provider == null),
so the bypass is observable in audit logs without surfacing as a misconfig
warning.
"""

from __future__ import annotations

from typing import Any

import structlog

from ragbot.shared.constants import DEFAULT_RERANK_TOP_N

logger = structlog.get_logger(__name__)


class NullReranker:
    """No-op reranker — returns chunks in retrieval order, top_n only."""

    def __init__(self, *, model: str | None = None) -> None:
        # `model` accepted purely so the registry can build NullReranker with
        # the same kwargs as a real provider. Stored for `get_provider_name`.
        self._model = model

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    @property
    def mode(self) -> str:
        """Observability identifier matching RerankerPort.mode."""
        return "null"

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = DEFAULT_RERANK_TOP_N,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the first ``top_n`` chunks unchanged.

        Score remains whatever the retrieval stage produced (RRF). No provider
        call is made — this is the operator-disabled default.
        """
        if not chunks:
            return []
        # Preserve retrieval order; do not mutate scores so downstream
        # `reranker_min_score` filter still applies to the original RRF score.
        logger.debug(
            "null_reranker_bypass",
            input=len(chunks),
            top_n=top_n,
        )
        return list(chunks[:top_n])

    async def health_check(self) -> bool:
        # Always healthy — the null implementation has no external dependency.
        return True

    async def close(self) -> None:
        return None


__all__ = ["NullReranker"]
