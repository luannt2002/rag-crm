"""[T1-Smartness] Stream S6 — Adaptive Router L1 classifier unit tests.

Pure source-level unit tests for the domain-neutral query complexity
classifier. No LangGraph boot, no DB, no LLM. All knobs flow through
an injected config getter so the tests stay deterministic.
"""

from __future__ import annotations

import pytest

from ragbot.orchestration.nodes.query_complexity import (
    classify_query_complexity,
)
from ragbot.shared.constants import (
    DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON,
    DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
    DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_NUMBERS,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
)


_DEFAULT_GETTER_OVERRIDES: dict[str, object] = {
    "query_complexity.weight_comma": DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA,
    "query_complexity.weight_conjunction": DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
    "query_complexity.weight_numbers": DEFAULT_QUERY_COMPLEXITY_WEIGHT_NUMBERS,
    "query_complexity.weight_question": DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
    "query_complexity.length_normalizer": DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
    "query_complexity.complexity_threshold": DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
    "query_complexity.conjunctions": DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON,
}


def _make_getter(overrides: dict[str, object] | None = None):
    """Return a config getter that honours ``overrides`` over defaults."""
    merged = dict(_DEFAULT_GETTER_OVERRIDES)
    if overrides:
        merged.update(overrides)

    def _getter(key: str, default):  # type: ignore[no-untyped-def]
        return merged.get(key, default)

    return _getter


# ---------------------------------------------------------------------------
# 1. Single-entity simple query → "simple".
# ---------------------------------------------------------------------------
def test_short_single_entity_query_is_simple() -> None:
    """A single short article reference must not trigger Layer 3."""
    label, score = classify_query_complexity(
        "Điều 11", config_getter=_make_getter(),
    )
    assert label == "simple"
    assert score < DEFAULT_QUERY_COMPLEXITY_THRESHOLD


# ---------------------------------------------------------------------------
# 2. Multi-entity comma list → "complex".
# ---------------------------------------------------------------------------
def test_comma_separated_entities_are_complex() -> None:
    """Two commas signal a three-item enumeration; classifier must fire."""
    label, score = classify_query_complexity(
        "Điều 11, 33, 44", config_getter=_make_getter(),
    )
    assert label == "complex"
    assert score >= DEFAULT_QUERY_COMPLEXITY_THRESHOLD


# ---------------------------------------------------------------------------
# 3. Conjunction trigger → "complex".
# ---------------------------------------------------------------------------
def test_vietnamese_conjunction_triggers_complex() -> None:
    """The Vietnamese ' và ' token MUST count as a conjunction signal."""
    label, _ = classify_query_complexity(
        "Điều 11 và Điều 33 và Điều 55",
        config_getter=_make_getter(),
    )
    assert label == "complex"


# ---------------------------------------------------------------------------
# 4. Domain-neutral assertion: classifier MUST NOT depend on domain words.
# ---------------------------------------------------------------------------
def test_classifier_is_domain_neutral_for_product_queries() -> None:
    """A query about products must classify on the SAME signals (commas,
    conjunctions, numbers) — never on the noun 'sản phẩm' itself.

    Swap 'sản phẩm' for any other noun and the score MUST be identical:
    the classifier is signal-driven, not subject-driven."""
    # Both queries carry the SAME signal pattern (' và ', commas, no
    # numbers, identical word count) — score must be identical regardless
    # of the subject noun. We swap noun ↔ noun keeping token count equal.
    score_product = classify_query_complexity(
        "alpha beta C và alpha beta D, E, F",
        config_getter=_make_getter(),
    )[1]
    score_item = classify_query_complexity(
        "delta gamma C và delta gamma D, E, F",
        config_getter=_make_getter(),
    )[1]
    assert score_product == pytest.approx(score_item, rel=1e-6)
    # And the conjunction + comma combo crosses the default threshold.
    label, _ = classify_query_complexity(
        "sản phẩm A và sản phẩm B, C, D", config_getter=_make_getter(),
    )
    assert label == "complex"


# ---------------------------------------------------------------------------
# 5. Generic single-clause question → "simple".
# ---------------------------------------------------------------------------
def test_single_clause_question_is_simple() -> None:
    """A plain single-clause question with no commas / conjunctions / extra
    numbers must classify as simple."""
    label, _ = classify_query_complexity(
        "thông tư nói gì?", config_getter=_make_getter(),
    )
    assert label == "simple"


# ---------------------------------------------------------------------------
# 6. Empty / None-like inputs → safe "simple", zero score.
# ---------------------------------------------------------------------------
def test_empty_string_is_simple_zero_score() -> None:
    label, score = classify_query_complexity("", config_getter=_make_getter())
    assert label == "simple"
    assert score == 0.0


# ---------------------------------------------------------------------------
# 7. Threshold tunable: very high threshold makes every query simple.
# ---------------------------------------------------------------------------
def test_high_threshold_disables_complex_label() -> None:
    """Raising threshold to 100 must force every signal-rich query
    back to 'simple' (the knob is a real bot-owner-facing dial)."""
    getter = _make_getter({"query_complexity.complexity_threshold": 100.0})
    label, _ = classify_query_complexity(
        "A, B, C và D và E, F, G?", config_getter=getter,
    )
    assert label == "simple"


# ---------------------------------------------------------------------------
# 8. Return type is a (str, float) tuple; score is positive on non-empty.
# ---------------------------------------------------------------------------
def test_return_shape_is_label_score_tuple() -> None:
    out = classify_query_complexity(
        "A, B và C", config_getter=_make_getter(),
    )
    assert isinstance(out, tuple)
    label, score = out
    assert isinstance(label, str)
    assert isinstance(score, float)
    assert score > 0.0


# ---------------------------------------------------------------------------
# 9. Multi-question mark triggers complex.
# ---------------------------------------------------------------------------
def test_multi_question_marks_trigger_complex() -> None:
    """Question marks must contribute monotonically: more '?' → higher score.
    A four-part question MUST exceed a single-part question with otherwise
    identical content."""
    one_q_score = classify_query_complexity(
        "What is A and what is B",
        config_getter=_make_getter(),
    )[1]
    many_q_score = classify_query_complexity(
        "What is A? What is B? What is C? What is D?",
        config_getter=_make_getter(),
    )[1]
    assert many_q_score > one_q_score
    # And a sufficiently long multi-question query crosses the threshold.
    label, _ = classify_query_complexity(
        "What is A? What is B? What is C? What is D?",
        config_getter=_make_getter(),
    )
    assert label == "complex"


# ---------------------------------------------------------------------------
# 10. Conjunction list configurable — bot owner adds a new language.
# ---------------------------------------------------------------------------
def test_custom_conjunction_list_is_honoured() -> None:
    """An operator should be able to add a new conjunction token (e.g. a
    new language) by editing system_config without code changes."""
    getter = _make_getter({
        "query_complexity.conjunctions": '["aussi"]',
    })
    label, _ = classify_query_complexity(
        "fait X aussi Y aussi Z aussi W", config_getter=getter,
    )
    assert label == "complex"


# ---------------------------------------------------------------------------
# 11. Malformed conjunctions config falls back gracefully (no crash).
# ---------------------------------------------------------------------------
def test_malformed_conjunctions_config_falls_back_silently() -> None:
    """A non-JSON config value must not raise; the classifier degrades
    to commas + numbers + length signals only."""
    getter = _make_getter({"query_complexity.conjunctions": "{not json"})
    label, score = classify_query_complexity(
        "Điều 11", config_getter=getter,
    )
    # Single-entity still simple.
    assert label == "simple"
    assert score >= 0.0


# ---------------------------------------------------------------------------
# 12. Default-getter path (no injection) does not crash and returns valid tuple.
# ---------------------------------------------------------------------------
def test_default_getter_path_returns_valid_tuple() -> None:
    """When no ``config_getter`` is supplied, the function MUST still
    return a (str, float) tuple. The DB call inside ``get_boot_config``
    is allowed to fail silently — the constants fallback covers it."""
    out = classify_query_complexity("hello world")
    assert isinstance(out, tuple)
    label, score = out
    assert label in {"simple", "complex"}
    assert isinstance(score, float)


# ---------------------------------------------------------------------------
# 13. Non-string input safely returns simple.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_input", [None, 42, [], {}])
def test_non_string_input_is_simple_zero_score(bad_input) -> None:
    label, score = classify_query_complexity(
        bad_input,  # type: ignore[arg-type]
        config_getter=_make_getter(),
    )
    assert label == "simple"
    assert score == 0.0
