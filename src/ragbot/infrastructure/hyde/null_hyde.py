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
# Reason: HyDE infra never wired.
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

# """NullHyDEGenerator — Null Object for the HyDE strategy.

# Default-OFF baseline. ``generate(query)`` returns the input verbatim so
# callers can wire ``await hyde.generate(q)`` unconditionally and still pay
# zero LLM cost until the bot owner opts in. Selecting this implementation
# is a deliberate operator choice (or the platform default until opt-in).
# """

# from __future__ import annotations

# import structlog

# logger = structlog.get_logger(__name__)


# class NullHyDEGenerator:
#     """No-op HyDE — always returns the input ``query`` unchanged."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def generate(self, query: str) -> str:
#         """Return ``query`` verbatim.

#         The retrieve pipeline then embeds the raw query — i.e. legacy
#         behaviour. Logged at debug so an operator can confirm the Null
#         branch is in effect without spamming hot-path logs.
#         """
#         logger.debug("null_hyde_bypass", query_chars=len(query))
#         return query


# __all__ = ["NullHyDEGenerator"]
