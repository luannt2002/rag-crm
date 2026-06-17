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
# Reason: Part of unused multi_agent_review subpackage.
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

# from __future__ import annotations

# from dataclasses import dataclass, field
# from enum import StrEnum
# from typing import Protocol, runtime_checkable

# from ragbot.shared.types import TenantId, TraceId


# class AgentRole(StrEnum):
#     ARCHITECT = "architect"
#     RAG_SPECIALIST = "rag_specialist"
#     VIETNAMESE_LINGUIST = "vietnamese_linguist"
#     QUALITY_GUARDIAN = "quality_guardian"
#     EVALUATOR = "evaluator"
#     CRITIC = "critic"
#     AUDITOR = "auditor"


# class ReviewVerdict(StrEnum):
#     APPROVED = "approved"
#     APPROVED_WITH_FIX = "approved_with_fix"
#     REJECTED = "rejected"


# class ArtefactKind(StrEnum):
#     PLAN = "plan"
#     SYSPROMPT = "sysprompt"
#     CODE_DIFF = "code_diff"
#     PROMPT = "prompt"
#     GENERIC = "generic"


# @dataclass(frozen=True, slots=True)
# class ReviewArtefact:
#     text: str
#     kind: ArtefactKind = ArtefactKind.GENERIC
#     title: str = ""
#     metadata: dict[str, str] = field(default_factory=dict)


# @dataclass(frozen=True, slots=True)
# class AgentResponse:
#     role: AgentRole
#     summary: str
#     issues: list[str] = field(default_factory=list)
#     suggestions: list[str] = field(default_factory=list)
#     verdict: ReviewVerdict = ReviewVerdict.APPROVED_WITH_FIX
#     risks: list[str] = field(default_factory=list)
#     raw: str = ""
#     tokens_in: int = 0
#     tokens_out: int = 0
#     cost_usd: float = 0.0
#     latency_ms: int = 0


# @runtime_checkable
# class AgentPort(Protocol):
#     role: AgentRole

#     async def review(
#         self,
#         artefact: ReviewArtefact,
#         *,
#         prior: list[AgentResponse],
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> AgentResponse: ...


# __all__ = [
#     "AgentPort",
#     "AgentResponse",
#     "AgentRole",
#     "ArtefactKind",
#     "ReviewArtefact",
#     "ReviewVerdict",
# ]
