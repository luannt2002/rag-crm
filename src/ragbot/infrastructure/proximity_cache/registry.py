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

# """Proximity cache strategy registry — DI factory based on provider key.

# Pattern mirrors :mod:`ragbot.infrastructure.reranker.registry`. Caller (DI
# container) reads ``proximity_cache_provider`` from ``system_config`` and asks
# the registry for the matching :class:`ProximityCachePort` implementation.
# Adding a new provider = drop a new file in this package and register it
# here; no edits to orchestration code.

# Default = ``"null"`` (:class:`NullProximityCache`) — owner-opt-in baseline.
# Unknown provider strings raise :class:`ValueError` so a typo in
# ``system_config`` is loud at boot rather than silently disabling the cache.
# """

# from __future__ import annotations

# import inspect
# from typing import Any

# from ragbot.application.ports.proximity_cache_port import ProximityCachePort
# from ragbot.infrastructure.proximity_cache.lsh_proximity_cache import (
#     LSHProximityCache,
# )
# from ragbot.infrastructure.proximity_cache.null_proximity_cache import (
#     NullProximityCache,
# )

# _REGISTRY: dict[str, type[ProximityCachePort]] = {
#     "null": NullProximityCache,
#     "lsh": LSHProximityCache,
# }


# def build_proximity_cache(
#     provider: str | None = None,
#     **kwargs: Any,
# ) -> ProximityCachePort:
#     """Construct the proximity cache matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"lsh"``). ``None`` / empty
#         falls back to ``"null"``.
#     @param kwargs: forwarded to the strategy constructor (e.g.
#         ``similarity_threshold=``). Kwargs the constructor does not accept
#         are filtered out so a globally-passed kwarg blob doesn't blow up the
#         Null adapter.
#     @return: :class:`ProximityCachePort` instance.
#     @raise ValueError: when ``provider`` is non-empty and not registered.
#     """
#     key = (provider or "").strip().lower() or "null"
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown proximity_cache provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}",
#         )
#     sig_params = set(inspect.signature(cls).parameters)
#     filtered = {k: v for k, v in kwargs.items() if k in sig_params}
#     return cls(**filtered)


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_proximity_cache", "list_providers"]
