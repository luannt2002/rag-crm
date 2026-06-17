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

# """NullSelfRagRouter — Null Object pattern for adaptive routing.

# Default OFF strategy: never instructs the orchestrator to skip retrieve,
# so the pipeline keeps its existing behaviour until the operator flips
# ``system_config.self_rag_router_provider`` to a real strategy.
# """

# from __future__ import annotations


# class NullSelfRagRouter:
#     """No-op router — always runs the full retrieve pipeline."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     def should_skip_retrieve(self, intent: str, query: str) -> bool:
        # Null Object: ignore inputs, never skip — operator-OFF baseline.
#         del intent, query
#         return False


# __all__ = ["NullSelfRagRouter"]
