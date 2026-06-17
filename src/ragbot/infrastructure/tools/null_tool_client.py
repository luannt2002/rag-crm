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
# Reason: tools infra never wired in bootstrap or graph.
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

# """NullToolClient — tools-disabled Strategy default.

# Returns an empty tool catalogue and a sentinel error payload when called.
# Selecting this strategy is the operator-OFF baseline.
# """

# from __future__ import annotations


# class NullToolClient:
#     """No-op tool client — list returns ``[]``, call returns sentinel."""

#     def __init__(self, **_: object) -> None:
#         return

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def list_tools(self) -> list[dict]:
#         return []

#     async def call(self, tool_name: str, args: dict) -> dict:  # noqa: ARG002
#         return {"error": "tools_disabled"}


# __all__ = ["NullToolClient"]
