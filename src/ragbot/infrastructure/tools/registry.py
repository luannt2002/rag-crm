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

# WIRED: not yet — answer_question integration (tool-use loop).
# """Tool client strategy registry — DI factory based on provider key.

# NOTE: as of commit 74a4dfe this registry is shipped but **not called
# from any production hot-path**. The answer_question pipeline does not
# yet pull a ``ToolClientPort`` from DI; tools remain off until threads ``build_tool_client`` through the orchestration graph.
# """

# from __future__ import annotations

# from typing import TYPE_CHECKING

# import structlog

# from ragbot.infrastructure.tools.mcp_tool_client import McpToolClient
# from ragbot.infrastructure.tools.null_tool_client import NullToolClient

# if TYPE_CHECKING:
#     from ragbot.application.ports.tool_client_port import ToolClientPort

# logger = structlog.get_logger(__name__)


# _REGISTRY: dict[str, type] = {
#     "null": NullToolClient,
#     "mcp": McpToolClient,
# }


# def build_tool_client(
#     provider: str | None = None,
#     **kwargs,
# ) -> "ToolClientPort":
#     key = (provider or "").strip().lower() or "null"
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         logger.warning(
#             "tool_client_unknown_provider_fallback_null",
#             requested=provider,
#             registered=sorted(_REGISTRY.keys()),
#         )
#         cls = NullToolClient
#     try:
#         return cls(**kwargs)  # type: ignore[return-value]
#     except (ImportError, NotImplementedError) as exc:
#         logger.error(
#             "tool_client_strategy_not_installed",
#             requested=key,
#             error=str(exc),
#         )
#         return NullToolClient(**kwargs)


# def list_providers() -> list[str]:
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_tool_client", "list_providers"]
