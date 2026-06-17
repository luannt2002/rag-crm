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
# Reason: CAG infra never wired in bootstrap or graph.
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

# """NullCAGService — Null Object for the CAG strategy.

# Default-OFF baseline. ``should_engage`` always returns False so the query
# graph stays on the standard retrieve/rerank/generate path. ``build_corpus_
# payload`` always returns ``None`` (the caller MUST honour the False from
# ``should_engage`` first — this is a belt-and-braces failsafe).

# Selecting this implementation is a *deliberate* operator choice (or the
# platform default until the bot owner opts in). The Null adapter performs
# zero I/O — no DB session, no corpus load — so the per-turn cost when CAG
# is off is just one logged decision.

# Citation: Chan et al. 2024 — "Don't Do RAG" (arXiv:2412.15605).
# """

# from __future__ import annotations

# import structlog

# from ragbot.application.ports.cag_port import CAGPayload
# from ragbot.shared.types import TenantId

# logger = structlog.get_logger(__name__)


# class NullCAGService:
#     """No-op CAG — always returns ``should_engage=False`` and no payload."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def should_engage(
#         self,
#         *,
#         record_tenant_id: TenantId,
#         record_bot_id: str,
#     ) -> bool:
#         """Always False — the retrieve hot path runs unchanged.

#         Logged at debug only so an operator can verify the Null branch
#         is engaged without flooding hot-path logs.
#         """
#         logger.debug(
#             "cag_lookup_null_bypass",
#             step_name="cag_lookup",
#             feature_flag="cag_mode_enabled",
#             record_tenant_id=str(record_tenant_id),
#             record_bot_id=record_bot_id,
#             engaged=False,
#         )
#         return False

#     async def build_corpus_payload(
#         self,
#         *,
#         record_tenant_id: TenantId,
#         record_bot_id: str,
#     ) -> CAGPayload | None:
#         """Always None — the corpus is never loaded under the Null strategy.

#         Callers that respect the ``should_engage=False`` contract above
#         never reach this method; the None return is a defensive belt
#         for misbehaving callers (return None instead of raising — CAG
#         is best-effort, never a hard dependency).
#         """
#         return None


# __all__ = ["NullCAGService"]
