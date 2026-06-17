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

# from ragbot.application.services.multi_agent_review.agent_port import (
#     AgentResponse,
#     AgentRole,
#     ArtefactKind,
#     ReviewArtefact,
# )

# _ROLE_BRIEF: dict[AgentRole, str] = {
#     AgentRole.ARCHITECT: (
#         "Architect. Judge pipeline shape, layering, scalability, multi-tenant "
#         "isolation, and how the artefact fits the 24-step pipeline (U1-U7 + "
#         "Q1-Q17). Flag tight coupling and version-ref drift."
#     ),
#     AgentRole.RAG_SPECIALIST: (
#         "RAG Specialist. Judge retrieval / embedding / rerank / chunking / "
#         "HyDE / parent-doc / metadata filters. Optimise recall+precision on "
#         "small Vietnamese corpora; defend HALLU=0 sacred."
#     ),
#     AgentRole.VIETNAMESE_LINGUIST: (
#         "Vietnamese Linguist. Judge VN compounds, underthesea limits, "
#         "n-grams, proper nouns, negation, tone. Reject phrasing that sounds "
#         "translated or stiff to a native ear."
#     ),
#     AgentRole.QUALITY_GUARDIAN: (
#         "Quality & Safety Guardian. Enforce HALLU=0 sacred, refusal traps, "
#         "Quality Gate #10 (no app inject / no app override), guardrail and "
#         "output control. No exception, no negotiation."
#     ),
#     AgentRole.EVALUATOR: (
#         "Evaluator & Tester. Design failing tests, golden-set additions, "
#         "load-test deltas, KPI moves (PASS rate, p95, cost/turn). Show what "
#         "would prove the artefact wrong."
#     ),
#     AgentRole.CRITIC: (
#         "Critic & Optimiser. Adversarial: name the weakest assumption, the "
#         "hidden trade-off, and one cheaper alternative that still solves the "
#         "stated problem."
#     ),
#     AgentRole.AUDITOR: (
#         "Auditor. Read every specialist's response, validate against sacred "
#         "contracts (HALLU=0, app-no-inject, app-no-override, 4-key identity, "
#         "domain-neutral, zero-hardcode, Strategy+DI), reconcile conflicts, "
#         "produce the final verdict. You DO NOT author review content."
#     ),
# }

# _OUTPUT_CONTRACT = (
#     "Reply ONLY in this exact section format. No prose outside sections.\n"
#     "SUMMARY: <one sentence, neutral>\n"
#     "ISSUES:\n"
#     "- <issue 1; empty list ok — write `- none` then>\n"
#     "SUGGESTIONS:\n"
#     "- <suggestion 1; `- none` ok>\n"
#     "RISKS:\n"
#     "- <risk 1; `- none` ok>\n"
#     "VERDICT: <approved | approved_with_fix | rejected>"
# )


# def build_specialist_messages(
#     role: AgentRole,
#     artefact: ReviewArtefact,
#     prior: list[AgentResponse],
# ) -> tuple[str, str]:
#     system = (
#         f"You are a {role.value} in a 7-agent code-review team. "
#         f"{_ROLE_BRIEF[role]}\n\n"
#         "Sacred (non-negotiable): HALLU=0, app does not inject text into the "
#         "LLM prompt, app does not override LLM answers, 4-key bot identity, "
#         "domain-neutral platform code, zero-hardcode, Strategy+DI, "
#         "no-version-ref. Reject any artefact that violates them.\n\n"
#         + _OUTPUT_CONTRACT
#     )

#     parts = [
#         f"ARTEFACT KIND: {artefact.kind.value}",
#         f"ARTEFACT TITLE: {artefact.title or '(untitled)'}",
#     ]
#     if artefact.metadata:
#         meta_lines = "\n".join(f"  {k}: {v}" for k, v in artefact.metadata.items())
#         parts.append(f"METADATA:\n{meta_lines}")
#     parts.append("ARTEFACT BODY:\n---\n" + artefact.text + "\n---")

#     if prior:
#         prior_block = "\n\n".join(
#             f"[{p.role.value}] verdict={p.verdict.value}\n"
#             f"summary: {p.summary}\n"
#             f"issues: {'; '.join(p.issues) or 'none'}\n"
#             f"suggestions: {'; '.join(p.suggestions) or 'none'}"
#             for p in prior
#         )
#         parts.append(
#             "PRIOR ROUND (debate — push back where you disagree, "
#             "concede where they are right):\n" + prior_block
#         )

#     parts.append("Now write your review using the SUMMARY/ISSUES/... format.")
#     user = "\n\n".join(parts)
#     return system, user


# def build_auditor_messages(
#     artefact: ReviewArtefact,
#     specialist_responses: list[AgentResponse],
# ) -> tuple[str, str]:
#     system = (
#         "You are the Auditor. You DO NOT author review content. You read "
#         "the six specialists' responses, validate them against sacred "
#         "contracts, reconcile conflicts, and produce ONE final verdict.\n\n"
#         + _OUTPUT_CONTRACT
#         + "\n\nIn ISSUES list the union of unresolved issues from specialists "
#         "(deduped, severity-sorted). In SUGGESTIONS list the merged action "
#         "list. VERDICT rule: rejected if any specialist returned rejected or "
#         "any sacred contract violation present; approved_with_fix if any "
#         "non-empty issue remains; otherwise approved."
#     )
#     spec_block = "\n\n".join(
#         f"[{r.role.value}] verdict={r.verdict.value}\n"
#         f"summary: {r.summary}\n"
#         f"issues: {'; '.join(r.issues) or 'none'}\n"
#         f"suggestions: {'; '.join(r.suggestions) or 'none'}\n"
#         f"risks: {'; '.join(r.risks) or 'none'}"
#         for r in specialist_responses
#     )
#     body = (
#         f"ARTEFACT KIND: {artefact.kind.value}\n"
#         f"ARTEFACT TITLE: {artefact.title or '(untitled)'}\n\n"
#         f"SPECIALIST RESPONSES (n={len(specialist_responses)}):\n{spec_block}\n\n"
#         "Reconcile and produce the final SUMMARY/ISSUES/SUGGESTIONS/RISKS/VERDICT."
#     )
#     return system, body


# __all__ = ["build_auditor_messages", "build_specialist_messages"]


# def kind_hint(kind: ArtefactKind) -> str:
#     return {
#         ArtefactKind.PLAN: "implementation plan",
#         ArtefactKind.SYSPROMPT: "bot system prompt",
#         ArtefactKind.CODE_DIFF: "code diff",
#         ArtefactKind.PROMPT: "LLM prompt template",
#         ArtefactKind.GENERIC: "artefact",
#     }[kind]
