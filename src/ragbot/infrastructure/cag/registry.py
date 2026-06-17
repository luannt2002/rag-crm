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
# Reason: build_cag never called outside this directory.
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

# """CAG strategy registry — DI factory keyed on config provider name.

# Pattern mirrors ``infrastructure.hyde.registry``: the DI container reads
# ``cag_provider`` from ``system_config`` (Redis-cached) and asks the
# registry for the matching ``CAGServicePort`` implementation. Adding a new
# provider = drop a new file in this package and register it here; **no
# edits to query_graph or chat_worker** — admin wiring binds the strategy
# when the operator opts in.

# Default = ``"null"`` (``NullCAGService``). Unknown provider strings
# raise ``ValueError`` — CAG is opt-in, so a typo at the DB layer must
# surface loudly rather than silently fall back to RAG (the RAG fallback
# is already the safe default; we want the loud error so ops notice the
# config drift).

# Citation: Chan et al. 2024 — "Don't Do RAG" (arXiv:2412.15605).
# """

# from __future__ import annotations

# from typing import Any

# from ragbot.application.ports.cag_port import CAGServicePort
# from ragbot.infrastructure.cag.anthropic_cag import AnthropicCAGService
# from ragbot.infrastructure.cag.null_cag import NullCAGService

# _REGISTRY: dict[str, type[CAGServicePort]] = {
#     "null": NullCAGService,
#     "anthropic": AnthropicCAGService,
# }


# def build_cag(provider: str, **kwargs: Any) -> CAGServicePort:
#     """Construct the CAG strategy matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"anthropic"``).
#     @param kwargs: forwarded to the strategy constructor. ``"anthropic"``
#         requires ``corpus_loader=``, ``enabled=``, ``max_corpus_tokens=``;
#         ``"null"`` accepts no kwargs (extras are silently dropped to keep
#         the registry call site uniform).
#     @return: ``CAGServicePort`` instance.
#     @raise ValueError: unknown provider key — owner-opt-in surfaces loud,
#         not silent fallback to RAG.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown cag provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}",
#         )
#     if cls is NullCAGService:
        # Null Object takes no kwargs — drop everything caller passed so the
        # registry call site can stay uniform across providers.
#         instance: CAGServicePort = NullCAGService()
#     else:
#         instance = cls(**kwargs)
#     return instance


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_cag", "list_providers"]
