"""Reranker Protocol — contract for reranker strategy implementations."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ragbot.shared.errors import RetrievalError  # noqa: F401 — re-exported for callers


@runtime_checkable
class RerankerPort(Protocol):
    """Reranker abstraction. Implementations: NullReranker, JinaReranker,
    LiteLLMReranker, ViRankerLocalReranker.

    Signature matches all 4 implementations (post-Loop-8).
    """

    @property
    def mode(self) -> str:
        """Identifier for observability (e.g. 'null', 'jina:jina-reranker-v3')."""
        ...

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = 5,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank chunks. Return top-N with rerank_score + reranker_used fields.

        @param query: user query string
        @param chunks: list of chunk dicts (must contain 'content' or 'text' key)
        @param top_n: number of chunks to return after reranking
        @param model: per-call model override (None = implementation default)
        @return: top-N chunks sorted by relevance score descending
        @raise RetrievalError: on API/network failure (caller should catch and
            fallback to NullReranker or original order)
        """
        ...

    async def health_check(self) -> bool:
        """Liveness probe. Returns True if backend is reachable."""
        ...

    async def close(self) -> None:
        """Release any persistent resources (HTTP sessions, thread pools)."""
        ...


__all__ = ["RerankerPort"]
