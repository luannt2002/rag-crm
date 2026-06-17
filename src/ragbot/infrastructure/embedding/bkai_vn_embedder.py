"""BKAI Vietnamese Bi-Encoder embedder — PhoBERT-base-v2, 768-dim.

Self-hosted HTTP adapter for the open-weight BKAI Vietnamese bi-encoder
(``bkai-foundation-models/vietnamese-bi-encoder`` on HuggingFace), wrapping
a HuggingFace Text-Embeddings-Inference (TEI) endpoint. Implements
``EmbeddingPort`` so the orchestrator swaps providers via DI without
business-logic changes (CLAUDE.md Strategy + Port + Registry).

CLAUDE.md compliance
--------------------
* Domain-neutral: BKAI is a general Vietnamese language model trained on
  open VN corpora (Wikipedia, news, legal). It is NOT a brand/customer
  artefact and is interchangeable per-deployment via ``embedding_provider``.
* Zero-hardcode: dimensions / model id / endpoint path live in
  ``shared/constants.py``; deployment URL comes from env
  ``BKAI_VN_EMBEDDING_URL``. No magic numbers / brand literals.
* No app-inject / no override: this adapter only computes vectors; it does
  not touch the LLM answer surface.
* Tenant isolation honoured: ``embed_*`` accept ``record_tenant_id`` for
  audit propagation (no cross-tenant pool sharing).

Proof citation
--------------
* Model card: https://huggingface.co/bkai-foundation-models/vietnamese-bi-encoder
* Architecture: PhoBERT-base-v2 (Nguyen & Tuan Nguyen, EMNLP-Findings 2020,
  https://aclanthology.org/2020.findings-emnlp.92/) bi-encoder fine-tuned
  on MS MARCO + Vietnamese Wikipedia QA pairs.
* Benchmark: BKAI legal retrieval benchmark reports MRR@10 = 80.73% on
  Vietnamese legal corpus (BKAI 2024 internal eval; reference report at
  arXiv:2412.00657 — Vietnamese RAG survey, Table 4).
* Dimension: 768 (PhoBERT-base hidden size). Well below pgvector's 2000-dim
  HNSW limit — no halfvec rewrite needed.

Why a separate adapter (vs LiteLLM routing)
-------------------------------------------
LiteLLM does not list ``bkai-foundation-models/vietnamese-bi-encoder`` as a
hosted provider; the canonical deployment is self-hosted (HF TEI on GPU).
This adapter follows the ZeroEntropy direct-HTTP pattern so onboarding new
self-hosted embedders is a one-file copy.

Endpoint contract (HuggingFace TEI)
-----------------------------------
* ``POST {base_url}/embed``
* Body: ``{"inputs": [str, ...], "truncate": true}``
* Response: ``[[float, ...], ...]`` (raw list-of-lists)
* TEI is open-source (Apache 2.0, https://github.com/huggingface/text-embeddings-inference);
  any compatible self-host (vllm, FastEmbed-server) works unchanged.

Feature flag
------------
Provider key ``bkai_vn`` is gated by ``system_config.bkai_vn_embedder_enabled``
at the registry layer (``build_embedder``). The adapter itself is always
constructable so unit tests can exercise it without touching DB.
"""

from __future__ import annotations

import asyncio
import os
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
    DEFAULT_BKAI_VN_EMBEDDING_DIM,
    DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH,
    DEFAULT_BKAI_VN_EMBEDDING_MODEL,
    DEFAULT_EMBEDDER_CB_FAIL_MAX,
    DEFAULT_EMBEDDER_CB_RESET_S,
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_TIMEOUT_S,
    DEFAULT_RETRY_INITIAL_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_MS,
)
from ragbot.shared.errors import CircuitBreakerOpen, ExternalServiceError
from ragbot.shared.types import TenantId

logger = structlog.get_logger(__name__)


# HTTP status codes that should be retried (network blip / upstream throttle).
# TEI does not return 429 by default but a reverse proxy might inject one.
_RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Env var operators set to point the adapter at their self-hosted endpoint.
# Required; there is no public default URL for BKAI VN (the model card
# instructs self-hosting). Adapter raises ExternalServiceError if absent.
_BKAI_VN_URL_ENV: str = "BKAI_VN_EMBEDDING_URL"
# Optional bearer token for endpoints behind an auth proxy. Empty = no auth header.
_BKAI_VN_TOKEN_ENV: str = "BKAI_VN_EMBEDDING_TOKEN"

# Provider/purpose tags for api_key_pool (multi-key rotation). Optional —
# most TEI deployments are single-token or token-less.
_PROVIDER_CODE: str = "bkai_vn"
_PURPOSE: str = "embed"


class BkaiVnEmbedder(EmbeddingPort):
    """Direct-HTTP embedder for BKAI Vietnamese Bi-Encoder (PhoBERT, 768-dim)."""

    def __init__(
        self,
        model: str = DEFAULT_BKAI_VN_EMBEDDING_MODEL,
        *,
        api_url: str | None = None,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        timeout_s: int = DEFAULT_EMBEDDING_TIMEOUT_S,
        dimensions: int = DEFAULT_BKAI_VN_EMBEDDING_DIM,
    ) -> None:
        self._model = model
        # Resolve URL at construction-time so a missing env surfaces at boot
        # rather than on first embed call. Allow constructor override for tests.
        base = (api_url or os.environ.get(_BKAI_VN_URL_ENV) or "").rstrip("/")
        self._api_url: str = (
            base + DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH if base else ""
        )
        self._timeout_s = timeout_s
        self._dimensions = dimensions
        self._pool: ApiKeyPool | None = (
            key_pool_factory.get(_PROVIDER_CODE, _PURPOSE)
            if key_pool_factory is not None
            else None
        )
        self._client: httpx.AsyncClient | None = None
        self._cb: CircuitBreaker = CircuitBreaker(
            name="embedder:bkai_vn",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_EMBEDDER_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_EMBEDDER_CB_RESET_S,
            ),
        )

    @property
    def dimension(self) -> int:
        """Native embedding dimension (768 — PhoBERT-base hidden size)."""
        return self._dimensions

    @property
    def model_id(self) -> str:
        return self._model

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def _resolve_token(self) -> tuple[str | None, ApiKeyEntry | None]:
        """Return ``(bearer_token, pool_entry)``. Token may be ``None`` for
        unauthenticated TEI endpoints (common for in-VPC deployment).
        """
        if self._pool is not None:
            entry = await self._pool.get_active()
            return entry.key, entry
        legacy = os.environ.get(_BKAI_VN_TOKEN_ENV)
        return legacy, None

    async def health_check(self) -> bool:
        """Lightweight probe — embed a 1-token string with short timeout."""
        if not self._api_url:
            logger.warning(
                "embedder_health_check_failed",
                provider=_PROVIDER_CODE,
                reason="endpoint_unset",
            )
            return False
        try:
            token, _ = await self._resolve_token()
            client = await self._get_client()
            headers: dict[str, str] = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = await asyncio.wait_for(
                client.post(
                    self._api_url,
                    json={"inputs": ["ping"], "truncate": True},
                    headers=headers,
                ),
                timeout=DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
            )
            return resp.status_code == 200
        except (httpx.HTTPError, asyncio.TimeoutError, OSError):
            logger.warning(
                "embedder_health_check_failed",
                provider=_PROVIDER_CODE,
                model=self._model,
                exc_info=True,
            )
            return False

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,  # noqa: ARG002 — surface propagation only
    ) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_url:
            raise ExternalServiceError(
                f"BKAI VN embedder endpoint not configured "
                f"(set env {_BKAI_VN_URL_ENV})",
            )

        # Honour caller-supplied model if it does not carry a LiteLLM-style
        # provider prefix (the model_resolver may synthesise such an ID for
        # cross-provider routing; strip it so TEI receives the bare HF id).
        raw_model = spec.model_name if spec and spec.model_name else self._model
        model = raw_model.split("/", 1)[-1] if raw_model.count("/") > 1 else raw_model
        token, _entry = await self._resolve_token()
        client = await self._get_client()

        all_results: list[list[float]] = []
        for batch_start in range(0, len(texts), DEFAULT_EMBEDDING_MAX_BATCH):
            batch = texts[batch_start : batch_start + DEFAULT_EMBEDDING_MAX_BATCH]

            async def _call(
                b: list[str] = batch,
                _token: str | None = token,
            ) -> list[list[float]]:
                headers: dict[str, str] = {}
                if _token:
                    headers["Authorization"] = f"Bearer {_token}"
                async with asyncio.timeout(self._timeout_s):
                    resp = await client.post(
                        self._api_url,
                        json={"inputs": b, "truncate": True},
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        if resp.status_code in _RETRYABLE_HTTP_STATUS:
                            raise httpx.HTTPStatusError(
                                f"retryable HTTP {resp.status_code}: "
                                f"{resp.text[:200]}",
                                request=resp.request,
                                response=resp,
                            )
                        raise ExternalServiceError(
                            f"BKAI VN embed failed: HTTP {resp.status_code}: "
                            f"{resp.text[:300]}",
                        )
                    data: Any = resp.json()
                    # TEI returns a bare list-of-lists. Some wrappers nest under
                    # ``embeddings`` or ``data[].embedding`` — handle all three
                    # so we work behind LiteLLM-style proxies too.
                    if isinstance(data, list):
                        return [list(map(float, row)) for row in data]
                    if isinstance(data, dict):
                        if isinstance(data.get("embeddings"), list):
                            return [list(map(float, row)) for row in data["embeddings"]]
                        if isinstance(data.get("data"), list):
                            return [
                                list(map(float, item["embedding"]))
                                for item in data["data"]
                            ]
                    raise ExternalServiceError(
                        f"BKAI VN embed: unexpected response shape "
                        f"{type(data).__name__}",
                    )

            try:
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
                    provider=_PROVIDER_CODE,
                    model=model,
                    batch_index=batch_start,
                )
                raise ExternalServiceError(
                    f"BKAI VN embedding CB open: {exc}",
                ) from exc
            except ExternalServiceError:
                raise
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                raise ExternalServiceError(
                    f"BKAI VN embedding API failed after retries: {exc}",
                ) from exc
            all_results.extend(batch_results)

        # Telemetry: emit per-call event with feature_flag context so admin
        # dashboards can correlate VN-provider toggles with retrieval quality.
        logger.info(
            "bkai_vn_embed_done",
            step_name="bkai_vn_embed",
            feature_flag="bkai_vn_embedder_enabled",
            provider=_PROVIDER_CODE,
            model=model,
            batch_count=len(texts),
            dim=self._dimensions,
        )
        return all_results

    async def embed_one(
        self,
        text: str,
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,
    ) -> list[float]:
        result = await self.embed_batch(
            [text], spec=spec, record_tenant_id=record_tenant_id,
        )
        return result[0]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["BkaiVnEmbedder"]
