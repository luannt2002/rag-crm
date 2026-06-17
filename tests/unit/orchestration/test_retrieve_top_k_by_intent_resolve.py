"""Pin tests — per-intent retrieve top_k resolver in the retrieve node.

We test the resolver logic in isolation (the same expression used inline
at the retrieve call sites) rather than spinning the full LangGraph node.
Same pattern as ``test_per_intent_caps.py`` for rerank_top_n_by_intent.

Coverage:
  1. aggregation → uses the wide 40-cap.
  2. factoid → uses 15.
  3. greeting → uses the narrow 5-cap.
  4. unknown intent → falls back to DEFAULT_TOP_K global.
  5. invalid / non-dict config → falls back to DEFAULT_TOP_K.
  6. intent_override flag is True when a dict hit occurred.
  7. intent_override flag is False on fallback.
  8. Every canonical intent has a value in DEFAULT_RETRIEVE_TOP_K_BY_INTENT.
  9. All values are positive integers.
 10. Aggregation cap > factoid cap (wider funnel for aggregation).
 11. Greeting cap < factoid cap (narrower funnel for lightweight intents).
"""

from __future__ import annotations

import pytest

from ragbot.shared.constants import DEFAULT_TOP_K


# ---------------------------------------------------------------------------
# Resolver helper — mirrors the inline expression in query_graph retrieve node
# ---------------------------------------------------------------------------


def _resolve_top_k(
    intent: str,
    by_intent_cfg: object,
    global_fallback: int,
) -> tuple[int, bool]:
    """Mirror of the retrieve node per-intent top_k resolver.

    Returns ``(top_k, intent_override_used)``.
    Kept in lock-step with query_graph.py retrieve node logic.
    """
    if isinstance(by_intent_cfg, dict) and intent in by_intent_cfg:
        try:
            return (int(by_intent_cfg[intent]), True)
        except (TypeError, ValueError):
            return (global_fallback, False)
    return (global_fallback, False)


# ---------------------------------------------------------------------------
# 1. aggregation uses 40
# ---------------------------------------------------------------------------


def test_aggregation_uses_40() -> None:
    """Aggregation must get the widest funnel (40) for multi-row retrieval."""
    by_intent = {"aggregation": 40, "factoid": 15, "greeting": 5}
    top_k, override = _resolve_top_k("aggregation", by_intent, DEFAULT_TOP_K)
    assert top_k == 40
    assert override is True


# ---------------------------------------------------------------------------
# 2. factoid uses 15
# ---------------------------------------------------------------------------


def test_factoid_uses_15() -> None:
    by_intent = {"aggregation": 40, "factoid": 15, "greeting": 5}
    top_k, override = _resolve_top_k("factoid", by_intent, DEFAULT_TOP_K)
    assert top_k == 15
    assert override is True


# ---------------------------------------------------------------------------
# 3. greeting uses 5
# ---------------------------------------------------------------------------


def test_greeting_uses_5() -> None:
    by_intent = {"aggregation": 40, "factoid": 15, "greeting": 5}
    top_k, override = _resolve_top_k("greeting", by_intent, DEFAULT_TOP_K)
    assert top_k == 5
    assert override is True


# ---------------------------------------------------------------------------
# 4. missing intent falls back to DEFAULT_TOP_K
# ---------------------------------------------------------------------------


def test_missing_intent_falls_back_to_default_top_k() -> None:
    """Intent not in dict → global fallback, no override flag."""
    by_intent = {"factoid": 15}
    top_k, override = _resolve_top_k("totally_unknown_intent", by_intent, DEFAULT_TOP_K)
    assert top_k == DEFAULT_TOP_K
    assert override is False


def test_empty_intent_falls_back_to_default_top_k() -> None:
    by_intent = {"factoid": 15, "aggregation": 40}
    top_k, override = _resolve_top_k("", by_intent, DEFAULT_TOP_K)
    assert top_k == DEFAULT_TOP_K
    assert override is False


# ---------------------------------------------------------------------------
# 5. invalid / non-dict config falls back
# ---------------------------------------------------------------------------


def test_invalid_intent_dict_falls_back_to_default() -> None:
    """Operator sets the row to a non-dict value → must not crash."""
    top_k, override = _resolve_top_k("aggregation", "not-a-dict", DEFAULT_TOP_K)
    assert top_k == DEFAULT_TOP_K
    assert override is False


def test_none_config_falls_back_to_default() -> None:
    top_k, override = _resolve_top_k("aggregation", None, DEFAULT_TOP_K)
    assert top_k == DEFAULT_TOP_K
    assert override is False


def test_malformed_value_falls_back_silently() -> None:
    """Operator typo: stringly-typed value int() cannot parse."""
    by_intent = {"aggregation": "forty"}
    top_k, override = _resolve_top_k("aggregation", by_intent, DEFAULT_TOP_K)
    assert top_k == DEFAULT_TOP_K
    assert override is False


# ---------------------------------------------------------------------------
# 6 + 7. intent_override flag correctness
# ---------------------------------------------------------------------------


def test_intent_override_flag_logged_metadata_when_hit() -> None:
    """override flag must be True when the dict has the intent key."""
    by_intent = {"multi_hop": 30}
    _, override = _resolve_top_k("multi_hop", by_intent, DEFAULT_TOP_K)
    assert override is True


def test_intent_override_flag_false_on_fallback() -> None:
    _, override = _resolve_top_k("comparison", {}, DEFAULT_TOP_K)
    assert override is False


# ---------------------------------------------------------------------------
# 8. Every canonical intent has a value in DEFAULT_RETRIEVE_TOP_K_BY_INTENT
# ---------------------------------------------------------------------------

_CANONICAL_INTENTS = (
    "factoid",
    "comparison",
    "multi_hop",
    "aggregation",
    "out_of_scope",
    "greeting",
    "feedback",
    "chitchat",
    "vu_vo",
)


@pytest.mark.parametrize("intent", _CANONICAL_INTENTS)
def test_every_canonical_intent_has_retrieve_top_k_default(intent: str) -> None:
    """All taxonomy intents must have an explicit per-intent value."""
    from ragbot.shared.constants import DEFAULT_RETRIEVE_TOP_K_BY_INTENT

    assert intent in DEFAULT_RETRIEVE_TOP_K_BY_INTENT, (
        f"intent {intent!r} missing from DEFAULT_RETRIEVE_TOP_K_BY_INTENT"
    )


# ---------------------------------------------------------------------------
# 9. All values are positive integers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", _CANONICAL_INTENTS)
def test_every_retrieve_top_k_value_is_positive(intent: str) -> None:
    from ragbot.shared.constants import DEFAULT_RETRIEVE_TOP_K_BY_INTENT

    val = DEFAULT_RETRIEVE_TOP_K_BY_INTENT[intent]
    assert isinstance(val, int), f"intent {intent!r} value {val!r} is not int"
    assert val > 0, f"intent {intent!r} value {val} must be positive"


# ---------------------------------------------------------------------------
# 10. aggregation cap > factoid cap (wider funnel)
# ---------------------------------------------------------------------------


def test_aggregation_cap_wider_than_factoid() -> None:
    from ragbot.shared.constants import DEFAULT_RETRIEVE_TOP_K_BY_INTENT

    assert (
        DEFAULT_RETRIEVE_TOP_K_BY_INTENT["aggregation"]
        > DEFAULT_RETRIEVE_TOP_K_BY_INTENT["factoid"]
    ), "aggregation must have a wider retrieve funnel than factoid"


# ---------------------------------------------------------------------------
# 11. greeting cap < factoid cap (narrower for lightweight)
# ---------------------------------------------------------------------------


def test_greeting_cap_narrower_than_factoid() -> None:
    from ragbot.shared.constants import DEFAULT_RETRIEVE_TOP_K_BY_INTENT

    assert (
        DEFAULT_RETRIEVE_TOP_K_BY_INTENT["greeting"]
        < DEFAULT_RETRIEVE_TOP_K_BY_INTENT["factoid"]
    ), "greeting must have a narrower retrieve funnel than factoid"
