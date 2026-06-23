"""LiteLLM-based cross-encoder reranker — supports Cohere, Jina, etc.

Dùng litellm.arerank() unified API. Model configurable qua system_config.
"""

from __future__ import annotations

import asyncio
from typing import Any

import litellm
import structlog

from ragbot.application.services.retry_policy import RetryPolicy, retry_with_backoff
from ragbot.shared.constants import (
    DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
    DEFAULT_RERANK_MODEL,
    DEFAULT_RERANK_TIMEOUT_S,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_RETRY_INITIAL_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_MS,
)

logger = structlog.get_logger(__name__)


class LiteLLMReranker:
    """Cross-encoder reranker via litellm unified API."""

    def __init__(self, model: str = DEFAULT_RERANK_MODEL) -> None:
        self._model = model

    @staticmethod
    def get_provider_name() -> str:
        # Strategy registry uses this as the audit-log + telemetry tag.
        # All LiteLLM-mediated providers (Cohere, Jina, Voyage, ...) are
        # one strategy from the platform's perspective — the underlying
        # vendor is encoded in ``self._model``.
        return "litellm"

    @property
    def mode(self) -> str:
        """Observability identifier matching RerankerPort.mode."""
        return f"litellm:{self._model}"

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = DEFAULT_RERANK_TOP_N,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank chunks with a cross-encoder model.

        Args:
            query: the user question.
            chunks: list of chunk dicts (must carry 'content' or 'text').
            top_n: number of chunks to keep after reranking.
            model: per-call model override (None = constructor default).

        Returns:
            top_n chunks sorted by descending relevance score.
        """
        if not chunks:
            return []

        effective_model = model or self._model

        # Extract text from chunks
        passages = [
            c.get("content") or c.get("text") or ""
            for c in chunks
        ]
        passages = [p for p in passages if p]

        if not passages:
            return chunks[:top_n]

        async def _call() -> list[dict[str, Any]]:
            async with asyncio.timeout(DEFAULT_RERANK_TIMEOUT_S):
                response = await litellm.arerank(
                    model=effective_model,
                    query=query,
                    documents=passages,
                    top_n=min(top_n, len(passages)),
                )

            # Map scores back to chunks. Overwrite `score` with the reranker
            # relevance so downstream filters (reranker_min_score,
            # crag_min_fallback_score) apply to the 0..1 relevance value they
            # were designed for — not the raw retrieval RRF (~0..0.033).
            # Keep the retrieval score accessible via `retrieval_score` for
            # debugging / telemetry.
            scored: list[dict[str, Any]] = []
            for result in response.results:
                idx = result.get("index", result.get("document", {}).get("index", 0))
                score = result.get("relevance_score", 0.0)
                if idx < len(chunks):
                    src = chunks[idx]
                    chunk = {
                        **src,
                        "rerank_score": score,
                        "retrieval_score": src.get("score"),
                        "score": score,
                        # Provenance tag for parity with the Jina / ZeroEntropy
                        # adapters so downstream telemetry sees which reranker
                        # produced this chunk's score.
                        "reranker_used": self.mode,
                    }
                    scored.append(chunk)

            # Sort by rerank score descending, with a deterministic tie-break
            # (D5a): higher original-retrieval score then stable chunk_index, so
            # tied rerank scores never flip order across identical calls.
            scored.sort(
                key=lambda c: (
                    -float(c.get("score", 0) or 0),
                    -float(c.get("retrieval_score") or 0),
                    int(c.get("chunk_index", 0) or 0),
                )
            )
            logger.info(
                "rerank_done",
                model=effective_model,
                input=len(chunks),
                output=len(scored),
            )
            return scored[:top_n]

        try:
            return await retry_with_backoff(
                _call,
                policy=RetryPolicy(max_attempts=DEFAULT_RETRY_MAX_ATTEMPTS, initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS, max_backoff_ms=DEFAULT_RETRY_MAX_MS),
                retryable_exceptions=(
                    OSError, ConnectionError, TimeoutError,
                    litellm.exceptions.RateLimitError,
                    litellm.exceptions.ServiceUnavailableError,
                    litellm.exceptions.APIConnectionError,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # Graceful degradation: return original order, log for alerting
            logger.warning(
                "rerank_fallback_to_original_order",
                model=effective_model,
                error=str(exc),
                input_count=len(chunks),
                top_n=top_n,
            )
            return chunks[:top_n]

    async def health_check(self) -> bool:
        """Health check — try reranking a single doc to verify API connectivity."""
        try:
            resp = await asyncio.wait_for(
                litellm.arerank(
                    model=self._model,
                    query="test",
                    documents=["health check document"],
                    top_n=1,
                ),
                timeout=DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
            )
            return bool(resp.results)
        except Exception:  # noqa: BLE001
            logger.warning("reranker_health_check_failed", model=self._model, exc_info=True)
            return False

    async def close(self) -> None:
        """Cleanup — nothing to release."""
