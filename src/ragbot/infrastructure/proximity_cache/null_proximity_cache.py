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

# """NullProximityCache — Null Object for the proximity-cache strategy.

# Default when the operator has not opted into proximity caching
# (``proximity_cache_provider="null"`` in system_config). Lookup always misses;
# store is a no-op. Selecting NullProximityCache is a deliberate choice: it
# keeps the wire-up identical for callers regardless of whether the bot owner
# has enabled the cost-saver path.
# """

# from __future__ import annotations

# import structlog

# from ragbot.application.ports.proximity_cache_port import CacheHit

# logger = structlog.get_logger(__name__)


# class NullProximityCache:
#     """No-op proximity cache — every lookup misses, every store is dropped."""

#     def __init__(self) -> None:
        # No state. Constructor accepts no kwargs so the registry's filtered
        # call from a generic kwargs blob still succeeds.
#         ...

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     def lookup(self, query_embedding: list[float]) -> CacheHit | None:
        # Operator-disabled default — never short-circuits the LLM call.
#         logger.debug("null_proximity_cache_miss", dim=len(query_embedding))
#         return None

#     def store(self, query_embedding: list[float], answer: str, ttl_s: int) -> None:
        # Drop on the floor. Callers must remain correct when store is a no-op.
#         logger.debug(
#             "null_proximity_cache_store_dropped",
#             dim=len(query_embedding),
#             answer_chars=len(answer),
#             ttl_s=ttl_s,
#         )


# __all__ = ["NullProximityCache"]
