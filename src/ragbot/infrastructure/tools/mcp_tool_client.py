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

# """McpToolClient — STUB Strategy for Anthropic MCP clients.

# Production wiring requires:
#   pip install mcp  (Anthropic's MCP SDK; not yet vetted on this server)
#   + URL allow-list (system_config.mcp_server_url_allowlist regex)
#   + per-tenant SSRF guards.

# Default OFF. The constructor raises :class:`NotImplementedError` so the
# registry's fail-soft path falls back to NullToolClient and the install
# hint surfaces in logs.
# """

# from __future__ import annotations


# class McpToolClient:
#     """MCP tool client stub — raises until the SDK is installed."""

#     def __init__(self, **_: object) -> None:
#         raise NotImplementedError(
#             "MCP tool client requires the `mcp` SDK and an allow-listed "
#             "server URL (system_config.mcp_server_url_allowlist). "
#             "Default OFF — see plans/260429-MCP-tools-rollout/plan.md "
#             "for the rollout checklist."
#         )

#     @staticmethod
#     def get_provider_name() -> str:
#         return "mcp"

#     async def list_tools(self) -> list[dict]:  # pragma: no cover — unreachable
#         raise NotImplementedError

#     async def call(self, tool_name: str, args: dict) -> dict:  # pragma: no cover  # noqa: ARG002
#         raise NotImplementedError


# __all__ = ["McpToolClient"]
