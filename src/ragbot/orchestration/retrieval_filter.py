"""Pure retrieval/grade post-processing filters — extracted from query_graph.

Strangler-fig Phase 2 ("peel the leaves first"): these are the side-effect-free
helpers the query graph applies to scored chunks + CRAG grade histograms. They
take plain ``list[dict]`` / ``dict[str,int]`` and return plain values — no
``GraphState``, no LLM, no I/O, no logger — so they unit-test in isolation and
carry zero behaviour change when moved out of the 8k-line orchestrator.

``query_graph`` re-imports every name below, so existing call sites and the
``from ragbot.orchestration.query_graph import _cliff_detect_filter`` test
imports keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    DEFAULT_CRAG_MIN_RELEVANT_COUNT,
    DEFAULT_CRAG_MIN_RELEVANT_FRACTION,
)

# CRAG grade vocabulary — single source of truth (was inline in query_graph).
CRAG_GRADE_RELEVANT = "relevant"
CRAG_GRADE_IRRELEVANT = "irrelevant"
CRAG_GRADE_AMBIGUOUS = "ambiguous"
_CRAG_VALID_GRADES = frozenset(
    {CRAG_GRADE_RELEVANT, CRAG_GRADE_IRRELEVANT, CRAG_GRADE_AMBIGUOUS},
)


def _is_retrieval_adequate(
    grade_counts: dict[str, int],
    *,
    min_relevant_count: int = DEFAULT_CRAG_MIN_RELEVANT_COUNT,
    min_relevant_fraction: float = DEFAULT_CRAG_MIN_RELEVANT_FRACTION,
) -> bool:
    """True iff CRAG grade histogram clears both `min_relevant_count` and `min_relevant_fraction`."""
    relevant_count = int(grade_counts.get(CRAG_GRADE_RELEVANT, 0))
    total_graded = sum(int(v) for v in grade_counts.values()) or 1
    fraction_ok = (relevant_count / total_graded) >= float(min_relevant_fraction)
    count_ok = relevant_count >= int(min_relevant_count)
    return count_ok and fraction_ok


def _remap_grade_for_intent(
    raw_grade: str,
    *,
    intent: str,
    lenient_intents: frozenset[str],
    lenient_enabled: bool,
) -> str:
    """Compound-intent leniency: promote ``irrelevant`` -> ``ambiguous``.

    Live evidence (mega-sprint G10b · Issue 10): compound queries decompose
    into N sub-queries, retrieve N times, RRF-merge -> grade once on the
    merged pool. Surviving chunks each cover ONE sub-entity of the compound
    query. The grader sees the FULL compound query and rationally labels
    each chunk ``no`` (does NOT fully answer the FULL question), producing
    ``relevant=0 irrelevant=N`` -> empty answer for the user.

    For intents in *lenient_intents* (default: comparison / multi_hop /
    aggregation), an ``irrelevant`` verdict is promoted to ``ambiguous``
    so the chunk stays in the graded pool and downstream ``generate`` can
    synthesize a partial answer. ``relevant`` and ``ambiguous`` verdicts
    pass through unchanged - never demote. Strict intents (factoid /
    chitchat / OOS / unknown / empty string) keep the original verdict.

    HALLU=0 sacred is preserved by the downstream ``grounding_check``
    guardrail which evaluates the FINAL answer against the chunks.
    """
    if not lenient_enabled:
        return raw_grade
    if raw_grade != CRAG_GRADE_IRRELEVANT:
        return raw_grade
    if not intent:
        return raw_grade
    if intent not in lenient_intents:
        return raw_grade
    return CRAG_GRADE_AMBIGUOUS


def _autocut(chunks: list[dict], min_gap_ratio: float = 0.3) -> list[dict]:
    """Drop chunks after a significant score cliff."""
    if len(chunks) <= 1:
        return chunks
    scores = [float(c.get("score", 0)) for c in chunks]
    for i in range(1, len(scores)):
        if scores[i - 1] > 0 and (scores[i - 1] - scores[i]) / scores[i - 1] > min_gap_ratio:
            return chunks[:i]
    return chunks


def _cliff_detect_filter(
    chunks: list[dict],
    *,
    absolute_floor: float,
    gap_ratio: float,
    min_keep: int,
    force_min_keep: bool = True,
) -> tuple[list[dict], dict[str, Any]]:
    """Adaptive filter — distribution-aware cut for calibrated rerank scores.

    Algorithm (Pattern B per RERANK_THRESHOLD_BEST_PRACTICE_2026):
      1. Drop chunks below ``absolute_floor`` (negative-relevance noise).
      2. Walk sorted scores, cut at first consecutive drop where
         ``(prev - curr) / prev > gap_ratio``. Always keep at least
         ``min_keep`` chunks even when first gap is huge (fallback safety).

    Empty-context safety: when ``force_min_keep=True`` (default) and the
    floor cut would otherwise leave 0 chunks, retain the single highest-
    scored input chunk so downstream nodes always see context. This
    prevents the LLM from emitting empty-string answers when retrieval
    is weak — refusal must come from the system prompt's empty-context
    rule reading a single low-confidence chunk, not from a literally
    blank ``<documents>`` block. Disable by passing ``force_min_keep=False``.

    Returns ``(filtered_chunks, metadata)`` where metadata captures
    ``cut_index``, ``max_gap_ratio``, ``triggered``, ``safety_triggered``
    for observability.
    """
    sorted_chunks = sorted(chunks, key=lambda c: -float(c.get("score", 0) or 0))
    floor_kept = [c for c in sorted_chunks if float(c.get("score", 0) or 0) >= absolute_floor]

    # Empty-context safety net: never return [] when caller had >=1 input chunk.
    if not floor_kept and sorted_chunks and force_min_keep:
        return [sorted_chunks[0]], {
            "cut_index": 1,
            "max_gap_ratio": 0.0,
            "triggered": False,
            "safety_triggered": True,
            "reason": "empty_context_safety_keep_top1",
        }

    if len(floor_kept) <= 1:
        return floor_kept, {
            "cut_index": len(floor_kept),
            "max_gap_ratio": 0.0,
            "triggered": False,
            "safety_triggered": False,
            "reason": "below_floor_or_single",
        }
    max_gap = 0.0
    cut_at = len(floor_kept)
    for i in range(1, len(floor_kept)):
        prev = float(floor_kept[i - 1].get("score", 0) or 0)
        curr = float(floor_kept[i].get("score", 0) or 0)
        if prev <= 0:
            continue
        gap = (prev - curr) / prev
        if gap > max_gap:
            max_gap = gap
        if gap > gap_ratio and i >= min_keep:
            cut_at = i
            break
    return floor_kept[:cut_at], {
        "cut_index": cut_at,
        "max_gap_ratio": round(max_gap, 4),
        "triggered": cut_at < len(floor_kept),
        "safety_triggered": False,
        "reason": "cliff" if cut_at < len(floor_kept) else "no_cliff_kept_all",
    }


def _rerank_threshold_gate(
    chunks: list[dict],
    *,
    threshold: float,
    mode: str,
) -> tuple[list[dict], dict[str, Any]]:
    """Refuse gate — drop all chunks when top-1 rerank score < threshold.

    Returns ``(out_chunks, meta)`` where ``meta`` carries observability
    fields (``top_score``, ``threshold``, ``refused``, ``applicable``).

    Behaviour:
      * Only applies when ``mode == "rerank"`` (a real cross-encoder ran).
        Bypass modes (``null_reranker``, ``disabled``, ``no_reranker``,
        ``empty_input``, ``intent_skip*``, ``rerank_fallback``) leave the
        chunks untouched — the bypass-score scale is incomparable with
        the cross-encoder 0..1 floor.
      * Empty ``chunks`` is a no-op (no gating decision to make).
      * When ``top_score < threshold`` the gate empties the chunk list so
        the downstream grade/generate refuse short-circuit emits the
        bot's ``oos_answer_template`` — gate NEVER injects refuse text
        itself (Quality Gate #10).

    Boundary semantics: ``top_score >= threshold`` passes; equality is a
    pass (>= comparison) so a threshold of 0.30 admits a chunk scoring
    exactly 0.30.
    """
    applicable = mode == "rerank" and bool(chunks)
    if not applicable:
        return chunks, {
            "applicable": False,
            "refused": False,
            "top_score": 0.0,
            "threshold": float(threshold),
            "mode": mode,
        }
    top_score = max((float(c.get("score", 0) or 0) for c in chunks), default=0.0)
    refused = top_score < float(threshold)
    out = [] if refused else chunks
    return out, {
        "applicable": True,
        "refused": refused,
        "top_score": round(top_score, 6),
        "threshold": float(threshold),
        "mode": mode,
    }


__all__ = [
    "CRAG_GRADE_AMBIGUOUS",
    "CRAG_GRADE_IRRELEVANT",
    "CRAG_GRADE_RELEVANT",
    "_CRAG_VALID_GRADES",
    "_autocut",
    "_cliff_detect_filter",
    "_is_retrieval_adequate",
    "_remap_grade_for_intent",
    "_rerank_threshold_gate",
]
