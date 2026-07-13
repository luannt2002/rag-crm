"""UnderstandOutput Pydantic schema — validates condensed query + intent.

Replaces the manual JSON parse + substring fallback that mis-classified
queries containing multiple intent keywords (e.g. "comparison of
multi-hop topics" → factoid by accident).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.dto.llm_schemas import UnderstandOutput


def test_accepts_minimal_valid_payload():
    out = UnderstandOutput(condensed_query="What is X?", intent="factoid")
    assert out.condensed_query == "What is X?"
    assert out.intent == "factoid"


def test_accepts_empty_condensed_query():
    """S0-C: condensed_query is now OPTIONAL (default "").

    A model that emits an empty rewrite must NOT break understand — the
    orchestrator keeps the original query when condensed is empty, so the
    schema degrades to a no-rewrite pass instead of a validation failure.
    """
    out = UnderstandOutput(condensed_query="", intent="factoid")
    assert out.condensed_query == ""
    assert out.intent == "factoid"


def test_accepts_missing_condensed_query():
    """S0-C: omitting condensed_query entirely defaults to "" (qwen3 case)."""
    out = UnderstandOutput.model_validate({"intent": "factoid"})
    assert out.condensed_query == ""
    assert out.intent == "factoid"


def test_rejects_unknown_intent_typo():
    with pytest.raises(ValidationError):
        UnderstandOutput(condensed_query="hi", intent="comparizon")


def test_rejects_extra_field_strict_mode():
    """``extra='forbid'`` mirrors OpenAI strict json_schema requirements."""
    with pytest.raises(ValidationError):
        UnderstandOutput(
            condensed_query="hi",
            intent="factoid",
            unexpected="x",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "intent",
    [
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
    ],
)
def test_accepts_every_supported_intent(intent: str):
    out = UnderstandOutput(condensed_query="q", intent=intent)
    assert out.intent == intent


def test_intent_literal_matches_orchestrator_valid_intents():
    """Schema Literal must equal _VALID_INTENTS in orchestrator (single source)."""
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    schema = UnderstandOutput.model_json_schema()
    intent_enum = schema["properties"]["intent"]["enum"]
    assert sorted(intent_enum) == sorted(_VALID_INTENTS), (
        "UnderstandOutput.intent Literal drifted from _VALID_INTENTS — keep them in sync"
    )


def test_rejects_condensed_query_above_max_len():
    from ragbot.shared.constants import DEFAULT_UNDERSTAND_CONDENSED_QUERY_MAX_LEN

    too_long = "x" * (DEFAULT_UNDERSTAND_CONDENSED_QUERY_MAX_LEN + 1)
    with pytest.raises(ValidationError):
        UnderstandOutput(condensed_query=too_long, intent="factoid")


def test_accepts_query_alias_from_gateway():
    """Some OpenAI-shape gateways echo the prompt back under a bare ``query``
    key instead of ``condensed_query``. Accept it as the condensed value so a
    valid intent classification is not thrown away with an ``extra_forbidden``
    failure (which otherwise forces a wasted repair round-trip).

    Reproduces the dominant load-test failure (2026-07-13): 56/62
    ``structured_output_repair_retry`` were ``UnderstandOutput`` payloads of
    the form ``{"query": "<raw question>", "intent": ...}``.
    """
    out = UnderstandOutput.model_validate(
        {"query": "Thời tiết Hà Nội hôm nay thế nào?", "intent": "factoid"}
    )
    assert out.condensed_query == "Thời tiết Hà Nội hôm nay thế nào?"
    assert out.intent == "factoid"


def test_condensed_query_wins_over_query_alias():
    """If the model emits BOTH keys, the canonical ``condensed_query`` wins."""
    out = UnderstandOutput.model_validate(
        {"condensed_query": "canonical", "query": "raw echo", "intent": "factoid"}
    )
    assert out.condensed_query == "canonical"


def test_query_alias_does_not_leak_into_json_schema():
    """Gateway tolerance must NOT change the schema sent to strict providers:
    the generated contract still exposes ``condensed_query`` and never ``query``.
    """
    props = UnderstandOutput.model_json_schema()["properties"]
    assert "condensed_query" in props
    assert "query" not in props


def test_query_alias_still_rejects_other_extra_fields():
    """The alias maps only ``query``; every other unexpected key is still
    forbidden (``extra='forbid'`` preserved)."""
    with pytest.raises(ValidationError):
        UnderstandOutput.model_validate(
            {"query": "hi", "intent": "factoid", "unexpected": "x"}
        )
