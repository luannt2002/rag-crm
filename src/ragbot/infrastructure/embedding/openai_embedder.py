# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: Not registered in embedding/registry.py. OpenAIEmbedder class has no callers.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """OpenAI cloud embedder — secondary strategy for failover.

# Routes the call through ``litellm.aembedding`` using the OpenAI namespace.
# Default model name + dimension are constants pulled from ``shared/constants``;
# ``model_name`` constructor kwarg / system_config can swap variants without
# code edits.

# Implements both:

# * ``EmbedderPort``  — minimal contract used by ``FailoverEmbedder``.
# * ``EmbeddingPort`` — orchestrator surface (``embed_one`` / ``embed_batch``)
#   so the orchestrator can call this adapter directly without changes to
#   ``query_graph``.

# CircuitBreaker wraps the retry loop. After ``DEFAULT_EMBEDDER_CB_FAIL_MAX``
# consecutive failures the breaker opens and ``embed_*`` raises
# ``CircuitBreakerOpen`` (subclass of ``InfrastructureError``) — the failover
# wrapper catches both ``CircuitBreakerOpen`` and ``EmbeddingError`` to fall
# through to the next strategy. Domain-neutral; no tenant-specific literals.
# """

# from __future__ import annotations

# import asyncio
# import os
# from typing import Any

# import litellm
# import structlog

# from ragbot.application.dto.ai_specs import EmbeddingSpec
# from ragbot.application.ports.embedder_port import EmbedderPort
# from ragbot.application.ports.embedding_port import EmbeddingPort
# from ragbot.application.services.retry_policy import (
#     CircuitBreaker,
#     CircuitBreakerPolicy,
#     RetryPolicy,
#     retry_with_backoff,
# )
# from ragbot.shared.constants import (
#     DEFAULT_EMBEDDER_CB_FAIL_MAX,
#     DEFAULT_EMBEDDER_CB_RESET_S,
#     DEFAULT_EMBEDDER_HEALTHCHECK_TIMEOUT_S,
#     DEFAULT_EMBEDDING_MAX_BATCH,
#     DEFAULT_EMBEDDING_TIMEOUT_S,
#     DEFAULT_OPENAI_EMBEDDING_DIMENSION,
#     DEFAULT_OPENAI_EMBEDDING_MODEL,
#     DEFAULT_RETRY_INITIAL_MS,
#     DEFAULT_RETRY_MAX_ATTEMPTS,
#     DEFAULT_RETRY_MAX_MS,
# )
# from ragbot.shared.errors import (
#     CircuitBreakerOpen,
#     EmbeddingError,
# )
# from ragbot.shared.types import TenantId

# logger = structlog.get_logger(__name__)


# class OpenAIEmbedder(EmbedderPort, EmbeddingPort):
#     """OpenAI cloud embedder via LiteLLM (cosmetic registry alias).

#     @param model_name: LiteLLM model identifier (default
#         ``text-embedding-3-small``). The dimension matches the model's
#         native output unless an explicit ``dimension`` override is passed.
#     @param api_key: Optional inline override; falls back to the
#         ``OPENAI_API_KEY`` env var (LiteLLM's default lookup) when unset.
#     """

#     _MODEL_ID_PREFIX = "openai-"

#     def __init__(
#         self,
#         model_name: str = DEFAULT_OPENAI_EMBEDDING_MODEL,
#         *,
#         api_key: str | None = None,
#         dimension: int = DEFAULT_OPENAI_EMBEDDING_DIMENSION,
#     ) -> None:
#         self._model = model_name
#         self._dimension = int(dimension)
        # Resolve from env at construct-time so credential rotation reaches
        # the adapter without a process restart for the very first call;
        # later rotations are still picked up by litellm's own per-call lookup.
#         self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or None
#         self._cb: CircuitBreaker = CircuitBreaker(
#             name=f"embedder:openai:{model_name}",
#             policy=CircuitBreakerPolicy(
#                 fail_max=DEFAULT_EMBEDDER_CB_FAIL_MAX,
#                 reset_timeout_s=DEFAULT_EMBEDDER_CB_RESET_S,
#             ),
#         )

    # --- Port metadata -----------------------------------------------------
#     @property
#     def dimension(self) -> int:
#         return self._dimension

#     @property
#     def model_id(self) -> str:
        # Strip the LiteLLM ``openai/`` prefix if present so the metric label
        # stays readable. ``-`` separates registry-level brand from concrete
        # model variant.
#         bare = self._model.split("/", 1)[-1]
#         return f"{self._MODEL_ID_PREFIX}{bare}"

    # --- Health probe ------------------------------------------------------
#     async def health_check(self) -> bool:
#         try:
#             resp = await asyncio.wait_for(
#                 litellm.aembedding(
#                     model=self._model,
#                     input=["health"],
#                     **self._extra_kwargs(),
#                 ),
#                 timeout=DEFAULT_EMBEDDER_HEALTHCHECK_TIMEOUT_S,
#             )
#             return bool(resp.data)
#         except (
#             asyncio.TimeoutError,
#             litellm.exceptions.AuthenticationError,
#             litellm.exceptions.RateLimitError,
#             litellm.exceptions.ServiceUnavailableError,
#             litellm.exceptions.APIConnectionError,
#             litellm.exceptions.APIError,
#             ConnectionError,
#             OSError,
#         ) as exc:
#             logger.warning(
#                 "openai_embedder_health_check_failed",
#                 model=self._model,
#                 error_type=type(exc).__name__,
#             )
#             return False

    # --- Internal ----------------------------------------------------------
#     def _extra_kwargs(self) -> dict[str, Any]:
#         kw: dict[str, Any] = {}
#         if self._api_key:
#             kw["api_key"] = self._api_key
#         return kw

#     async def _embed_raw(self, texts: list[str]) -> list[list[float]]:
#         """Issue the LiteLLM call inside CB+retry guard.

#         Translates LiteLLM's specific exception hierarchy into our
#         ``EmbeddingError`` / ``CircuitBreakerOpen`` so callers can
#         narrow-except cleanly.
#         """
#         if not texts:
#             return []

#         async def _call(b: list[str] = texts) -> list[list[float]]:
#             async with asyncio.timeout(DEFAULT_EMBEDDING_TIMEOUT_S):
#                 resp = await litellm.aembedding(
#                     model=self._model,
#                     input=b,
#                     **self._extra_kwargs(),
#                 )
#                 return [item["embedding"] for item in resp.data]

#         try:
#             with self._cb:
#                 return await retry_with_backoff(
#                     _call,
#                     policy=RetryPolicy(
#                         max_attempts=DEFAULT_RETRY_MAX_ATTEMPTS,
#                         initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS,
#                         max_backoff_ms=DEFAULT_RETRY_MAX_MS,
#                     ),
#                     retryable_exceptions=(
#                         OSError,
#                         ConnectionError,
#                         TimeoutError,
#                         litellm.exceptions.RateLimitError,
#                         litellm.exceptions.ServiceUnavailableError,
#                         litellm.exceptions.APIConnectionError,
#                     ),
#                 )
#         except CircuitBreakerOpen:
            # Propagate as-is; failover wrapper catches it.
#             raise
#         except (
#             litellm.exceptions.AuthenticationError,
#             litellm.exceptions.BadRequestError,
#             litellm.exceptions.NotFoundError,
#             litellm.exceptions.APIError,
#             asyncio.TimeoutError,
#             ValueError,
#             TypeError,
#             OSError,
#         ) as exc:
#             raise EmbeddingError(
#                 f"openai embed failed: {type(exc).__name__}: {exc}"
#             ) from exc

    # --- EmbedderPort surface ---------------------------------------------
#     async def embed_query(self, text: str) -> list[float]:
#         if not text:
#             raise EmbeddingError("openai embed_query: empty text")
#         out = await self._embed_raw([text])
#         return out[0] if out else []

#     async def embed_documents(self, texts: list[str]) -> list[list[float]]:
#         if not texts:
#             return []
        # Respect provider batch cap; concat sub-batches.
#         all_results: list[list[float]] = []
#         for start in range(0, len(texts), DEFAULT_EMBEDDING_MAX_BATCH):
#             batch = texts[start : start + DEFAULT_EMBEDDING_MAX_BATCH]
#             all_results.extend(await self._embed_raw(batch))
#         return all_results

    # --- EmbeddingPort surface ---------------------------------------------
#     async def embed_one(
#         self,
#         text: str,
#         *,
#         spec: EmbeddingSpec | None = None,
#         record_tenant_id: TenantId | None = None,  # noqa: ARG002
#     ) -> list[float]:
        # Spec may override model name (DB-driven AI config). Honour it.
#         if spec is not None and getattr(spec, "model_name", None):
#             self._model = spec.model_name
#         return await self.embed_query(text)

#     async def embed_batch(
#         self,
#         texts: list[str],
#         *,
#         spec: EmbeddingSpec | None = None,
#         record_tenant_id: TenantId | None = None,  # noqa: ARG002
#     ) -> list[list[float]]:
#         if spec is not None and getattr(spec, "model_name", None):
#             self._model = spec.model_name
#         return await self.embed_documents(texts)

#     async def close(self) -> None:
#         return None


# __all__ = ["OpenAIEmbedder"]
