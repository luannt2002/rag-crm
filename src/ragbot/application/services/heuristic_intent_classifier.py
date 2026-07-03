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
    DEFAULT_LANGUAGE,
    HEURISTIC_INTENT_CONFIDENCE_STRONG,
    HEURISTIC_INTENT_CONFIDENCE_WEAK,
    INTENT_CHITCHAT_LABEL,
    INTENT_GREETING,
)
from ragbot.shared.i18n import RoutingSignals, get_routing_signals


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
#   validates. Confidence is set to the WEAK tier (below the trust floor) to
#   force an LLM check on anything borderline.
#
# KHÔNG: factoid — factoid is the default fallback; no regex can safely detect
# "needs RAG" without domain knowledge.

# The Vietnamese intent regex that USED to live inline here are now the ``vi``
# seed of the language pack (``shared.i18n._VI_ROUTING_SIGNALS.intent_patterns``).
# ``classify_heuristic`` compiles the registry from a resolved ``RoutingSignals``
# so a non-Vietnamese bot classifies on ITS locale's patterns. The ``vi`` seed
# preserves the original regex byte-for-byte → a ``vi`` bot is unchanged. Source
# of truth = the DB-backed language pack; this in-memory seed is the boot guard.
_DEFAULT_SIGNALS: Final[RoutingSignals] = get_routing_signals(DEFAULT_LANGUAGE)

# Per-RoutingSignals compiled-registry cache. Keyed by object identity so the
# hot path compiles each locale's patterns once (the seed objects are module
# singletons; DB-hydrated packs are cached upstream by LanguagePackService).
_REGISTRY_CACHE: dict[int, list[tuple[str, re.Pattern[str]]]] = {}


def _compiled_registry(
    signals: RoutingSignals,
) -> list[tuple[str, re.Pattern[str]]]:
    """Return the (label, compiled_re) registry for ``signals`` (cached)."""
    cached = _REGISTRY_CACHE.get(id(signals))
    if cached is not None:
        return cached
    registry = [
        (label, re.compile(src, re.IGNORECASE | re.UNICODE))
        for label, src in signals.intent_patterns
    ]
    _REGISTRY_CACHE[id(signals)] = registry
    return registry


def classify_heuristic(
    query: str, *, signals: RoutingSignals | None = None
) -> HeuristicResult:
    """Layer 1 intent classify by regex.

    Scans the compiled pattern registry in order.  Returns the **first** match
    because patterns are ordered most-to-least specific.  When multiple
    distinct intents would match (unlikely but possible) the first wins — the
    caller's LLM fallback path resolves ambiguity when confidence is below
    threshold anyway.

    ``signals`` carries the locale-scoped intent regex (resolved from the bot's
    language pack). When ``None`` the ``vi`` DEFAULT SEED is used, keeping legacy
    call sites byte-identical. A locale with no intent patterns matches nothing
    → returns ``intent=None`` (LLM fallback), never mis-classifies.

    Confidence logic:
    - Single pattern match on a greeting/chitchat (anchored) → STRONG tier
      (above the trust floor; anchored patterns reject mid-string matches).
    - Single pattern match on aggregation/multi_hop/comparison → WEAK tier
      (below the trust floor; these are mid-string patterns that could appear
      in domain queries, so the gate forces LLM validation).
    - No match → 0.0, intent=None.

    Caller contract: if ``intent is None`` OR ``confidence < threshold`` →
    fall back to LLM ``understand_query`` path.  NEVER skip LLM on low-
    confidence result.
    """
    if not query or not query.strip():
        return HeuristicResult(intent=None, confidence=0.0, matched_pattern=None)

    sig = signals if signals is not None else _DEFAULT_SIGNALS
    stripped = query.strip()
    for intent_label, pattern in _compiled_registry(sig):
        if pattern.search(stripped):
            # Greeting/chitchat are anchored → strong signal, safe to skip LLM.
            # Everything else is a mid-string hint below the trust floor so the
            # gate forces LLM validation (see the constants' rationale).
            if intent_label in (INTENT_GREETING, INTENT_CHITCHAT_LABEL):
                confidence = HEURISTIC_INTENT_CONFIDENCE_STRONG
            else:
                confidence = HEURISTIC_INTENT_CONFIDENCE_WEAK
            return HeuristicResult(
                intent=intent_label,
                confidence=confidence,
                matched_pattern=pattern.pattern,
            )

    return HeuristicResult(intent=None, confidence=0.0, matched_pattern=None)
