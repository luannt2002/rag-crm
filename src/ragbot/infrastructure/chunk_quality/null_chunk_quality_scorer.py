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

# """NullChunkQualityScorer — Null Object for the chunk-quality strategy.

# Default-OFF baseline. ``score(chunk)`` returns a uniform 1.0 result so
# callers can wire ``scorer.score(c)`` unconditionally and still pay zero
# cost (no string parsing, no langdetect import). Selecting this
# implementation is a deliberate operator choice (or the platform default
# until opt-in via ``system_config.chunk_quality_scoring_enabled``).

# When the registry returns this scorer, the call-site in
# ``document_service.ingest`` still records the step (with
# ``skipped=True``) so analytics dashboards see one step row per ingest
# regardless of whether scoring is enabled.
# """

# from __future__ import annotations

# import structlog

# from ragbot.application.ports.chunk_quality_port import ChunkQualityResult

# logger = structlog.get_logger(__name__)


# class NullChunkQualityScorer:
#     """No-op scorer — every chunk grades 1.0 (passes any threshold)."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     def score(self, chunk: str) -> ChunkQualityResult:  # noqa: ARG002 — Port contract
#         """Return uniform 1.0 across all sub-scores.

#         Aggregate 1.0 guarantees no chunk is ever skipped by the gate
#         when the operator has not opted in (or has the feature flag
#         OFF). Debug-level log so an operator can confirm the bypass
#         path is in effect without spamming hot-path logs.
#         """
#         return ChunkQualityResult(
#             score=1.0,
#             text_length_score=1.0,
#             language_confidence=1.0,
#             information_density=1.0,
#             no_corruption_flag=1.0,
#         )


# __all__ = ["NullChunkQualityScorer"]
