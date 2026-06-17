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

# from collections.abc import Callable

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.llm_port import LLMPort
# from ragbot.application.services.multi_agent_review.agent_port import (
#     AgentPort,
#     AgentRole,
# )
# from ragbot.application.services.multi_agent_review.agents.auditor_agent import (
#     AuditorAgent,
# )
# from ragbot.application.services.multi_agent_review.agents.specialist_agent import (
#     SpecialistAgent,
# )

# _DEFAULT_SPECIALIST_ORDER: tuple[AgentRole, ...] = (
#     AgentRole.ARCHITECT,
#     AgentRole.RAG_SPECIALIST,
#     AgentRole.VIETNAMESE_LINGUIST,
#     AgentRole.QUALITY_GUARDIAN,
#     AgentRole.EVALUATOR,
#     AgentRole.CRITIC,
# )

# AgentFactory = Callable[[LLMPort, LLMSpec], AgentPort]

# _REGISTRY: dict[AgentRole, AgentFactory] = {}


# def register_agent(role: AgentRole, factory: AgentFactory) -> None:
#     if role is AgentRole.AUDITOR:
#         raise ValueError("Auditor is built directly, not via the specialist registry")
#     _REGISTRY[role] = factory


# def _default_factory(role: AgentRole) -> AgentFactory:
#     def _build(llm: LLMPort, spec: LLMSpec) -> AgentPort:
#         return SpecialistAgent(role, llm=llm, spec=spec)

#     return _build


# for _role in _DEFAULT_SPECIALIST_ORDER:
#     _REGISTRY.setdefault(_role, _default_factory(_role))


# def build_default_review_team(
#     *,
#     llm: LLMPort,
#     specialist_spec: LLMSpec,
#     auditor_spec: LLMSpec | None = None,
#     roles: tuple[AgentRole, ...] = _DEFAULT_SPECIALIST_ORDER,
# ) -> tuple[list[AgentPort], AuditorAgent]:
#     specialists: list[AgentPort] = []
#     for role in roles:
#         if role is AgentRole.AUDITOR:
#             continue
#         factory = _REGISTRY.get(role) or _default_factory(role)
#         specialists.append(factory(llm, specialist_spec))
#     auditor = AuditorAgent(llm=llm, spec=auditor_spec or specialist_spec)
#     return specialists, auditor


# __all__ = [
#     "build_default_review_team",
#     "register_agent",
# ]
