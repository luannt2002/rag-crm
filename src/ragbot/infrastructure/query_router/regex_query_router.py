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
# Reason: query_router infra never wired in bootstrap or graph.
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

# """RegexQueryRouter — pattern-based pre-retrieve intent classifier.

# FAST path strategy: zero network, pure regex, ~microseconds per query.
# Covers the dominant query shapes for Vietnamese + English without any
# LLM call. Order of evaluation is precedence-sensitive (most specific
# first) so a query like "so sánh Điều 5 và Điều 7" classifies as
# ``comparison`` rather than ``structured_ref``.

# Precedence (highest -> lowest):
#     1. hallu_trap   — promotional / superlative bait (sacred refuse-trap)
#     2. smalltalk    — pure greeting / thanks / farewell
#     3. comparison   — explicit compare keywords
#     4. structured_ref — legal-style "Điều N", "Article N", "Khoản N" refs
#     5. factoid      — wh-questions / "X là gì" / how-much / when / where
#     6. semantic     — catch-all default

# The hallu_trap detector takes precedence over smalltalk so a query like
# "Black Friday chào em" routes to ``hallu_trap`` and downstream applies
# the strict refuse trap. Patterns are intentionally domain-neutral —
# no brand / customer literals — and live inline here because they are
# strategy-private; promote to ``shared/regex_patterns.py`` only if a
# second strategy needs to reuse them.
# """

# from __future__ import annotations

# import re
# from typing import Final

# from ragbot.application.ports.query_router_port import QueryIntent
# from ragbot.shared.constants import (
#     QUERY_INTENT_COMPARISON,
#     QUERY_INTENT_FACTOID,
#     QUERY_INTENT_HALLU_TRAP,
#     QUERY_INTENT_SEMANTIC,
#     QUERY_INTENT_SMALLTALK,
#     QUERY_INTENT_STRUCTURED_REF,
# )

# Promotional / superlative bait — sacred refuse-trap territory.
# Examples: "Black Friday giảm 50%", "khuyến mãi sale 30%", "promotion".
# No trailing ``\b`` because the percent alternation ends in ``%`` which
# is not a word character — the trailing boundary would never fire and
# the whole alternation would miss "giảm 50%".
# _HALLU_TRAP: Final[re.Pattern[str]] = re.compile(
#     r"(?:\bblack\s*friday\b|\bkhuyến\s*mãi\b|\bsale\b|\bpromotion\b"
#     r"|\bgiảm\s*\d+\s*%|\d+\s*%\s*giảm\b)",
#     re.IGNORECASE | re.UNICODE,
# )

# Pure greeting / thanks / farewell — must be near sentence start to avoid
# false positives on bodies like "tôi muốn hỏi về vé chào mừng".
# _SMALLTALK: Final[re.Pattern[str]] = re.compile(
#     r"^\s*(?:hello|hi|hey|chào|xin\s*chào|cảm\s*ơn|thanks?|thank\s*you|bye|tạm\s*biệt|good\s*(?:morning|afternoon|evening))\b",
#     re.IGNORECASE | re.UNICODE,
# )

# Explicit comparison keywords. ``khác`` requires a following hint (nhau /
# với / so) to avoid matching "khác hàng" / "khác biệt" used as adjectives.
# _COMPARISON: Final[re.Pattern[str]] = re.compile(
#     r"\b(?:so\s*sánh|compare|versus|\bvs\.?\b|khác\s*(?:nhau|với|so)|hơn\s*kém|nào\s*tốt\s*hơn|better\s*than)\b",
#     re.IGNORECASE | re.UNICODE,
# )

# Legal-style structured references. Requires the section keyword to be
# followed by digits or Roman numerals so prose mentions like "điều này",
# "điều kiện", "điều khoản" do NOT match.
# _STRUCTURED_REF: Final[re.Pattern[str]] = re.compile(
#     r"\b(?:điều|khoản|chương|mục|article|section|clause)\s+(?:\d+|[ivxlcdm]+\b)",
#     re.IGNORECASE | re.UNICODE,
# )

# Factoid wh-questions and "là gì" idiom. Includes EN how-much/many and
# VN bao nhiêu / khi nào / ở đâu / ai. The leading word-boundary set keeps
# embedded "when" inside "whenever" from matching twice.
# _FACTOID: Final[re.Pattern[str]] = re.compile(
#     r"(?:^|\W)(?:là\s*gì|what\s+is|when(?:\s+did)?|where\s+is|who\s+is|how\s+(?:much|many)|bao\s*nhiêu|khi\s*nào|ở\s*đâu)\b",
#     re.IGNORECASE | re.UNICODE,
# )


# class RegexQueryRouter:
#     """Regex-pattern query classifier — fast, deterministic, no I/O."""

#     @staticmethod
#     def get_provider_name() -> str:
#         return "regex"

#     async def classify(self, query: str) -> QueryIntent:
        # Empty / whitespace-only queries cannot meaningfully route — fall
        # back to the catch-all so retrieve still has a chance to find
        # something. Logging is the caller's responsibility.
#         if not query or not query.strip():
#             return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]

#         text = query.strip()
        # Order matters — see module docstring for precedence rationale.
#         if _HALLU_TRAP.search(text):
#             return QUERY_INTENT_HALLU_TRAP  # type: ignore[return-value]
#         if _SMALLTALK.search(text):
#             return QUERY_INTENT_SMALLTALK  # type: ignore[return-value]
#         if _COMPARISON.search(text):
#             return QUERY_INTENT_COMPARISON  # type: ignore[return-value]
#         if _STRUCTURED_REF.search(text):
#             return QUERY_INTENT_STRUCTURED_REF  # type: ignore[return-value]
#         if _FACTOID.search(text):
#             return QUERY_INTENT_FACTOID  # type: ignore[return-value]
#         return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]


# __all__ = ["RegexQueryRouter"]
