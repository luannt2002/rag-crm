"""Adaptive Router — Layer 1: query complexity classifier (DOMAIN-NEUTRAL).

Generic regex/heuristic detector that flags whether an incoming query is
"simple" (single intent → straight retrieve) or "complex" (multi-intent
→ Layer 3 LLM decomposer fires). The classifier is the first guard on
the multi-entity fan-out path; it runs on every query (sub-millisecond).

Domain-neutral contract:
- NO hardcode of brand / domain tokens. The classifier scores on
  language signals only (commas, conjunctions, numerals, '?').
- All weights + the conjunction token list come from ``system_config``
  via ``get_boot_config`` (TTL-cached), with constants.py defaults as
  the fallback floor.
- Bot owner tunes per-tenant by updating the matching ``system_config``
  rows (no redeploy).

Caller contract::

    label, score = classify_query_complexity(query)
    if label == "complex":
        sub_queries = await decompose_query(query, ...)
        state["sub_queries"] = sub_queries
    # else: leave sub_queries empty; retrieve uses original query.

Pure function, no side effects, safe to call from any node.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ragbot.shared.bootstrap_config import get_boot_config
from ragbot.shared.constants import (
    DEFAULT_AGGREGATION_KEYWORDS_BY_LANG,
    DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON,
    DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
    DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_MAX_CHARS,
    DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_REF_PATTERN,
    DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_NUMBERS,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
    DEFAULT_STRUCTURAL_MARKERS_LANG,
)

logger = logging.getLogger(__name__)


_NUMBER_RE = re.compile(r"\b\d+\b")
_STRUCTURAL_REF_RE = re.compile(DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_REF_PATTERN)


# Strategy + DI hook: allow injection of an alternate config getter for
# tests / future Redis-direct paths. Falls through to ``get_boot_config``
# when the caller does not supply one. The signature mirrors
# ``get_boot_config(key, default)`` exactly.
ConfigGetter = Callable[[str, Any], Any]


def _coerce_float(raw: Any, fallback: float) -> float:
    """Best-effort float coerce; fall back to ``fallback`` on any error."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return fallback
    return fallback


def _resolve_conjunctions(raw: Any) -> list[str]:
    """Return a flat list[str] of conjunction tokens from a config value.

    Accepts: list (native), str (JSON-encoded). Empty list on parse
    failure so the classifier degrades gracefully (commas + numbers +
    question marks still contribute).
    """
    if isinstance(raw, list):
        return [str(c) for c in raw if isinstance(c, (str, int, float))]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, (str, int, float))]
    return []


def classify_query_complexity(
    query: str,
    *,
    config_getter: ConfigGetter | None = None,
) -> tuple[str, float]:
    """Score a query and return ``("simple"|"complex", score)``.

    The scoring is intentionally additive + monotone so bot owners can
    reason about it without reading code: more commas / more conjunctions
    / more numbers / more question marks / longer query → higher score.
    Threshold flip is the single bot-owner-facing knob.

    DOMAIN-NEUTRAL: the function never mentions a domain literal; the
    conjunction list lives in ``system_config`` (multi-language) so new
    languages do not require a code change.
    """
    getter: ConfigGetter = config_getter or get_boot_config
    if not isinstance(query, str) or not query:
        return ("simple", 0.0)

    # Resolve conjunction tokens once — used by both the structural early-exit
    # gate below and the conjunction signal (step 2).
    conj_list = _resolve_conjunctions(
        getter(
            "query_complexity.conjunctions",
            DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON,
        )
    )
    q_padded = " " + query.lower() + " "
    has_conjunction = any(
        f" {str(t).strip().lower()} " in q_padded
        for t in conj_list
        if str(t).strip()
    )

    # Structural-reference early-exit: a short single-entity structural lookup
    # short-circuits to ("simple", 0.0) so the article number never mis-routes
    # it to the LLM decomposer. Gate requires EXACTLY ONE structural ref (two+
    # refs = multi-entity → decompose, e.g. "so sánh Điều 22 và Điều 55"), plus
    # ≤1 comma, no conjunction, and ≤ cap chars.
    if (
        len(_STRUCTURAL_REF_RE.findall(query)) == 1
        and len(query) <= DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_MAX_CHARS
        and query.count(",") <= 1
        and not has_conjunction
    ):
        return ("simple", 0.0)

    score = 0.0

    # 1. Comma list signal. First comma is free (e.g. "Hello, world?") —
    # only the additional commas count, mirroring how human writers use
    # them to enumerate multiple entities.
    weight_comma = _coerce_float(
        getter("query_complexity.weight_comma", DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA),
        DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA,
    )
    score += max(0, query.count(",") - 1) * weight_comma

    # 2. Conjunction signal — multi-language. Pad with spaces so we
    # don't accidentally match substrings ("Anders" matching "and").
    weight_conj = _coerce_float(
        getter(
            "query_complexity.weight_conjunction",
            DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
        ),
        DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
    )
    if conj_list:
        for token in conj_list:
            t = str(token).strip().lower()
            if not t:
                continue
            score += q_padded.count(f" {t} ") * weight_conj

    # 3. Numeric tokens — multi-entity hint (article numbers, prices,
    # quantities). Domain-neutral: it counts integers, not currency or
    # category names.
    weight_num = _coerce_float(
        getter(
            "query_complexity.weight_numbers",
            DEFAULT_QUERY_COMPLEXITY_WEIGHT_NUMBERS,
        ),
        DEFAULT_QUERY_COMPLEXITY_WEIGHT_NUMBERS,
    )
    score += len(_NUMBER_RE.findall(query)) * weight_num

    # 4. Multi-question marks — explicit multi-part query.
    weight_q = _coerce_float(
        getter(
            "query_complexity.weight_question",
            DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
        ),
        DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
    )
    score += max(0, query.count("?") - 1) * weight_q

    # 5. Length normaliser. Longer queries carry more entities on
    # average, so length contributes proportionally (in token count).
    length_norm = _coerce_float(
        getter(
            "query_complexity.length_normalizer",
            DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
        ),
        DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
    )
    if length_norm > 0:
        score += len(query.split()) / length_norm

    threshold = _coerce_float(
        getter(
            "query_complexity.complexity_threshold",
            DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
        ),
        DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
    )
    label = "complex" if score >= threshold else "simple"
    return (label, score)


def has_aggregation_keyword(
    query: str,
    *,
    lang: str = DEFAULT_STRUCTURAL_MARKERS_LANG,
) -> bool:
    """Return True when the query contains a per-language aggregation keyword.

    Multi-language hardening: the keyword set is sourced from
    ``DEFAULT_AGGREGATION_KEYWORDS_BY_LANG`` keyed by language code, so a
    non-Vietnamese bot ("en") gets its own enumeration vocabulary ("list",
    "how many", "compare") instead of silently matching nothing. The default
    language ("vi") preserves the historical Vietnamese behaviour. Unknown
    languages resolve to an empty set (no match) rather than the VN literals.

    Pure substring match on the case-folded query — no DB read, safe on the
    intent hot path. Returns False on empty / non-str input.
    """
    if not isinstance(query, str) or not query:
        return False
    keywords = DEFAULT_AGGREGATION_KEYWORDS_BY_LANG.get(lang, ())
    folded = query.lower()
    return any(kw and kw.lower() in folded for kw in keywords)


__all__ = [
    "ConfigGetter",
    "classify_query_complexity",
    "has_aggregation_keyword",
]
