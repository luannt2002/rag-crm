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
# Reason: sentence_similarity infra never wired.
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

# """Sentence-similarity strategy package (registry + adapters).

# Public entrypoint: :func:`build_sentence_similarity` resolves a
# :class:`SentenceSimilarityPort` from the ``sentence_similarity_provider``
# key in ``system_config``. Adding a provider = drop a file in this package
# + register in :mod:`ragbot.infrastructure.sentence_similarity.registry`.
# """
# from ragbot.infrastructure.sentence_similarity.registry import (
#     build_sentence_similarity,
#     list_providers,
# )

# __all__ = ["build_sentence_similarity", "list_providers"]
