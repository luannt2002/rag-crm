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

# """HeuristicChunkQualityScorer — re-exported via the registry.

# The actual heuristic lives in ``ragbot.shared.chunk_quality`` so unit
# tests + non-DI callers can score without depending on the
# infrastructure layer. This thin module exists so the registry pattern
# mirrors the rest of the codebase (one file per provider in
# ``infrastructure/<thing>/<provider>_<thing>.py``).
# """

# from __future__ import annotations

# from ragbot.shared.chunk_quality import HeuristicChunkQualityScorer

# __all__ = ["HeuristicChunkQualityScorer"]
