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
# Reason: build_hyde never called.
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

# """HyDE strategy registry — DI factory keyed on config provider name.

# Pattern mirrors ``infrastructure.convo_summary.registry``: the DI container
# reads ``hyde_provider`` from ``system_config`` (Redis-cached) and asks the
# registry for the matching ``HyDEServicePort`` implementation. Adding a new
# provider = drop a new file in this package and register it here; **no
# edits to query_graph or chat_worker** — admin wiring binds the strategy
# when the operator opts in.

# Default = ``"null"`` (``NullHyDEGenerator``). Unknown provider strings
# raise ``ValueError`` — HyDE is opt-in, so a typo at the DB layer must
# surface loudly rather than silently fall back.
# """

# from __future__ import annotations

# from typing import Any

# from ragbot.application.ports.hyde_port import HyDEServicePort
# from ragbot.infrastructure.hyde.llm_hyde import LLMHyDEGenerator
# from ragbot.infrastructure.hyde.null_hyde import NullHyDEGenerator

# _REGISTRY: dict[str, type[HyDEServicePort]] = {
#     "null": NullHyDEGenerator,
#     "llm": LLMHyDEGenerator,
# }


# def build_hyde(provider: str, **kwargs: Any) -> HyDEServicePort:
#     """Construct the HyDE strategy matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"llm"``).
#     @param kwargs: forwarded to the strategy constructor (``llm=``,
#         ``spec=``, ``record_tenant_id=``, ``trace_id=`` for ``"llm"``;
#         ignored for ``"null"``).
#     @return: ``HyDEServicePort`` instance.
#     @raise ValueError: unknown provider key — owner-opt-in surfaces loud,
#         not silent fallback.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown hyde provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}"
#         )
#     instance: HyDEServicePort = cls(**kwargs)
#     return instance


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_hyde", "list_providers"]
