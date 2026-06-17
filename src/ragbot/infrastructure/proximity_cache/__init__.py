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
# Reason: proximity_cache infra never wired in bootstrap or graph.
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

# """Proximity cache adapters — owner-opt-in semantic short-circuit for LLM.

# Strategy pattern: ``build_proximity_cache(provider, **kwargs)`` returns the
# matching implementation. Default provider = ``"null"``
# (:class:`NullProximityCache`, no-op bypass).
# """

# from ragbot.infrastructure.proximity_cache.lsh_proximity_cache import (
#     LSHProximityCache,
# )
# from ragbot.infrastructure.proximity_cache.null_proximity_cache import (
#     NullProximityCache,
# )
# from ragbot.infrastructure.proximity_cache.registry import (
#     build_proximity_cache,
#     list_providers,
# )

# __all__: list[str] = [
#     "LSHProximityCache",
#     "NullProximityCache",
#     "build_proximity_cache",
#     "list_providers",
# ]
