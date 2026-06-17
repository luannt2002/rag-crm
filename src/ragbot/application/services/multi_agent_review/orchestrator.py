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

# import asyncio
# from dataclasses import dataclass, field

# import structlog

# from ragbot.application.services.multi_agent_review.agent_port import (
#     AgentPort,
#     AgentResponse,
#     ReviewArtefact,
#     ReviewVerdict,
# )
# from ragbot.application.services.multi_agent_review.agents.auditor_agent import (
#     AuditorAgent,
# )
# from ragbot.shared.constants import (
#     DEFAULT_MULTI_AGENT_DEBATE_ROUNDS,
#     DEFAULT_MULTI_AGENT_MAX_DEBATE_ROUNDS,
# )
# from ragbot.shared.types import TenantId, TraceId

# _log = structlog.get_logger("multi_agent_review.orchestrator")


# @dataclass(frozen=True, slots=True)
# class ReviewReport:
#     artefact: ReviewArtefact
#     rounds: list[list[AgentResponse]] = field(default_factory=list)
#     auditor: AgentResponse | None = None

#     @property
#     def total_cost_usd(self) -> float:
#         agg = 0.0
#         for rnd in self.rounds:
#             agg += sum(r.cost_usd for r in rnd)
#         if self.auditor is not None:
#             agg += self.auditor.cost_usd
#         return agg

#     @property
#     def total_tokens_in(self) -> int:
#         agg = 0
#         for rnd in self.rounds:
#             agg += sum(r.tokens_in for r in rnd)
#         if self.auditor is not None:
#             agg += self.auditor.tokens_in
#         return agg

#     @property
#     def total_tokens_out(self) -> int:
#         agg = 0
#         for rnd in self.rounds:
#             agg += sum(r.tokens_out for r in rnd)
#         if self.auditor is not None:
#             agg += self.auditor.tokens_out
#         return agg

#     @property
#     def verdict(self) -> ReviewVerdict:
#         if self.auditor is not None:
#             return self.auditor.verdict
#         return ReviewVerdict.APPROVED_WITH_FIX


# class MultiAgentReviewOrchestrator:
#     def __init__(
#         self,
#         specialists: list[AgentPort],
#         auditor: AuditorAgent,
#         *,
#         debate_rounds: int = DEFAULT_MULTI_AGENT_DEBATE_ROUNDS,
#         run_specialists_concurrently: bool = True,
#     ) -> None:
#         if not specialists:
#             raise ValueError("orchestrator needs at least one specialist")
#         if debate_rounds < 0:
#             raise ValueError("debate_rounds must be >= 0")
#         if debate_rounds > DEFAULT_MULTI_AGENT_MAX_DEBATE_ROUNDS:
#             raise ValueError(
#                 f"debate_rounds capped at {DEFAULT_MULTI_AGENT_MAX_DEBATE_ROUNDS}"
#             )
#         self._specialists = specialists
#         self._auditor = auditor
#         self._debate_rounds = debate_rounds
#         self._concurrent = run_specialists_concurrently

#     async def run(
#         self,
#         artefact: ReviewArtefact,
#         *,
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> ReviewReport:
#         rounds: list[list[AgentResponse]] = []
#         prior: list[AgentResponse] = []
#         for round_idx in range(self._debate_rounds + 1):
#             round_responses = await self._run_round(
#                 artefact,
#                 prior=prior,
#                 record_tenant_id=record_tenant_id,
#                 trace_id=trace_id,
#             )
#             rounds.append(round_responses)
#             _log.info(
#                 "multi_agent.round_complete",
#                 round=round_idx,
#                 trace_id=str(trace_id),
#                 verdicts=[r.verdict.value for r in round_responses],
#             )
#             if round_idx == 0 and self._all_approved(round_responses):
#                 break
#             prior = round_responses

#         auditor_resp = await self._auditor.synthesise(
#             artefact,
#             rounds[-1],
#             record_tenant_id=record_tenant_id,
#             trace_id=trace_id,
#         )
#         return ReviewReport(artefact=artefact, rounds=rounds, auditor=auditor_resp)

#     async def _run_round(
#         self,
#         artefact: ReviewArtefact,
#         *,
#         prior: list[AgentResponse],
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> list[AgentResponse]:
#         if self._concurrent:
#             tasks = [
#                 a.review(
#                     artefact,
#                     prior=prior,
#                     record_tenant_id=record_tenant_id,
#                     trace_id=trace_id,
#                 )
#                 for a in self._specialists
#             ]
#             return list(await asyncio.gather(*tasks))
#         out: list[AgentResponse] = []
#         for a in self._specialists:
#             out.append(
#                 await a.review(
#                     artefact,
#                     prior=prior,
#                     record_tenant_id=record_tenant_id,
#                     trace_id=trace_id,
#                 )
#             )
#         return out

#     @staticmethod
#     def _all_approved(responses: list[AgentResponse]) -> bool:
#         return all(r.verdict is ReviewVerdict.APPROVED for r in responses)


# __all__ = ["MultiAgentReviewOrchestrator", "ReviewReport"]
