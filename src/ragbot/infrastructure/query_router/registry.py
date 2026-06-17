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
# Reason: query_router infra never wired in bootstrap or graph.
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

# """Query router strategy registry — DI factory keyed by provider name.

# Pattern (matches ``self_rag_router/registry.py``): caller (the
# ``bootstrap.Container``) reads ``query_router_provider`` from
# ``system_config`` (Redis-cached) and asks this registry for the
# matching ``QueryRouterPort`` implementation. Adding a new strategy =
# drop a new file in this package and register it here; orchestrator
# code never branches on provider name.

# Unknown / empty provider raises ``ValueError`` so misconfiguration is
# loud at boot rather than silently downgrading the routing decision.
# """

# from __future__ import annotations

# from typing import TYPE_CHECKING, Any

# from ragbot.infrastructure.query_router.llm_query_router import LLMQueryRouter
# from ragbot.infrastructure.query_router.null_query_router import NullQueryRouter
# from ragbot.infrastructure.query_router.regex_query_router import RegexQueryRouter

# if TYPE_CHECKING:
#     from ragbot.application.ports.query_router_port import QueryRouterPort


# Registered strategies — values are classes so each ``build_*`` call
# returns a fresh instance (DI container may wrap as Singleton).
# _REGISTRY: dict[str, type] = {
#     "null": NullQueryRouter,
#     "regex": RegexQueryRouter,
#     "llm": LLMQueryRouter,
# }


# def build_query_router(
#     provider: str,
#     **kwargs: Any,
# ) -> QueryRouterPort:
#     """Construct the router strategy matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"regex"`` | ``"llm"``).
#     @param kwargs: forwarded to the strategy constructor.
#     @return: ``QueryRouterPort`` instance.
#     @raise ValueError: when ``provider`` is not a registered key.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown query_router provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}"
#         )
#     return cls(**kwargs)  # type: ignore[no-any-return]


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_query_router", "list_providers"]
