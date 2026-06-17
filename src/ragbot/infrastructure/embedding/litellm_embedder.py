"""LiteLLM-based embedding client — cloud embedding APIs via LiteLLM."""

from __future__ import annotations

import asyncio
import os

import litellm
import structlog

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.application.services.retry_policy import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from ragbot.infrastructure.observability.metrics import api_key_failover_total
from ragbot.shared.api_key_pool import ApiKeyEntry, ApiKeyPool, ApiKeyPoolFactory
from ragbot.shared.constants import (
    DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
    DEFAULT_EMBEDDER_CB_FAIL_MAX,
    DEFAULT_EMBEDDER_CB_RESET_S,
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_TIMEOUT_S,
    DEFAULT_RETRY_INITIAL_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_MS,
    JINA_EMBEDDING_MODEL_PREFIXES,
)
from ragbot.shared.errors import CircuitBreakerOpen, ExternalServiceError
from ragbot.shared.types import TenantId

logger = structlog.get_logger(__name__)

# HTTP status codes that trigger an active-passive key swap. LiteLLM surfaces
# these via ``status_code`` on its mapped exception classes.
_COOLDOWN_HTTP_STATUS: frozenset[int] = frozenset({403, 429})

# Provider code for Jina-prefixed embedding models. Localised here because
# the LiteLLM embedder is the Jina-aware dispatch surface — selecting the
# right pool requires knowing the model-prefix → provider mapping. Add a
# new branch (and matching prefix tuple in ``constants``) when onboarding
# another provider with quota-burst failover semantics.
_JINA_PROVIDER_CODE: str = "jina"
# Purpose tag — disambiguates pools when the same provider serves both
# embed + rerank surfaces (see RerankerSettings).
_EMBED_PURPOSE: str = "embed"


class LiteLLMEmbedder(EmbeddingPort):
    """Cloud-based embedding via LiteLLM (OpenAI, Gemini, Cohere, etc.)."""

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODEL,
        *,
        key_pool_factory: ApiKeyPoolFactory | None = None,
    ) -> None:
        self._model = model
        # Resolve the Jina-purpose pool eagerly so the per-call hot path is
        # a single in-memory dict lookup (factory caches by tuple key).
        # ``None`` when no Jina keys are configured — adapter falls back
        # to env-var path.
        self._jina_pool: ApiKeyPool | None = (
            key_pool_factory.get(_JINA_PROVIDER_CODE, _EMBED_PURPOSE)
            if key_pool_factory is not None
            else None
        )
        # CB-OPEN fast-fails during provider outage so worker isn't starved
        # by retry * timeout × queue-depth.
        self._cb: CircuitBreaker = CircuitBreaker(
            name="embedder:litellm",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_EMBEDDER_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_EMBEDDER_CB_RESET_S,
            ),
        )

    async def _resolve_jina_key(self) -> tuple[str | None, ApiKeyEntry | None]:
        """Return (api_key, pool_entry) for the next Jina embedding call.

        Pool path resolves the active key from Redis cooldown ledger.
        Legacy path falls back to env vars so deployments that have not
        provisioned a pool keep working unchanged.
        """
        if self._jina_pool is not None:
            entry = await self._jina_pool.get_active()
            return entry.key, entry
        # ``EMBEDDING_JINA_API_KEY`` and ``JINA_API_KEY`` env aliases
        # cover the historical .env layouts.
        legacy = os.environ.get("EMBEDDING_JINA_API_KEY") or os.environ.get(
            "JINA_API_KEY",
        )
        return legacy, None

    async def _mark_cooldown(self, entry: ApiKeyEntry, status: int) -> None:
        """Record cooldown + emit failover metric on 403/429 from upstream."""
        if self._jina_pool is None:
            return
        reason = f"HTTP_{status}"
        await self._jina_pool.mark_cooldown(entry, reason=reason)
        next_entry = await self._jina_pool.get_active()
        api_key_failover_total.labels(
            provider_code=self._jina_pool.provider_code,
            purpose=self._jina_pool.purpose,
            from_label=entry.label,
            to_label=next_entry.label,
            reason=reason,
        ).inc()

    async def health_check(self) -> bool:
        try:
            resp = await asyncio.wait_for(
                litellm.aembedding(model=self._model, input=["health"]),
                timeout=DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
            )
            return bool(resp.data)
        except (
            litellm.exceptions.APIError,
            asyncio.TimeoutError,
            OSError,
            ConnectionError,
            TimeoutError,
        ):
            # Probe failure surfaces from the litellm exception tree
            # (auth, rate limit, service unavailable) plus the network /
            # asyncio timeout layer; report unhealthy without leaking
            # tracebacks to callers (e.g. /health/models).
            logger.warning("embedder_health_check_failed", model=self._model, exc_info=True)
            return False

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> list[list[float]]:
        if not texts:
            return []
        model = spec.model_name if spec and spec.model_name else self._model

        # Pass-through provider-specific kwargs so asymmetric models can
        # select query/passage heads. getattr defends against legacy stubs
        # without the `task` field.
        extra_kwargs: dict[str, object] = {}
        spec_task = getattr(spec, "task", None) if spec is not None else None
        if spec_task is not None:
            extra_kwargs["task"] = spec_task
        pool_entry: ApiKeyEntry | None = None
        if model.startswith(JINA_EMBEDDING_MODEL_PREFIXES):
            jina_key, pool_entry = await self._resolve_jina_key()
            if jina_key:
                extra_kwargs["api_key"] = jina_key

        # Enforce batch cap (OpenAI ~2048 tok/req); split + concat sub-batches.
        all_results: list[list[float]] = []
        for batch_start in range(0, len(texts), DEFAULT_EMBEDDING_MAX_BATCH):
            batch = texts[batch_start : batch_start + DEFAULT_EMBEDDING_MAX_BATCH]

            async def _call(
                b: list[str] = batch,
                _kw: dict[str, object] = extra_kwargs,
            ) -> list[list[float]]:
                async with asyncio.timeout(DEFAULT_EMBEDDING_TIMEOUT_S):
                    resp = await litellm.aembedding(model=model, input=b, **_kw)
                    return [item["embedding"] for item in resp.data]

            try:
                # CB-guard wraps retry — OPEN short-circuits before retry burns time.
                with self._cb:
                    batch_results = await retry_with_backoff(
                        _call,
                        policy=RetryPolicy(max_attempts=DEFAULT_RETRY_MAX_ATTEMPTS, initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS, max_backoff_ms=DEFAULT_RETRY_MAX_MS),
                        retryable_exceptions=(
                            OSError, ConnectionError, TimeoutError,
                            litellm.exceptions.RateLimitError,
                            litellm.exceptions.ServiceUnavailableError,
                            litellm.exceptions.APIConnectionError,
                        ),
                    )
            except CircuitBreakerOpen as exc:
                logger.warning(
                    "embedder_circuit_open",
                    model=model,
                    batch_index=batch_start,
                )
                raise ExternalServiceError(
                    f"embedding CB open: {exc}"
                ) from exc
            except Exception as exc:  # noqa: BLE001 — preserve existing public contract
                # Inspect upstream status before re-raising so a key under
                # quota burst can be cooled and the request can shed to the
                # standby on its next attempt.
                status = getattr(exc, "status_code", None)
                if (
                    pool_entry is not None
                    and isinstance(status, int)
                    and status in _COOLDOWN_HTTP_STATUS
                ):
                    await self._mark_cooldown(pool_entry, status)
                raise ExternalServiceError(f"embedding API failed after retries: {exc}") from exc
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
        pass


__all__ = ["LiteLLMEmbedder"]
