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
