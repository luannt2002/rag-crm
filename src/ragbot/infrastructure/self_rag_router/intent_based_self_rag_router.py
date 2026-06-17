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

# """IntentBasedSelfRagRouter — skip retrieve for conversational intents.

# Strategy: when the upstream classifier labels the query with a
# conversational intent (greeting / chitchat / vu_vo), the LLM does not
# need retrieved chunks — the answer is governed by sysprompt + history
# alone. Skipping retrieve here removes embed + pgvector + RRF + rerank
# work from the hot path.

# Skip set is sourced from ``DEFAULT_SELF_RAG_SKIP_INTENTS`` so operators
# can override per-deployment via constants / system_config without
# touching this strategy.
# """

# from __future__ import annotations

# from collections.abc import Iterable

# from ragbot.shared.constants import DEFAULT_SELF_RAG_SKIP_INTENTS


# class IntentBasedSelfRagRouter:
#     """Skip-retrieve when intent is in the configured skip set."""

#     def __init__(self, skip_intents: Iterable[str] | None = None) -> None:
        # Defensive copy into a frozenset so mutating the input after
        # construction cannot leak into routing decisions.
#         self._skip_intents: frozenset[str] = frozenset(
#             skip_intents if skip_intents is not None else DEFAULT_SELF_RAG_SKIP_INTENTS
#         )

#     @staticmethod
#     def get_provider_name() -> str:
#         return "intent"

#     @property
#     def skip_intents(self) -> frozenset[str]:
#         """Expose the resolved skip set for observability / tests."""
#         return self._skip_intents

#     def should_skip_retrieve(self, intent: str, query: str) -> bool:
        # Intent label is the only signal; query is part of the Port
        # signature so future strategies (e.g. Self-RAG critique LLM)
        # can act on it without changing callers.
#         del query
#         return intent in self._skip_intents


# __all__ = ["IntentBasedSelfRagRouter"]
