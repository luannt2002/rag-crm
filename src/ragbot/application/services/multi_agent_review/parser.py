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

# from ragbot.application.services.multi_agent_review.agent_port import (
#     AgentResponse,
#     AgentRole,
#     ReviewVerdict,
# )

# _SECTION_RE = re.compile(
#     r"(SUMMARY|ISSUES|SUGGESTIONS|RISKS|VERDICT)\s*:",
#     re.IGNORECASE,
# )


# def _split_sections(raw: str) -> dict[str, str]:
#     sections: dict[str, str] = {}
#     matches = list(_SECTION_RE.finditer(raw))
#     for i, m in enumerate(matches):
#         key = m.group(1).upper()
#         start = m.end()
#         end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
#         sections[key] = raw[start:end].strip()
#     return sections


# def _parse_bullets(block: str) -> list[str]:
#     out: list[str] = []
#     for line in block.splitlines():
#         s = line.strip()
#         if not s:
#             continue
#         if s.startswith(("-", "*", "•")):
#             s = s[1:].strip()
#         elif re.match(r"^\d+[.)]\s+", s):
#             s = re.sub(r"^\d+[.)]\s+", "", s)
#         if s and s.lower() != "none":
#             out.append(s)
#     return out


# _VERDICT_MAP = {
#     "approved": ReviewVerdict.APPROVED,
#     "approved_with_fix": ReviewVerdict.APPROVED_WITH_FIX,
#     "approved with fix": ReviewVerdict.APPROVED_WITH_FIX,
#     "rejected": ReviewVerdict.REJECTED,
# }


# def _parse_verdict(block: str) -> ReviewVerdict:
#     s = block.strip().lower().split("\n", 1)[0]
#     s = s.strip(" .;:`")
#     if s in _VERDICT_MAP:
#         return _VERDICT_MAP[s]
#     for key, val in _VERDICT_MAP.items():
#         if key in s:
#             return val
#     return ReviewVerdict.APPROVED_WITH_FIX


# def parse_agent_response(
#     role: AgentRole,
#     raw: str,
#     *,
#     tokens_in: int = 0,
#     tokens_out: int = 0,
#     cost_usd: float = 0.0,
#     latency_ms: int = 0,
# ) -> AgentResponse:
#     sections = _split_sections(raw)
#     summary = sections.get("SUMMARY", "").strip().splitlines()
#     summary_text = summary[0].strip() if summary else raw.strip()[:200]
#     return AgentResponse(
#         role=role,
#         summary=summary_text,
#         issues=_parse_bullets(sections.get("ISSUES", "")),
#         suggestions=_parse_bullets(sections.get("SUGGESTIONS", "")),
#         risks=_parse_bullets(sections.get("RISKS", "")),
#         verdict=_parse_verdict(sections.get("VERDICT", "")),
#         raw=raw,
#         tokens_in=tokens_in,
#         tokens_out=tokens_out,
#         cost_usd=cost_usd,
#         latency_ms=latency_ms,
#     )


# __all__ = ["parse_agent_response"]
