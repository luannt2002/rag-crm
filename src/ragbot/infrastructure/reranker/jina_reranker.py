"""Jina AI Reranker v3 — listwise multilingual reranker.

API: https://api.jina.ai/v1/rerank. Max 64 docs/request. API key sourced
from the provider-agnostic ``ApiKeyPool`` via ``ApiKeyPoolFactory``,
keyed by this adapter's internal ``_PROVIDER_CODE``.

The reranker resolves the active key per call and marks the active entry
on cooldown for HTTP 403 (out of balance) or 429 (rate limit). When no
pool is configured for the adapter's provider code the reranker falls
back to the legacy single-key path (``RERANKER_JINA_API_KEY`` env).

Fail-soft: httpx errors raise RetrievalError; caller falls back to NullReranker.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from ragbot.application.ports.token_ledger_port import TokenLedgerPort
from ragbot.application.services.retry_policy import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from ragbot.infrastructure.observability.metrics import api_key_failover_total
from ragbot.infrastructure.token_ledger.aux_usage import emit_aux_usage
from ragbot.shared.api_key_pool import ApiKeyEntry, ApiKeyPool, ApiKeyPoolFactory
from ragbot.shared.constants import (
    DEFAULT_JINA_HEALTH_CHECK_TIMEOUT_S,
    DEFAULT_JINA_RERANKER_CB_FAIL_MAX,
    DEFAULT_JINA_RERANKER_CB_RESET_S,
    DEFAULT_JINA_RERANKER_MAX_ATTEMPTS,
    DEFAULT_JINA_RERANKER_MAX_DOCS,
    DEFAULT_JINA_RERANKER_MODEL,
    DEFAULT_JINA_RERANKER_SCORE_PRECISION,
    DEFAULT_JINA_RERANKER_TIMEOUT_S,
    DEFAULT_RERANK_TOP_N,
)
from ragbot.shared.errors import CircuitBreakerOpen, RetrievalError

# HTTP status codes that trigger an active-passive key swap. 403 = upstream
# reports the key is out of balance; 429 = rate limit / quota burst.
_COOLDOWN_HTTP_STATUS: frozenset[int] = frozenset({403, 429})

logger = structlog.get_logger(__name__)

_JINA_RERANK_ENDPOINT: str = "https://api.jina.ai/v1/rerank"


class JinaReranker:
    """Production Jina reranker (RerankerPort).

    Concrete strategy at ``infrastructure/reranker/jina_reranker.py``;
    knows its own ``_PROVIDER_CODE`` so business logic never branches on
    brand strings — the reranker registry sees only ``RerankerPort``.
    """

    # Provider code used to look up the key pool. Adapter-internal ID;
    # business logic upstream never reads this — it goes through the
    # ``RerankerPort`` interface.
    _PROVIDER_CODE: str = "jina"
    _PURPOSE: str = "rerank"

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_JINA_RERANKER_MODEL,
        base_url: str = _JINA_RERANK_ENDPOINT,
        *,
        key_pool: ApiKeyPool | None = None,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        ledger: TokenLedgerPort | None = None,
    ) -> None:
        # Pool can be passed explicitly (tests) or resolved from a factory
        # (production DI). Factory path picks the pool matching this
        # adapter's provider_code so brand strings stay contained.
        resolved_pool: ApiKeyPool | None = key_pool
        if resolved_pool is None and key_pool_factory is not None:
            resolved_pool = key_pool_factory.get(self._PROVIDER_CODE, self._PURPOSE)
        # Legacy single-key env fallback so deployments that have not
        # provisioned ``PROVIDER_API_KEYS_JSON`` keep booting.
        legacy_api_key = api_key or os.environ.get("RERANKER_JINA_API_KEY", "") or os.environ.get(
            "JINA_API_KEY", "",
        )
        if resolved_pool is None and not legacy_api_key:
            raise ValueError(
                "JinaReranker requires a key_pool or a non-empty api_key. "
                "Configure PROVIDER_API_KEYS_JSON or set RERANKER_JINA_API_KEY in .env."
            )
        self._api_key = legacy_api_key
        self._model = model
        self._base_url = base_url
        self._key_pool = resolved_pool
        self._ledger = ledger
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
            name="reranker:jina",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_JINA_RERANKER_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_JINA_RERANKER_CB_RESET_S,
            ),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-create the shared AsyncClient (event-loop-aware)."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=DEFAULT_JINA_RERANKER_TIMEOUT_S,
                    )
        return self._client

    async def _resolve_key(self) -> tuple[str, ApiKeyEntry | None]:
        """Return (api_key, pool_entry) for the next API call.

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
        # Record the swap target for ops dashboards. ``to_label`` is the
        # entry the next caller will see; when no secondary exists the
        # pool returns primary again, so the metric records the same label.
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
        return "jina"

    @property
    def mode(self) -> str:
        return f"jina:{self._model}"

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = DEFAULT_RERANK_TOP_N,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank chunks via Jina rerank API; return top-N enriched dicts.

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
        capped_chunks = chunks[:DEFAULT_JINA_RERANKER_MAX_DOCS]

        documents = [
            c.get("content") or c.get("text") or ""
            for c in capped_chunks
        ]

        payload: dict[str, Any] = {
            "model": effective_model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
            # return_documents=False — caller re-attaches metadata from original chunks.
            "return_documents": False,
        }

        _ledger_t0 = datetime.now(UTC)
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
                        max_attempts=DEFAULT_JINA_RERANKER_MAX_ATTEMPTS,
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
                "jina_reranker_circuit_open",
                model=effective_model,
                doc_count=len(documents),
            )
            raise RetrievalError(f"Jina reranker CB open: {exc!r}") from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "jina_reranker_timeout",
                model=effective_model,
                query_len=len(query),
                doc_count=len(documents),
                exc_info=True,
            )
            raise RetrievalError(
                f"Jina reranker timed out after {DEFAULT_JINA_RERANKER_TIMEOUT_S}s: {exc!r}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, pool_entry)
            logger.warning(
                "jina_reranker_http_error",
                model=effective_model,
                status=exc.response.status_code if exc.response is not None else 0,
                error=str(exc)[:200],
                exc_info=True,
            )
            raise RetrievalError(f"Jina reranker HTTP error: {exc!r}") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "jina_reranker_http_error",
                model=effective_model,
                error=str(exc)[:200],
                exc_info=True,
            )
            raise RetrievalError(f"Jina reranker HTTP error: {exc!r}") from exc

        # Log-center: snapshot Jina rerank token usage to the durable ledger
        # (action="rerank"). Fire-and-forget — helper never raises here.
        _usage = data.get("usage") or {}
        emit_aux_usage(
            self._ledger,
            action="rerank",
            provider=self._PROVIDER_CODE,
            model=effective_model,
            total_tokens=int(_usage.get("total_tokens", 0) or 0),
            started_at=_ledger_t0,
            finished_at=datetime.now(UTC),
        )

        # Map API results back to original chunks (preserving all metadata)
        output: list[dict[str, Any]] = []
        for r in data.get("results", []):
            idx: int | None = r.get("index")
            if idx is None or idx >= len(capped_chunks):
                continue
            relevance_score = float(r.get("relevance_score", 0.0))
            src = capped_chunks[idx]
            enriched = dict(src)
            enriched["rerank_score"] = round(relevance_score, DEFAULT_JINA_RERANKER_SCORE_PRECISION)
            enriched["retrieval_score"] = src.get("score")
            enriched["score"] = round(relevance_score, DEFAULT_JINA_RERANKER_SCORE_PRECISION)
            enriched["reranker_used"] = self.mode
            output.append(enriched)

        logger.info(
            "jina_rerank_done",
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
        """Smoke-ping Jina API with a single document.

        Returns True if the API responds with 2xx; False on any error.
        Does NOT raise — callers use this as a liveness probe.
        """
        try:
            api_key, _entry = await self._resolve_key()
            headers = {**self._static_headers, "Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient(timeout=DEFAULT_JINA_HEALTH_CHECK_TIMEOUT_S) as client:
                resp = await client.post(
                    self._base_url,
                    json={
                        "model": self._model,
                        "query": "health check",
                        "documents": ["ok"],
                        "top_n": 1,
                        "return_documents": False,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return bool(resp.json().get("results"))
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.warning("jina_reranker_health_check_failed", exc_info=True)
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


__all__ = ["JinaReranker"]
