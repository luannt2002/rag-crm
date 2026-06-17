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

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.llm_port import LLMMessage, LLMPort
# from ragbot.application.services.multi_agent_review.agent_port import (
#     AgentResponse,
#     AgentRole,
#     ReviewArtefact,
# )
# from ragbot.application.services.multi_agent_review.parser import parse_agent_response
# from ragbot.application.services.multi_agent_review.prompts import (
#     build_specialist_messages,
# )
# from ragbot.shared.types import TenantId, TraceId


# class SpecialistAgent:
#     """One Strategy class, role distinguished at construction.

#     Adding a new specialist = registering a new `AgentRole` + entry in
#     `prompts._ROLE_BRIEF` + entry in `registry.build_default_review_team`.
#     No subclass-per-agent factory churn needed.
#     """

#     role: AgentRole

#     def __init__(
#         self,
#         role: AgentRole,
#         *,
#         llm: LLMPort,
#         spec: LLMSpec,
#     ) -> None:
#         if role is AgentRole.AUDITOR:
#             raise ValueError("SpecialistAgent rejects AUDITOR role; use AuditorAgent")
#         self.role = role
#         self._llm = llm
#         self._spec = spec

#     async def review(
#         self,
#         artefact: ReviewArtefact,
#         *,
#         prior: list[AgentResponse],
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> AgentResponse:
#         system, user = build_specialist_messages(self.role, artefact, prior)
#         resp = await self._llm.complete(
#             messages=[
#                 LLMMessage(role="system", content=system),
#                 LLMMessage(role="user", content=user),
#             ],
#             spec=self._spec,
#             record_tenant_id=record_tenant_id,
#             trace_id=trace_id,
#         )
#         return parse_agent_response(
#             self.role,
#             resp.content,
#             tokens_in=resp.tokens_in,
#             tokens_out=resp.tokens_out,
#             cost_usd=resp.cost_usd,
#             latency_ms=resp.latency_ms,
#         )


# __all__ = ["SpecialistAgent"]
