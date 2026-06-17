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
# Reason: Multi-vector stack never plumbed into bootstrap or graph.
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

# """Multi-vector embedder strategy registry — Port + Strategy + Registry.

# Maps the system_config key ``multi_vector_provider`` to a
# :class:`MultiVectorEmbedPort` adapter. Default ``"null"`` preserves the
# single-vector retrieval behaviour; flip to ``"sentence_split"`` to enable
# the late-interaction scaffold. Full ColBERT-style adapters land in later
# phases — adding one is a single new file plus one row in ``_REGISTRY``;
# orchestrator code is untouched.

# Fail-soft contract:
# * Unknown / empty provider key → log + fall back to ``NullMultiVectorEmbedder``
#   so a stale system_config row cannot crash worker boot.
# * Strategy constructor raising on init → also fall back to the null path,
#   same reasoning.
# """

# from __future__ import annotations

# import inspect
# from typing import Any, Final

# import structlog

# from ragbot.application.ports.embedder_port import EmbedderPort
# from ragbot.application.ports.multi_vector_embed_port import MultiVectorEmbedPort
# from ragbot.infrastructure.embedding.null_multi_vector import NullMultiVectorEmbedder
# from ragbot.infrastructure.embedding.sentence_split_multi_vector import (
#     SentenceSplitMultiVectorEmbedder,
# )
# from ragbot.shared.constants import DEFAULT_MULTI_VECTOR_PROVIDER

# logger = structlog.get_logger(__name__)


# _REGISTRY: Final[dict[str, type[MultiVectorEmbedPort]]] = {
#     "null": NullMultiVectorEmbedder,
#     "sentence_split": SentenceSplitMultiVectorEmbedder,
# }


# def build_multi_vector_embedder(
#     *,
#     provider: str | None = None,
#     inner: EmbedderPort | None = None,
#     **kwargs: Any,
# ) -> MultiVectorEmbedPort:
#     """Construct the multi-vector embedder matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"sentence_split"`` | ...).
#         ``None``/empty/unknown falls back to ``NullMultiVectorEmbedder``.
#     @param inner: single-vector :class:`EmbedderPort` used by strategies
#         that wrap a real embedder (sentence_split). The null path tolerates
#         ``inner=None`` so callers can defer wiring until the bootstrap
#         container has resolved the inner embedder.
#     @param kwargs: forwarded to the strategy constructor, filtered to the
#         signature so global kwargs cannot crash null/passthrough adapters.
#     """
#     key = (provider or DEFAULT_MULTI_VECTOR_PROVIDER).strip().lower() or "null"
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         logger.warning(
#             "multi_vector_unknown_provider_fallback_null",
#             requested=provider,
#             registered=sorted(_REGISTRY.keys()),
#         )
#         cls = NullMultiVectorEmbedder

#     candidate_kwargs: dict[str, Any] = {"inner": inner, **kwargs}
#     sig_params = set(inspect.signature(cls.__init__).parameters)
#     filtered = {k: v for k, v in candidate_kwargs.items() if k in sig_params}
#     try:
#         return cls(**filtered)  # type: ignore[return-value]
#     except (TypeError, ValueError) as exc:
#         logger.error(
#             "multi_vector_strategy_init_failed",
#             requested=key,
#             error=str(exc),
#         )
#         return NullMultiVectorEmbedder(inner=inner)


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_multi_vector_embedder", "list_providers"]
