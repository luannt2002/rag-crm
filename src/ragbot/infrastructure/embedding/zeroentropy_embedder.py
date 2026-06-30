"""ZeroEntropy embedder — hosted multilingual embedding (zembed-1, 2560-dim).

Direct HTTP adapter (bypasses LiteLLM — ZeroEntropy is not on the LiteLLM
provider list as of 2026-05). Follows the same shape as the existing
``LiteLLMEmbedder``: implements ``EmbeddingPort`` so the orchestrator can
swap providers via DI without code changes.

Endpoint: ``POST https://api.zeroentropy.dev/v1/models/embed``
Auth: ``Authorization: Bearer <key>``
Body: ``{model, input: [str, ...], input_type: "document" | "query"}``
Response: ``{results: [{embedding: [float, ...]}, ...], usage}``

Provider-specific quirks honoured per CLAUDE.md domain-neutral rule:
* ``input_type`` mapped from ``EmbeddingSpec.task`` (passage→document,
  query→query). When ``task`` is absent, defaults to ``document`` (the
  ingest path is the dominant caller).
* All URLs / model names / timeouts read from ``shared/constants.py`` or
  env so swapping providers stays one config row.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import structlog

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.application.services.retry_policy import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from ragbot.shared.api_key_pool import ApiKeyEntry, ApiKeyPool, ApiKeyPoolFactory
from ragbot.shared.constants import (
    DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
    DEFAULT_EMBEDDER_CB_FAIL_MAX,
    DEFAULT_EMBEDDER_CB_RESET_S,
    DEFAULT_EMBEDDER_MAX_CONCURRENT,
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_TIMEOUT_S,
    DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS,
    DEFAULT_RETRY_INITIAL_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_MS,
    DEFAULT_ZEROENTROPY_API_URL,
    DEFAULT_ZEROENTROPY_EMBEDDING_DIM,
    DEFAULT_ZEROENTROPY_EMBEDDING_MODEL,
)
from ragbot.shared.errors import CircuitBreakerOpen, ExternalServiceError
from ragbot.shared.types import TenantId

logger = structlog.get_logger(__name__)


_RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class ZeroEntropyEmbedder(EmbeddingPort):
    """Direct-HTTP embedder for ZeroEntropy zembed-1 (2560-dim multilingual)."""

    _PROVIDER_CODE: str = "zeroentropy"
    _PURPOSE: str = "embed"

    def __init__(
        self,
        model: str = DEFAULT_ZEROENTROPY_EMBEDDING_MODEL,
        *,
        api_url: str = DEFAULT_ZEROENTROPY_API_URL,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        timeout_s: int = DEFAULT_EMBEDDING_TIMEOUT_S,
        dimensions: int = DEFAULT_ZEROENTROPY_EMBEDDING_DIM,
        max_concurrent: int = DEFAULT_EMBEDDER_MAX_CONCURRENT,
    ) -> None:
        self._model = model
        self._api_url = api_url.rstrip("/") + "/v1/models/embed"
        self._timeout_s = timeout_s
        self._dimensions = dimensions
        self._pool: ApiKeyPool | None = (
            key_pool_factory.get(self._PROVIDER_CODE, self._PURPOSE)
            if key_pool_factory is not None
            else None
        )
        self._client: httpx.AsyncClient | None = None
        # Lazy-init lock — needed once the embedder DI is a Singleton so
        # the first N concurrent callers don't all see ``self._client is
        # None`` and each instantiate a fresh AsyncClient (TLS+DNS waste
        # + connection-pool fragmentation). Mirrors the pattern in
        # :class:`ZeroEntropyReranker` and :class:`JinaReranker`.
        self._client_lock = asyncio.Lock()
        # Bounded concurrency (Async Rule 6): cap in-flight embed calls so a
        # request burst can't self-saturate the provider and trip the CB → 503.
        # Size is injectable so ops can pass ``ai_providers.max_concurrent`` via
        # the registry (Open-Closed); defaults to the platform constant.
        self._sem = asyncio.Semaphore(max_concurrent)
        self._cb: CircuitBreaker = CircuitBreaker(
            name="embedder:zeroentropy",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_EMBEDDER_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_EMBEDDER_CB_RESET_S,
            ),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-create the shared AsyncClient (event-loop-aware).

        Double-checked locking — the fast path reads ``self._client``
        unlocked (common case once warm); the cold path takes the lock
        and re-checks before creating.
        """
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def _resolve_key(self) -> tuple[str | None, ApiKeyEntry | None]:
        if self._pool is not None:
            entry = await self._pool.get_active()
            return entry.key, entry
        legacy = os.environ.get("ZEROENTROPY_EMBEDDING_API_KEY") or os.environ.get(
            "ZEROENTROPY_API_KEY",
        )
        return legacy, None

    async def health_check(self) -> bool:
        try:
            key, _ = await self._resolve_key()
            if not key:
                return False
            client = await self._get_client()
            resp = await asyncio.wait_for(
                client.post(
                    self._api_url,
                    json={
                        "model": self._model,
                        "input": ["health"],
                        "input_type": "document",
                        # Match production matryoshka cap so warmup exercises the
                        # same dim that document_chunks.embedding expects (1280).
                        # Without it, ZE returns the full 2560-dim vector — the
                        # probe still passes but doesn't catch dim-config drift.
                        "dimensions": self._dimensions,
                    },
                    headers={"Authorization": f"Bearer {key}"},
                ),
                timeout=DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
            )
            if resp.status_code != 200:
                return False
            # Verify the returned vector has the configured dimension. A
            # provider-side default flip would otherwise silently break
            # ingest with `psycopg2.errors.DataException` only at insert
            # time (catastrophic, batches lost). Catch it at warmup.
            try:
                payload = resp.json()
                vec = (payload.get("results") or [{}])[0].get("embedding") or []
                if len(vec) != self._dimensions:
                    logger.warning(
                        "embedder_health_check_dim_mismatch",
                        expected=self._dimensions,
                        actual=len(vec),
                        model=self._model,
                    )
                    return False
            except (ValueError, KeyError, TypeError):
                # JSON shape unexpected — treat as unhealthy so warmup_partial
                # fires and ops can investigate before traffic lands.
                return False
            return True
        except (httpx.HTTPError, asyncio.TimeoutError, OSError):
            logger.warning("embedder_health_check_failed", model=self._model, exc_info=True)
            return False

    def _input_type_for_spec(self, spec: EmbeddingSpec | None) -> str:
        """Map ``EmbeddingSpec.task`` → ZeroEntropy ``input_type``."""
        task = getattr(spec, "task", None) if spec is not None else None
        if isinstance(task, str) and task.lower() in {"query", "retrieval.query", "search_query"}:
            return "query"
        return "document"

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> list[list[float]]:
        if not texts:
            return []
        # Strip any LiteLLM-style provider prefix (``openai/zembed-1`` ->
        # ``zembed-1``) since ZeroEntropy's API accepts the bare model id only.
        # The orchestrator's model_resolver synthesises a LiteLLM-format ID
        # for cross-provider routing; we don't want it leaking to the wire.
        raw_model = spec.model_name if spec and spec.model_name else self._model
        model = raw_model.split("/", 1)[1] if "/" in raw_model else raw_model
        input_type = self._input_type_for_spec(spec)
        key, _entry = await self._resolve_key()
        if not key:
            raise ExternalServiceError(
                "ZeroEntropy embedding API key not configured "
                "(set ZEROENTROPY_EMBEDDING_API_KEY or ZEROENTROPY_API_KEY)",
            )

        client = await self._get_client()
        all_results: list[list[float]] = []
        for batch_start in range(0, len(texts), DEFAULT_EMBEDDING_MAX_BATCH):
            batch = texts[batch_start : batch_start + DEFAULT_EMBEDDING_MAX_BATCH]

            async def _call(
                b: list[str] = batch,
                _model: str = model,
                _itype: str = input_type,
                _key: str = key,
            ) -> list[list[float]]:
                _t0 = time.monotonic()
                async with asyncio.timeout(self._timeout_s):
                    resp = await client.post(
                        self._api_url,
                        json={
                            "model": _model,
                            "input": b,
                            "input_type": _itype,
                            "dimensions": self._dimensions,
                        },
                        headers={"Authorization": f"Bearer {_key}"},
                    )
                    if resp.status_code != 200:
                        # Observability first — emit the canonical event with the
                        # provider's actual status + body snippet BEFORE deciding
                        # whether the status is retryable. Without this, a 4xx
                        # surfaces only as a string-wrapped ExternalServiceError
                        # and a retryable status that exhausts retries loses its
                        # original status/body entirely. Pure logging: the raise
                        # below is unchanged.
                        logger.warning(
                            "external_call_failed",
                            integration=self._PURPOSE,
                            provider=self._PROVIDER_CODE,
                            model=_model,
                            status_code=resp.status_code,
                            error=resp.text[:DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS],
                            duration_ms=int((time.monotonic() - _t0) * 1000),
                        )
                        if resp.status_code in _RETRYABLE_HTTP_STATUS:
                            raise httpx.HTTPStatusError(
                                f"retryable HTTP {resp.status_code}: {resp.text[:200]}",
                                request=resp.request,
                                response=resp,
                            )
                        raise ExternalServiceError(
                            f"ZeroEntropy embed failed: HTTP {resp.status_code}: {resp.text[:300]}",
                        )
                    data: dict[str, Any] = resp.json()
                    results = data.get("results") or []
                    return [item["embedding"] for item in results]

            try:
                async with self._sem:
                    with self._cb:
                        batch_results = await retry_with_backoff(
                            _call,
                            policy=RetryPolicy(
                                max_attempts=DEFAULT_RETRY_MAX_ATTEMPTS,
                                initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS,
                                max_backoff_ms=DEFAULT_RETRY_MAX_MS,
                            ),
                            retryable_exceptions=(
                                OSError,
                                ConnectionError,
                                TimeoutError,
                                httpx.HTTPStatusError,
                                httpx.TimeoutException,
                                httpx.HTTPError,
                            ),
                        )
            except CircuitBreakerOpen as exc:
                logger.warning(
                    "embedder_circuit_open",
                    model=model,
                    batch_index=batch_start,
                )
                raise ExternalServiceError(f"embedding CB open: {exc}") from exc
            except ExternalServiceError:
                raise
            except (httpx.HTTPError, OSError, TimeoutError) as exc:  # noqa: BLE001 — fall through hard errors as external service failure
                raise ExternalServiceError(
                    f"ZeroEntropy embedding API failed after retries: {exc}",
                ) from exc
            all_results.extend(batch_results)

        return all_results

    async def embed_one(
        self,
        text: str,
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,
    ) -> list[float]:
        result = await self.embed_batch([text], spec=spec, record_tenant_id=record_tenant_id)
        return result[0]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["ZeroEntropyEmbedder"]
