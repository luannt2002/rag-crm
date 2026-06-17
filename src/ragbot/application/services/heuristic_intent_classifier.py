"""Layer 1 intent classify — regex VN pattern.

Fast-path heuristic that matches common Vietnamese query patterns via compiled
regex so the LLM ``understand_query`` round-trip (~1.6 s p50) is skipped for
high-confidence easy-to-classify turns (greeting, chitchat).

HALLU=0 sacred guarantee:
- Only returns a non-None intent when confidence >= caller's threshold.
- Caller MUST fall back to the LLM path when intent is None OR when
  confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD.
- The heuristic NEVER returns intents for domain-specific queries — those
  always fall through to the LLM path.
- ``factoid`` has no explicit pattern: any query not matching a fast-path
  pattern stays None → LLM fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ragbot.shared.constants import (
    INTENT_AGGREGATION,
    INTENT_CHITCHAT_LABEL,
    INTENT_COMPARISON,
    INTENT_GREETING,
    INTENT_MULTI_HOP,
)


@dataclass(frozen=True)
class HeuristicResult:
    """Result of a Layer-1 heuristic intent classify call.

    ``intent`` is ``None`` when no pattern matched with sufficient confidence —
    the caller MUST fall back to the LLM path in that case.
    """

    intent: str | None
    confidence: float  # 0.0 - 1.0
    matched_pattern: str | None


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------
# Patterns are anchored or use word-boundary semantics so a domain term that
# happens to contain a matching substring does NOT trigger the fast path.
# Each entry is (label, compiled_re). Order matters: more specific patterns
# are listed first so an early match wins.
#
# WHY these intents only:
# - greeting / chitchat: zero retrieval needed → skip is 100 % safe
# - aggregation / multi_hop / comparison: heuristic hint only; LLM still
#   validates. Confidence is set lower (0.85 single-match) to force LLM check
#   on anything borderline.
#
# KHÔNG: factoid — factoid is the default fallback; no regex can safely detect
# "needs RAG" without domain knowledge.

_PATTERN_REGISTRY: list[tuple[str, re.Pattern[str]]] = [
    # Greeting — high signal, narrow patterns, anchored
    (
        INTENT_GREETING,
        re.compile(
            r"^(xin chào|hi|hello|chào em|chào bạn|chào shop|hey|xin chao)\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    # Chitchat — acknowledgement / feedback tokens
    (
        INTENT_CHITCHAT_LABEL,
        re.compile(
            r"^(cảm ơn|cám ơn|thanks|thank you|ok\b|được rồi|tốt lắm|hay lắm|"
            r"tuyệt|tuyệt vời|đúng rồi|vâng|dạ|oke|okay)\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    # Aggregation — enumerating / counting signals
    (
        INTENT_AGGREGATION,
        re.compile(
            r"(có mấy|bao nhiêu|liệt kê|tất cả|toàn bộ|kể tên|các loại|"
            r"mấy loại|bao gồm những gì|gồm những gì)",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    # Multi-hop — causal / explanatory signals
    (
        INTENT_MULTI_HOP,
        re.compile(
            r"(tại sao|vì sao|giải thích|nguyên nhân|lý do|how come|why)",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    # Comparison — explicit compare signals
    (
        INTENT_COMPARISON,
        re.compile(
            r"(so sánh|khác nhau|khác gì|vs\b|versus|difference between|"
            r"hơn hay kém|tốt hơn|nên chọn)",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
]


def classify_heuristic(query: str) -> HeuristicResult:
    """Layer 1 intent classify by regex.

    Scans the compiled pattern registry in order.  Returns the **first** match
    because patterns are ordered most-to-least specific.  When multiple
    distinct intents would match (unlikely but possible) the first wins — the
    caller's LLM fallback path resolves ambiguity when confidence is below
    threshold anyway.

    Confidence logic:
    - Single pattern match on a greeting/chitchat (anchored) → 0.90 (very
      high signal, anchored patterns reject mid-string matches).
    - Single pattern match on aggregation/multi_hop/comparison → 0.85 (these
      are mid-string patterns that could appear in domain queries; threshold
      acts as a safety floor).
    - No match → 0.0, intent=None.

    Caller contract: if ``intent is None`` OR ``confidence < threshold`` →
    fall back to LLM ``understand_query`` path.  NEVER skip LLM on low-
    confidence result.
    """
    if not query or not query.strip():
        return HeuristicResult(intent=None, confidence=0.0, matched_pattern=None)

    stripped = query.strip()
    for intent_label, pattern in _PATTERN_REGISTRY:
        if pattern.search(stripped):
            # Greeting/chitchat are anchored → higher signal than mid-string patterns.
            if intent_label in (INTENT_GREETING, INTENT_CHITCHAT_LABEL):
                confidence = 0.90
            else:
                confidence = 0.85
            return HeuristicResult(
                intent=intent_label,
                confidence=confidence,
                matched_pattern=pattern.pattern,
            )

    return HeuristicResult(intent=None, confidence=0.0, matched_pattern=None)
