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
# Reason: chunk_quality infra never wired in bootstrap or graph.
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

# """Chunk-quality scorer registry — DI factory keyed on config provider name.

# Pattern mirrors ``infrastructure.hyde.registry``: the DI container reads
# ``chunk_quality_scorer_provider`` (or falls back to "null" when the
# feature flag is OFF) from ``system_config`` and asks the registry for
# the matching ``ChunkQualityScorerPort`` implementation.

# Adding a new provider = drop a new file in this package and register
# it here; **no edits to document_service**.

# Default = ``"null"`` (``NullChunkQualityScorer``) — operator-OFF
# baseline. Unknown provider strings fall back to ``NullChunkQualityScorer``
# with a warn log so a typo in DB config can never crash ingest; the
# feature is opt-in so a silent fallback is the right safety vs. surface
# trade-off (ingest stays alive; the operator notices in logs).
# """

# from __future__ import annotations

# from typing import Any

# import structlog

# from ragbot.application.ports.chunk_quality_port import ChunkQualityScorerPort
# from ragbot.infrastructure.chunk_quality.heuristic_chunk_quality_scorer import (
#     HeuristicChunkQualityScorer,
# )
# from ragbot.infrastructure.chunk_quality.null_chunk_quality_scorer import (
#     NullChunkQualityScorer,
# )

# logger = structlog.get_logger(__name__)

# _REGISTRY: dict[str, type] = {
#     "null": NullChunkQualityScorer,
#     "heuristic": HeuristicChunkQualityScorer,
# }


# def build_chunk_quality_scorer(
#     provider: str | None = None,
#     **kwargs: Any,
# ) -> ChunkQualityScorerPort:
#     """Construct the chunk-quality scorer matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"heuristic"``).
#         ``None`` / unknown / empty falls back to ``NullChunkQualityScorer``
#         (warned) — feature is opt-in, so a typo cannot break ingest.
#     @param kwargs: forwarded to the strategy constructor. Currently both
#         providers are constructor-arg-free; the signature is preserved
#         for parity with sister registries.
#     @return: ``ChunkQualityScorerPort`` instance.
#     """
#     key = (provider or "").strip().lower() or "null"
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         logger.warning(
#             "chunk_quality_scorer_unknown_provider_fallback_null",
#             requested=provider,
#             registered=sorted(_REGISTRY.keys()),
#         )
#         cls = NullChunkQualityScorer
#     try:
#         return cls(**kwargs)  # type: ignore[return-value]
#     except (TypeError, ValueError) as exc:
#         logger.error(
#             "chunk_quality_scorer_init_failed",
#             requested=key,
#             error=str(exc),
#         )
#         return NullChunkQualityScorer()


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_chunk_quality_scorer", "list_providers"]
