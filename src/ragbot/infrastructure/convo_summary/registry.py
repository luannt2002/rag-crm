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
# Reason: convo_summary infra never wired in bootstrap or graph.
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

# """ConvoSummary strategy registry — DI factory keyed on config provider name.

# Pattern mirrors ``infrastructure.reranker.registry``: caller (DI container)
# reads ``convo_summary_provider`` from ``system_config`` (Redis-cached) and
# asks the registry for the matching ``ConvoSummaryPort`` implementation.
# Adding a new provider = drop a new file in this package and register it
# here; **no edits to query_graph or chat_worker** — admin wiring binds the
# strategy when the bot owner opts in via ``bots.convo_summary_enabled``.

# Default = ``"null"`` (NullConvoSummary). Unknown provider strings raise
# ``ValueError`` — convo summary is owner-opt-in, so a typo at the DB layer
# must surface loudly rather than silently fall back (contrast with reranker,
# where a fail-soft fallback is preferred to keep retrieval running).
# """

# from __future__ import annotations

# from typing import Any

# from ragbot.application.ports.convo_summary_port import ConvoSummaryPort
# from ragbot.infrastructure.convo_summary.llm_convo_summary import LLMConvoSummary
# from ragbot.infrastructure.convo_summary.null_convo_summary import NullConvoSummary

# _REGISTRY: dict[str, type[ConvoSummaryPort]] = {
#     "null": NullConvoSummary,
#     "llm": LLMConvoSummary,
# }


# def build_convo_summary(provider: str, **kwargs: Any) -> ConvoSummaryPort:
#     """Construct the convo summary strategy matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"llm"``).
#     @param kwargs: forwarded to the strategy constructor (``llm=``, ``spec=``,
#         ``record_tenant_id=``, ``trace_id=`` for ``"llm"``; ignored for
#         ``"null"``).
#     @return: ``ConvoSummaryPort`` instance.
#     @raise ValueError: unknown provider key — owner-opt-in surfaces loud,
#         not silent fallback.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown convo_summary provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}"
#         )
#     instance: ConvoSummaryPort = cls(**kwargs)
#     return instance


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_convo_summary", "list_providers"]
