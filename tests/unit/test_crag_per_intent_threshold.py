"""CRAG fallback threshold per-intent contract.

Pins the per-intent dict in ``shared/constants.py`` and replays the exact
lookup the grade node uses (intent dict → global default fallback) so a
silent regression in either the dict shape or the lookup wiring fails
loudly.
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT,
)


def _resolve_min_score(state: dict[str, Any]) -> float:
    """Mirror of the grade-node fallback-threshold lookup.

    Kept in the test module to assert the exact algorithm the orchestrator
    runs at runtime (intent dict first, global default second).
    """
    pipeline_cfg = state.get("pipeline_config") or {}
    intent_key = state.get("intent") or "factoid"
    intent_thresholds = pipeline_cfg.get(
        "crag_min_fallback_score_by_intent",
        DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT,
    )
    if isinstance(intent_thresholds, dict) and intent_key in intent_thresholds:
        return float(intent_thresholds[intent_key])
    return float(pipeline_cfg.get(
        "crag_min_fallback_score", DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    ))


def test_per_intent_dict_has_required_keys() -> None:
    """All baseline platform intents must have explicit thresholds; the
    forward-compat promo/sale/voucher keys (Cluster C1) are seeded
    pending intent-classifier extension and the strict 0.40 floor lives
    here as a dormant gate."""
    expected = {
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
        "promo",
        "sale",
        "voucher",
    }
    assert set(DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT.keys()) == expected


def test_per_intent_dict_values_within_unit_range() -> None:
    """Every threshold must be a float in [0.0, 1.0]."""
    for intent, score in DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT.items():
        assert isinstance(score, float), f"{intent} threshold not float"
        assert 0.0 <= score <= 1.0, f"{intent} threshold out of range: {score}"


def test_factoid_threshold_strict() -> None:
    """Factoid CRAG threshold pinned at 0.25 (T1-Smartness re-calibration).

    History: baseline 0.30 → Cluster C1 tightened to 0.35 to harden the
    gray zone (top_score 0.18..0.30) after HALLU Q7. Later
    LOAD_TEST_VERDICT Q18 Điều 45 showed 0.35 over-rejected single-
    article legal / regulatory factoid queries (dense retrieval scores
    typically 0.30..0.40), triggering rewrite_retry loops. Lowered to
    0.25 by multi-agent-r4 (commit 4e8a83d, 2026-05-15). HALLU=0
    sacred preserved by Anti-fake-section sysprompt + grounding gate.
    """
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"] == 0.25


def test_multi_hop_threshold_loose() -> None:
    """Multi-hop synthesis needs broader recall → 0.15."""
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["multi_hop"] == 0.15


def test_comparison_and_aggregation_threshold_mid() -> None:
    """Synthesis-style intents at 0.20."""
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["comparison"] == 0.20
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["aggregation"] == 0.20


def test_refuse_intents_match_global_strict() -> None:
    """Greeting / OOS / feedback should not loosen the strict default."""
    for intent in ("greeting", "out_of_scope", "feedback"):
        assert (
            DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT[intent]
            == DEFAULT_CRAG_MIN_FALLBACK_SCORE
        )


def test_resolve_uses_intent_dict_when_intent_present() -> None:
    """Lookup picks the per-intent value when intent is in the dict."""
    state = {"intent": "multi_hop", "pipeline_config": {}}
    assert _resolve_min_score(state) == 0.15

    state = {"intent": "comparison", "pipeline_config": {}}
    assert _resolve_min_score(state) == 0.20


def test_resolve_falls_back_to_global_when_intent_unknown() -> None:
    """Unknown intent → fall back to global default constant."""
    state = {"intent": "novel_intent_not_in_dict", "pipeline_config": {}}
    assert _resolve_min_score(state) == DEFAULT_CRAG_MIN_FALLBACK_SCORE


def test_resolve_falls_back_to_global_when_intent_missing() -> None:
    """Missing intent key → defaults to ``factoid`` (strict)."""
    state = {"pipeline_config": {}}
    assert _resolve_min_score(state) == DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"]


def test_resolve_per_bot_dict_override_wins() -> None:
    """Per-bot pipeline_config dict must override the constant default."""
    state = {
        "intent": "factoid",
        "pipeline_config": {
            "crag_min_fallback_score_by_intent": {"factoid": 0.10},
        },
    }
    assert _resolve_min_score(state) == 0.10


def test_resolve_per_bot_global_override_wins_when_intent_not_in_override_dict() -> None:
    """When the per-bot dict omits an intent, fall back to per-bot global."""
    state = {
        "intent": "multi_hop",
        "pipeline_config": {
            "crag_min_fallback_score_by_intent": {"factoid": 0.10},
            "crag_min_fallback_score": 0.05,
        },
    }
    assert _resolve_min_score(state) == 0.05


def test_resolve_handles_malformed_override_gracefully() -> None:
    """Non-dict override (e.g. JSON string slip-through) must not crash; the
    code must fall back to the global threshold."""
    state = {
        "intent": "factoid",
        "pipeline_config": {
            "crag_min_fallback_score_by_intent": "not_a_dict",
        },
    }
    # Falls through to global default.
    assert _resolve_min_score(state) == DEFAULT_CRAG_MIN_FALLBACK_SCORE
