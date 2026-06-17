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

# """NullConvoSummary — Null Object pattern for the convo summary strategy.

# Default-OFF baseline. Returns an empty string regardless of input so callers
# can treat "no summary configured" and "summary produced empty" identically
# without raising. Selecting this implementation is a deliberate operator
# choice (or the platform default until the owner opts in).
# """

# from __future__ import annotations

# import structlog

# from ragbot.application.ports.convo_summary_port import Turn

# logger = structlog.get_logger(__name__)


# class NullConvoSummary:
#     """No-op convo summary — always returns ``""``."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def summarise(self, turns: list[Turn], max_tokens: int) -> str:
#         """Return ``""`` regardless of input.

#         Empty string signals "no summary" to the caller; the platform never
#         auto-injects this into an LLM prompt.
#         """
#         logger.debug(
#             "null_convo_summary_bypass",
#             turns=len(turns),
#             max_tokens=max_tokens,
#         )
#         return ""


# __all__ = ["NullConvoSummary"]
