"""ZeroEntropy reranker — hosted multilingual instruction-following reranker.

API: ``POST https://api.zeroentropy.dev/v1/models/rerank``. Auth via
``Authorization: Bearer <api_key>``. Request body mirrors the Cohere /
Jina rerank shape (``model``, ``query``, ``documents``, ``top_n``); the
response is ``{"results": [{"index": int, "relevance_score": float}, ...]}``.
The ZeroEntropy spec adds an optional ``latency`` knob (``"fast" | "slow"``)
where ``"slow"`` trades higher rate limits for >10s latency. We default
to ``"fast"`` because the reranker sits on the request-blocking path.

Key is sourced from the provider-agnostic ``ApiKeyPool`` via
``ApiKeyPoolFactory``, keyed by this adapter's internal
``_PROVIDER_CODE``. Falls back to ``RERANKER_ZEROENTROPY_API_KEY`` env
when no pool is configured, matching the Jina adapter's legacy path so
operators have a single-key bootstrap option.

Fail-soft: on transport error (timeout, 5xx, ConnectError) the adapter
raises ``RetrievalError`` and the caller falls back to ``NullReranker``
(same contract as ``JinaReranker``). 4xx propagates immediately and
trips the active-key cooldown when 403 / 429.
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
    DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_ZEROENTROPY_HEALTH_CHECK_TIMEOUT_S,
    DEFAULT_RERANKER_MAX_CONCURRENT,
    DEFAULT_ZEROENTROPY_RERANKER_CB_FAIL_MAX,
    DEFAULT_ZEROENTROPY_RERANKER_CB_RESET_S,
    DEFAULT_ZEROENTROPY_RERANKER_ENDPOINT,
    DEFAULT_ZEROENTROPY_RERANKER_LATENCY_MODE,
    DEFAULT_ZEROENTROPY_RERANKER_MAX_ATTEMPTS,
    DEFAULT_ZEROENTROPY_RERANKER_MAX_DOCS,
    DEFAULT_ZEROENTROPY_RERANKER_MODEL,
    DEFAULT_ZEROENTROPY_RERANKER_SCORE_PRECISION,
    DEFAULT_ZEROENTROPY_RERANKER_TIMEOUT_S,
)
from ragbot.shared.errors import CircuitBreakerOpen, RetrievalError

# HTTP status codes that trigger an active-passive key swap. 403 = upstream
# reports the key is out of balance / forbidden; 429 = rate limit / quota burst.
_COOLDOWN_HTTP_STATUS: frozenset[int] = frozenset({403, 429})

logger = structlog.get_logger(__name__)


class ZeroEntropyReranker:
    """Production ZeroEntropy reranker (``RerankerPort``).

    Concrete strategy at ``infrastructure/reranker/zeroentropy_reranker.py``;
    knows its own ``_PROVIDER_CODE`` so business logic never branches on
    brand strings — the reranker registry sees only ``RerankerPort``.
    """

    # Provider code used to look up the key pool. Adapter-internal ID;
    # business logic upstream never reads this — it goes through the
    # ``RerankerPort`` interface.
    _PROVIDER_CODE: str = "zeroentropy"
    _PURPOSE: str = "rerank"

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_ZEROENTROPY_RERANKER_MODEL,
        base_url: str = DEFAULT_ZEROENTROPY_RERANKER_ENDPOINT,
        *,
        key_pool: ApiKeyPool | None = None,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        latency: str = DEFAULT_ZEROENTROPY_RERANKER_LATENCY_MODE,
        max_concurrent: int = DEFAULT_RERANKER_MAX_CONCURRENT,
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
            or os.environ.get("RERANKER_ZEROENTROPY_API_KEY", "")
            or os.environ.get("ZEROENTROPY_API_KEY", "")
        )
        if resolved_pool is None and not legacy_api_key:
            raise ValueError(
                "ZeroEntropyReranker requires a key_pool or a non-empty api_key. "
                "Configure PROVIDER_API_KEYS_JSON or set "
                "RERANKER_ZEROENTROPY_API_KEY in .env."
            )
        self._api_key = legacy_api_key
        self._model = model
        self._base_url = base_url
        self._key_pool = resolved_pool
        self._latency = latency
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
        # Bulkhead (Async Rule 6): bound in-flight rerank calls so a burst
        # can't self-saturate the provider and trip the CB. Separate from the
        # embedder semaphore so the two pools don't starve each other. Size is
        # injectable (ops can pass ai_providers.max_concurrent); default const.
        self._sem = asyncio.Semaphore(max_concurrent)
        # CB fast-fails subsequent calls during outage; caller falls back to RRF.
        self._cb: CircuitBreaker = CircuitBreaker(
            name="reranker:zeroentropy",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_ZEROENTROPY_RERANKER_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_ZEROENTROPY_RERANKER_CB_RESET_S,
            ),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-create the shared AsyncClient (event-loop-aware)."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=DEFAULT_ZEROENTROPY_RERANKER_TIMEOUT_S,
                    )
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

    async def _cool_ratelimited_key(self, entry: ApiKeyEntry | None) -> None:
        """Cool a 429'd key for a short window + record the failover.

        Called mid-request from ``_do_post`` so the very next retry attempt
        round-robins past this key onto a fresh one. The TTL is short
        (``DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S``) because a per-minute (BPM)
        quota refills fast — a long park would needlessly shrink the pool
        and cascade load onto the survivors.
        """
        if entry is None or self._key_pool is None:
            return
        await self._key_pool.mark_cooldown(
            entry,
            reason="HTTP_429",
            cooldown_s=DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S,
        )
        next_entry = await self._key_pool.get_active()
        api_key_failover_total.labels(
            provider_code=self._key_pool.provider_code,
            purpose=self._key_pool.purpose,
            from_label=entry.label,
            to_label=next_entry.label,
            reason="HTTP_429",
        ).inc()

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
        # 429 = transient per-minute (BPM) quota — refills in ~60s, so cool
        # the key briefly and let the round-robin bring it straight back.
        # 403 = revoked / forbidden — keep the long default so a dead key is
        # not retried every rotation.
        cooldown_s = (
            DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S if status == 429 else None
        )
        await self._key_pool.mark_cooldown(
            entry, reason=reason, cooldown_s=cooldown_s
        )
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
        return "zeroentropy"

    @property
    def mode(self) -> str:
        return f"zeroentropy:{self._model}"

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        top_n: int = DEFAULT_RERANK_TOP_N,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank chunks via ZeroEntropy rerank API; return top-N enriched dicts.

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
        capped_chunks = chunks[:DEFAULT_ZEROENTROPY_RERANKER_MAX_DOCS]

        documents = [
            c.get("content") or c.get("text") or ""
            for c in capped_chunks
        ]

        payload: dict[str, Any] = {
            "model": effective_model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }
        # ZeroEntropy ``latency`` knob: "fast" / "slow" select a guaranteed
        # mode but EACH has its own rate ceiling — the "fast" quota trips a
        # 503 under concurrent load. Empty ``_latency`` omits the knob → ZE's
        # default mode (same sub-second latency, no fast-mode rate ceiling).
        if self._latency:
            payload["latency"] = self._latency

        client = await self._get_client()
        # Last key used — the 403 HTTPStatusError handler reads it to cool the
        # right entry. The key is resolved PER ATTEMPT inside ``_do_post`` so a
        # retry after a 429 rotates round-robin onto a DIFFERENT upstream key
        # (the previous one is cooled briefly), instead of hammering the same
        # throttled key for every attempt and then degrading to RRF.
        last_entry: ApiKeyEntry | None = None

        async def _do_post() -> dict[str, Any]:
            nonlocal last_entry
            api_key, pool_entry = await self._resolve_key()
            last_entry = pool_entry
            headers = {
                **self._static_headers,
                "Authorization": f"Bearer {api_key}",
            }
            resp = await client.post(
                self._base_url,
                json=payload,
                headers=headers,
            )
            # A per-KEY rate-limit signal: 429 (BPM quota) OR a 503 whose body
            # is the ZeroEntropy "Rate limit … could not be met" rejection
            # (latency-mode quota). Both are key-level, not transport blips —
            # retrying the same key is futile. Cool this key briefly so the
            # next attempt's round-robin skips it onto a fresh key, then
            # re-raise as a retryable ConnectError.
            body = resp.text[:300]
            is_rate_503 = (
                resp.status_code == 503 and "rate limit" in body.lower()
            )
            if resp.status_code == 429 or is_rate_503:
                await self._cool_ratelimited_key(pool_entry)
                raise httpx.ConnectError(
                    f"ZeroEntropy transient {resp.status_code} (rate): {body}"
                )
            # Transient 5xx (502/503/504) are retryable: re-raise as
            # ConnectError so retry_with_backoff catches it via the existing
            # retryable_exceptions tuple. 4xx (auth, validation) propagate
            # immediately as HTTPStatusError.
            # 2026-05-27: ZeroEntropy 503 webhook alerts during transient
            # upstream outage were NOT retried before this guard.
            if resp.status_code in (502, 503, 504):
                raise httpx.ConnectError(
                    f"ZeroEntropy transient {resp.status_code}: {body}"
                )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

        try:
            async with self._sem:
                with self._cb:
                    data = await retry_with_backoff(
                        _do_post,
                        policy=RetryPolicy(
                            max_attempts=DEFAULT_ZEROENTROPY_RERANKER_MAX_ATTEMPTS,
                        ),
                        # 4xx propagates immediately; transient 5xx/429 are
                        # re-raised as ConnectError inside ``_do_post`` so the
                        # same retry path covers timeouts + transport + 5xx.
                        retryable_exceptions=(
                            httpx.TimeoutException,
                            httpx.ConnectError,
                            httpx.ReadError,
                            httpx.RemoteProtocolError,
                        ),
                    )
        except CircuitBreakerOpen as exc:
            logger.warning(
                "zeroentropy_reranker_circuit_open",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                doc_count=len(documents),
            )
            raise RetrievalError(
                f"ZeroEntropy reranker CB open: {exc!r}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "zeroentropy_reranker_timeout",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                query_len=len(query),
                doc_count=len(documents),
                exc_info=True,
            )
            raise RetrievalError(
                f"ZeroEntropy reranker timed out after "
                f"{DEFAULT_ZEROENTROPY_RERANKER_TIMEOUT_S}s: {exc!r}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, last_entry)
            logger.warning(
                "zeroentropy_reranker_http_error",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                status=exc.response.status_code if exc.response is not None else 0,
                error=str(exc)[:200],
                exc_info=True,
            )
            raise RetrievalError(
                f"ZeroEntropy reranker HTTP error: {exc!r}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "zeroentropy_reranker_http_error",
                reranker_provider=self._PROVIDER_CODE,
                model=effective_model,
                error=str(exc)[:200],
                exc_info=True,
            )
            raise RetrievalError(
                f"ZeroEntropy reranker HTTP error: {exc!r}"
            ) from exc

        # Map API results back to original chunks (preserving all metadata).
        output: list[dict[str, Any]] = []
        for r in data.get("results", []):
            idx: int | None = r.get("index")
            if idx is None or idx >= len(capped_chunks):
                continue
            relevance_score = float(r.get("relevance_score", 0.0))
            src = capped_chunks[idx]
            enriched = dict(src)
            enriched["rerank_score"] = round(
                relevance_score, DEFAULT_ZEROENTROPY_RERANKER_SCORE_PRECISION,
            )
            enriched["retrieval_score"] = src.get("score")
            enriched["score"] = round(
                relevance_score, DEFAULT_ZEROENTROPY_RERANKER_SCORE_PRECISION,
            )
            enriched["reranker_used"] = self.mode
            output.append(enriched)

        logger.info(
            "zeroentropy_rerank_done",
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
        """Smoke-ping ZeroEntropy API with a single document.

        Returns True if the API responds with 2xx; False on any error.
        Does NOT raise — callers use this as a liveness probe.
        """
        try:
            api_key, _entry = await self._resolve_key()
            headers = {
                **self._static_headers,
                "Authorization": f"Bearer {api_key}",
            }
            async with httpx.AsyncClient(
                timeout=DEFAULT_ZEROENTROPY_HEALTH_CHECK_TIMEOUT_S,
            ) as client:
                resp = await client.post(
                    self._base_url,
                    json={
                        "model": self._model,
                        "query": "health check",
                        "documents": ["ok"],
                        "top_n": 1,
                        "latency": self._latency,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return bool(resp.json().get("results"))
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.warning(
                "zeroentropy_reranker_health_check_failed", exc_info=True,
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


__all__ = ["ZeroEntropyReranker"]
