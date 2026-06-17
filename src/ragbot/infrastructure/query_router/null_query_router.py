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

# """NullQueryRouter — Null Object for pre-retrieve intent classification.

# Default OFF strategy: always returns ``"semantic"`` so the orchestrator
# keeps its existing retrieve+rerank path until the operator flips
# ``system_config.query_router_provider`` to a real strategy.
# """

# from __future__ import annotations

# from ragbot.application.ports.query_router_port import QueryIntent
# from ragbot.shared.constants import QUERY_INTENT_SEMANTIC


# class NullQueryRouter:
#     """No-op router — always returns the ``semantic`` default label."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def classify(self, query: str) -> QueryIntent:
        # Null Object: ignore the input, always emit the catch-all label.
#         del query
#         return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]


# __all__ = ["NullQueryRouter"]
