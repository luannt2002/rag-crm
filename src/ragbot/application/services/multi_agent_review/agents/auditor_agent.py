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

# import re

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.llm_port import LLMMessage, LLMPort
# from ragbot.application.services.multi_agent_review.agent_port import (
#     AgentResponse,
#     AgentRole,
#     ReviewArtefact,
#     ReviewVerdict,
# )
# from ragbot.application.services.multi_agent_review.parser import parse_agent_response
# from ragbot.application.services.multi_agent_review.prompts import (
#     build_auditor_messages,
# )
# from ragbot.shared.types import TenantId, TraceId

# _BODY_VIOLATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
#     ("version-ref `_v[0-9]`", re.compile(r"_v\d+\b")),
#     ("version-ref `_legacy`", re.compile(r"_legacy\b")),
#     ("provider hardcode `if provider ==`", re.compile(r"if\s+\w*provider\w*\s*==", re.IGNORECASE)),
# )

# Markdown documentation regions whose contents describe forbidden tokens
# rather than instantiate them. Stripping them before the violation scan
# prevents false-positive rejection on plans/ADRs that quote the rules.
# _MD_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
# _MD_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# _MD_BLOCKQUOTE_LINE_RE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
# _MD_INDENTED_CODE_LINE_RE = re.compile(r"^(?: {4}|\t).*$", re.MULTILINE)


# def _strip_markdown_doc_blocks(text: str) -> str:
#     """Replace markdown documentation regions with whitespace.

#     Order: fenced (may itself contain backticks) -> inline backtick spans ->
#     blockquote lines -> 4-space/tab indented code lines. Substitution uses a
#     single space to keep surrounding word boundaries intact for the scanner.
#     """
#     if not text:
#         return text
#     out = _MD_FENCED_BLOCK_RE.sub(" ", text)
#     out = _MD_INLINE_CODE_RE.sub(" ", out)
#     out = _MD_BLOCKQUOTE_LINE_RE.sub(" ", out)
#     return _MD_INDENTED_CODE_LINE_RE.sub(" ", out)


# _ISSUE_FLAGS = (
#     "hallu",
#     "fabricat",
#     "inject text",
#     "override answer",
#     "hardcode",
#     "magic number",
#     "version-ref",
#     "if provider ==",
# )


# class AuditorAgent:
#     """Validates specialist outputs and produces the final verdict.

#     The Auditor never authors review *content*. It runs a deterministic
#     sacred-contract check first; if that finds a violation, the LLM call
#     is short-circuited and the verdict is forced to REJECTED. Otherwise
#     the LLM merges + reconciles.
#     """

#     role = AgentRole.AUDITOR

#     def __init__(self, *, llm: LLMPort, spec: LLMSpec) -> None:
#         self._llm = llm
#         self._spec = spec

#     async def synthesise(
#         self,
#         artefact: ReviewArtefact,
#         responses: list[AgentResponse],
#         *,
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> AgentResponse:
#         forced = self._sacred_violation(artefact, responses)
#         if forced is not None:
#             return forced

#         system, user = build_auditor_messages(artefact, responses)
#         resp = await self._llm.complete(
#             messages=[
#                 LLMMessage(role="system", content=system),
#                 LLMMessage(role="user", content=user),
#             ],
#             spec=self._spec,
#             record_tenant_id=record_tenant_id,
#             trace_id=trace_id,
#         )
#         parsed = parse_agent_response(
#             AgentRole.AUDITOR,
#             resp.content,
#             tokens_in=resp.tokens_in,
#             tokens_out=resp.tokens_out,
#             cost_usd=resp.cost_usd,
#             latency_ms=resp.latency_ms,
#         )

#         any_rejected = any(r.verdict is ReviewVerdict.REJECTED for r in responses)
#         any_with_fix = any(
#             r.verdict is ReviewVerdict.APPROVED_WITH_FIX or r.issues for r in responses
#         )
#         if any_rejected and parsed.verdict is not ReviewVerdict.REJECTED:
#             parsed = AgentResponse(
#                 role=parsed.role,
#                 summary=parsed.summary,
#                 issues=parsed.issues,
#                 suggestions=parsed.suggestions,
#                 risks=parsed.risks,
#                 verdict=ReviewVerdict.REJECTED,
#                 raw=parsed.raw,
#                 tokens_in=parsed.tokens_in,
#                 tokens_out=parsed.tokens_out,
#                 cost_usd=parsed.cost_usd,
#                 latency_ms=parsed.latency_ms,
#             )
#         elif (
#             any_with_fix
#             and parsed.verdict is ReviewVerdict.APPROVED
#             and not any_rejected
#         ):
#             parsed = AgentResponse(
#                 role=parsed.role,
#                 summary=parsed.summary,
#                 issues=parsed.issues,
#                 suggestions=parsed.suggestions,
#                 risks=parsed.risks,
#                 verdict=ReviewVerdict.APPROVED_WITH_FIX,
#                 raw=parsed.raw,
#                 tokens_in=parsed.tokens_in,
#                 tokens_out=parsed.tokens_out,
#                 cost_usd=parsed.cost_usd,
#                 latency_ms=parsed.latency_ms,
#             )
#         return parsed

#     @staticmethod
#     def _sacred_violation(
#         artefact: ReviewArtefact,
#         responses: list[AgentResponse],
#     ) -> AgentResponse | None:
#         violations: list[str] = []
#         scan_text = _strip_markdown_doc_blocks(artefact.text)
#         for label, pattern in _BODY_VIOLATION_PATTERNS:
#             if pattern.search(scan_text):
#                 violations.append(f"artefact body contains {label}")
#         for r in responses:
#             for issue in r.issues:
#                 low = issue.lower()
#                 if any(flag in low for flag in _ISSUE_FLAGS):
#                     violations.append(f"{r.role.value}: {issue}")
#         if not violations:
#             return None
#         return AgentResponse(
#             role=AgentRole.AUDITOR,
#             summary="Sacred contract violation detected — auto-rejected before LLM merge.",
#             issues=violations[:10],
#             suggestions=[
#                 "Resolve every sacred-contract violation before re-submitting."
#             ],
#             verdict=ReviewVerdict.REJECTED,
#             raw="",
#         )


# __all__ = ["AuditorAgent"]
