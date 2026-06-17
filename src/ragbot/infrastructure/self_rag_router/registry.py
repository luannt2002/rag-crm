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
# Reason: self_rag_router never wired.
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

# """Self-RAG router strategy registry — DI factory keyed by provider name.

# Pattern: caller (``bootstrap.Container``) reads
# ``self_rag_router_provider`` from ``system_config`` (Redis-cached) and
# asks the registry for the matching ``SelfRagRouterPort`` implementation.
# Adding a new strategy = drop a new file in this package and register
# it here; orchestrator code never branches on provider name.

# Unknown provider raises ``ValueError`` so misconfiguration is loud at
# boot rather than silently downgrading the routing decision.
# """

# from __future__ import annotations

# from typing import TYPE_CHECKING, Any

# from ragbot.infrastructure.self_rag_router.intent_based_self_rag_router import (
#     IntentBasedSelfRagRouter,
# )
# from ragbot.infrastructure.self_rag_router.null_self_rag_router import (
#     NullSelfRagRouter,
# )

# if TYPE_CHECKING:
#     from ragbot.application.ports.self_rag_router_port import SelfRagRouterPort


# Registered strategies — values are classes so each ``build_*`` call
# returns a fresh instance (DI container may wrap as Singleton).
# _REGISTRY: dict[str, type] = {
#     "null": NullSelfRagRouter,
#     "intent": IntentBasedSelfRagRouter,
# }


# def build_self_rag_router(
#     provider: str,
#     **kwargs: Any,
# ) -> SelfRagRouterPort:
#     """Construct the router strategy matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"intent"``).
#     @param kwargs: forwarded to the strategy constructor.
#     @return: ``SelfRagRouterPort`` instance.
#     @raise ValueError: when ``provider`` is not a registered key.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown self_rag_router provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}"
#         )
#     return cls(**kwargs)  # type: ignore[no-any-return]


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_self_rag_router", "list_providers"]
