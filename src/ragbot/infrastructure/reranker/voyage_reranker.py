"""Voyage reranker — hosted multilingual cross-encoder reranker.

API: ``POST https://api.voyageai.com/v1/rerank``. Auth via
``Authorization: Bearer <api_key>``. Request body matches the
Cohere/Jina/ZeroEntropy rerank shape (``model``, ``query``, ``documents``,
``top_k``); the response is
``{"data": [{"index": int, "relevance_score": float}, ...]}``.

Key is sourced from the provider-agnostic ``ApiKeyPool`` via
``ApiKeyPoolFactory``, keyed by this adapter's internal
``_PROVIDER_CODE``. Falls back to ``RERANKER_VOYAGE_API_KEY`` env when no
pool is configured, matching the ZeroEntropy / Jina adapter's legacy
path so operators have a single-key bootstrap option.

Fail-soft: on transport error (timeout, 5xx, ConnectError) the adapter
raises ``RetrievalError`` and the caller falls back to ``NullReranker``
(same contract as ``ZeroEntropyReranker``). 4xx propagates immediately
and trips the active-key cooldown when 403 / 429.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog

from ragbot.application.services.retry_policy import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from ragbot.infrastructure.observability.metrics import api_key_failover_total
from ragbot.shared.api_key_pool import ApiKeyEntry, ApiKeyPool, ApiKeyPoolFactory
from ragbot.shared.constants import (
    DEFAULT_RERANK_TOP_N,
    DEFAULT_VOYAGE_RERANK_CB_FAIL_MAX,
    DEFAULT_VOYAGE_RERANK_CB_RESET_S,
    DEFAULT_VOYAGE_RERANK_DIMENSIONS,
    DEFAULT_VOYAGE_RERANK_ENDPOINT,
    DEFAULT_VOYAGE_RERANK_HEALTH_TIMEOUT_S,
    DEFAULT_VOYAGE_RERANK_MAX_ATTEMPTS,
    DEFAULT_VOYAGE_RERANK_MAX_DOCS,
    DEFAULT_VOYAGE_RERANK_MODEL,
    DEFAULT_VOYAGE_RERANK_SCORE_PRECISION,
    DEFAULT_VOYAGE_RERANK_TIMEOUT_S,
)
from ragbot.shared.errors import CircuitBreakerOpen, RetrievalError

# HTTP status codes that trigger an active-passive key swap. 403 = upstream
# reports the key is out of balance / forbidden; 429 = rate limit / quota burst.
_COOLDOWN_HTTP_STATUS: frozenset[int] = frozenset({403, 429})

logger = structlog.get_logger(__name__)


class VoyageReranker:
    """Production Voyage reranker (``RerankerPort``).

    Concrete strategy at ``infrastructure/reranker/voyage_reranker.py``;
    knows its own ``_PROVIDER_CODE`` so business logic never branches on
    brand strings — the reranker registry sees only ``RerankerPort``.
    """

    # Provider code used to look up the key pool. Adapter-internal ID;
    # business logic upstream never reads this — it goes through the
    # ``RerankerPort`` interface.
    _PROVIDER_CODE: str = "voyage"
    _PURPOSE: str = "rerank"

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_VOYAGE_RERANK_MODEL,
        base_url: str = DEFAULT_VOYAGE_RERANK_ENDPOINT,
        *,
        key_pool: ApiKeyPool | None = None,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        timeout_s: float = DEFAULT_VOYAGE_RERANK_TIMEOUT_S,
        dimensions: int = DEFAULT_VOYAGE_RERANK_DIMENSIONS,
    ) -> None:
        # Pool can be passed explicitly (tests) or resolved from a factory
        # (production DI). Factory path picks the pool matching this
        # adapter's provider_code so brand strings stay contained.
        resolved_pool: ApiKeyPool | None = key_pool
        if resolved_pool is None and key_pool_factory is not None:
            resolved_pool = key_pool_factory.get(self._PROVIDER_CODE, self._PURPOSE)
        # Legacy single-key env fallback so deployments that have not
        # provisioned ``PROVIDER_API_KEYS_JSON`` keep booting.
        legacy_api_key = (
            api_key
            or os.environ.get("RERANKER_VOYAGE_API_KEY", "")
            or os.environ.get("VOYAGE_API_KEY", "")
        )
        if resolved_pool is None and not legacy_api_key:
            raise ValueError(
                "VoyageReranker requires a key_pool or a non-empty api_key. "
                "Configure PROVIDER_API_KEYS_JSON or set "
                "RERANKER_VOYAGE_API_KEY in .env."
            )
        self._api_key = legacy_api_key
        self._model = model
        self._base_url = base_url
        self._key_pool = resolved_pool
        self._timeout = timeout_s
        # 0 = use model default; >0 = pass through as request param when
        # the API supports the dimensions truncation knob.
        self._dimensions = dimensions
        # Headers built per call when the pool is active so a key swap takes
        # effect on the very next request without a process restart.
        self._static_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Reused AsyncClient saves TLS+DNS per call. Lazy-init because
        # client creation binds the event loop (DI may not have one yet).
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        # CB fast-fails subsequent calls during outage; caller falls back to RRF.
        self._cb: CircuitBreaker = CircuitBreaker(
            name="reranker:voyage",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_VOYAGE_RERANK_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_VOYAGE_RERANK_CB_RESET_S,
            ),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-create the shared AsyncClient (event-loop-aware)."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _resolve_key(self) -> tuple[str, ApiKeyEntry | None]:
        """Return ``(api_key, pool_entry)`` for the next API call.

        ``pool_entry`` is None when the legacy single-key path is used; the
        caller short-circuits cooldown bookkeeping in that case.
        """
        if self._key_pool is None:
            return self._api_key, None
        entry = await self._key_pool.get_active()
        return entry.key, entry

    async def _handle_status_error(
        self,
        exc: httpx.HTTPStatusError,
        entry: ApiKeyEntry | None,
    ) -> None:
        """Mark the active key cooled when upstream signals 403/429."""
        if entry is None or self._key_pool is None:
            return
        status = exc.response.status_code if exc.response is not None else 0
        if status not in _COOLDOWN_HTTP_STATUS:
            return
        reason = f"HTTP_{status}"
        await self._key_pool.mark_cooldown(entry, reason=reason)
        next_entry = await self._key_pool.get_active()
        api_key_failover_total.labels(
            provider_code=self._key_pool.provider_code,
            purpose=self._key_pool.purpose,
            from_label=entry.label,
            to_label=next_entry.label,
            reason=reason,
        ).inc()

    @staticmethod
    def get_provider_name() -> str:
        return "voyage"

    @property
    def mode(self) -> str:
        return f"voyage:{self._model}"

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = DEFAULT_RERANK_TOP_N,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank chunks via Voyage rerank API; return top-N enriched dicts.

        @param query: user query (rewritten upstream).
        @param chunks: chunk dicts with ``content`` or ``text`` key.
        @param top_n: post-rerank cut-off.
        @param model: per-call model override.
        @return: list sorted by relevance, each augmented with rerank_score,
            retrieval_score, score (= rerank_score), reranker_used.
        @raises RetrievalError: on httpx errors; caller handles fallback.
        """
        if not chunks:
            return []

        effective_model = model or self._model
        capped_chunks = chunks[:DEFAULT_VOYAGE_RERANK_MAX_DOCS]

        documents = [
            c.get("content") or c.get("text") or ""
            for c in capped_chunks
        ]

        payload: dict[str, Any] = {
            "model": effective_model,
            "query": query,
            "documents": documents,
            "top_k": min(top_n, len(documents)),
        }
        # Optional dimensions knob — only forwarded when the operator
        # opted in (>0) so the API receives a clean default body otherwise.
        if self._dimensions > 0:
            payload["dimensions"] = self._dimensions

        client = await self._get_client()
        api_key, pool_entry = await self._resolve_key()
        headers = {**self._static_headers, "Authorization": f"Bearer {api_key}"}

        async def _do_post() -> dict[str, Any]:
            resp = await client.post(
                self._base_url,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

        try:
            with self._cb:
                data = await retry_with_backoff(
                    _do_post,
                    policy=RetryPolicy(
                        max_attempts=DEFAULT_VOYAGE_RERANK_MAX_ATTEMPTS,
                    ),
                    # 4xx propagates immediately; only transient 5xx/timeouts retried.
                    retryable_exceptions=(
                        httpx.TimeoutException,
                        httpx.ConnectError,
                        httpx.ReadError,
                        httpx.RemoteProtocolError,
                    ),
                )
        except CircuitBreakerOpen as exc:
            logger.warning(
                "voyage_reranker_circuit_open",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                doc_count=len(documents),
            )
            raise RetrievalError(
                f"Voyage reranker CB open: {exc!r}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "voyage_reranker_timeout",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                query_len=len(query),
                doc_count=len(documents),
                exc_info=True,
            )
            raise RetrievalError(
                f"Voyage reranker timed out after {self._timeout}s: {exc!r}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, pool_entry)
            logger.warning(
                "voyage_reranker_http_error",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                status=exc.response.status_code if exc.response is not None else 0,
                error=str(exc)[:200],
                exc_info=True,
            )
            raise RetrievalError(
                f"Voyage reranker HTTP error: {exc!r}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "voyage_reranker_http_error",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                error=str(exc)[:200],
                exc_info=True,
            )
            raise RetrievalError(
                f"Voyage reranker HTTP error: {exc!r}"
            ) from exc

        # Map API results back to original chunks (preserving all metadata).
        # Voyage returns the rank list under "data" (Cohere-style); accept
        # "results" as a defensive alias so a future API rev with renamed
        # field does not silently 0-out the output.
        rank_list = data.get("data") or data.get("results") or []
        output: list[dict[str, Any]] = []
        for r in rank_list:
            idx: int | None = r.get("index")
            if idx is None or idx >= len(capped_chunks):
                continue
            relevance_score = float(r.get("relevance_score", 0.0))
            src = capped_chunks[idx]
            enriched = dict(src)
            enriched["rerank_score"] = round(
                relevance_score, DEFAULT_VOYAGE_RERANK_SCORE_PRECISION,
            )
            enriched["retrieval_score"] = src.get("score")
            enriched["score"] = round(
                relevance_score, DEFAULT_VOYAGE_RERANK_SCORE_PRECISION,
            )
            enriched["reranker_used"] = self.mode
            output.append(enriched)

        logger.info(
            "voyage_rerank_done",
            reranker_provider=self._PROVIDER_CODE,
            model=effective_model,
            input_count=len(capped_chunks),
            output_count=len(output),
            top_n=top_n,
        )
        return output

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Smoke-ping Voyage API with a single document.

        Returns True if the API responds with 2xx; False on any error.
        Does NOT raise — callers use this as a liveness probe.
        """
        try:
            api_key, _entry = await self._resolve_key()
            headers = {
                **self._static_headers,
                "Authorization": f"Bearer {api_key}",
            }
            payload: dict[str, Any] = {
                "model": self._model,
                "query": "health check",
                "documents": ["ok"],
                "top_k": 1,
            }
            if self._dimensions > 0:
                payload["dimensions"] = self._dimensions
            async with httpx.AsyncClient(
                timeout=DEFAULT_VOYAGE_RERANK_HEALTH_TIMEOUT_S,
            ) as client:
                resp = await client.post(
                    self._base_url,
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                body = resp.json()
                # Voyage returns "data"; tolerate "results" defensively.
                return bool(body.get("data") or body.get("results"))
        except (httpx.HTTPError, httpx.TimeoutException, OSError):
            logger.warning(
                "voyage_reranker_health_check_failed", exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the reused AsyncClient."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None


__all__ = ["VoyageReranker"]
