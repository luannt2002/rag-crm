"""Unit tests — S2 generation structured sub-answer path.

Covers:
  1. GenerateOutput accepts the optional ``sub_answers`` list (backward
     compatible: default empty) and SubAnswerItem field shape.
  2. GenerateFlatOutput stays flat (no ``sub_answers`` field).
  3. The generate-node schema selector (`_resolve_generate_schema`) picks
     the structured schema ONLY when the flag is ON and the intent is a
     gated multi-fact intent; every other case keeps the flat schema.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.dto.llm_schemas import (
    GenerateFlatOutput,
    GenerateOutput,
    SubAnswerItem,
)
from ragbot.orchestration.query_graph import _resolve_generate_schema


# ---------------------------------------------------------------------------
# 1. DTO — GenerateOutput.sub_answers is optional + backward compatible
# ---------------------------------------------------------------------------


def test_generate_output_sub_answers_defaults_empty() -> None:
    """Omitting sub_answers yields an empty list — legacy callers unaffected."""
    out = GenerateOutput(answer="hello")
    assert out.sub_answers == []
    assert out.answer == "hello"


def test_generate_output_accepts_sub_answers() -> None:
    """sub_answers accepts SubAnswerItem rows; values round-trip intact."""
    out = GenerateOutput(
        answer="A costs 10, B costs 20.",
        sub_answers=[
            {"facet": "A price", "value": "10", "citation": "chunk:abc"},
            {"facet": "B price", "value": "20", "citation": None},
        ],
    )
    assert len(out.sub_answers) == 2
    assert isinstance(out.sub_answers[0], SubAnswerItem)
    assert out.sub_answers[0].facet == "A price"
    assert out.sub_answers[0].value == "10"
    assert out.sub_answers[0].citation == "chunk:abc"
    # citation is optional → None preserved, not coerced.
    assert out.sub_answers[1].citation is None


def test_sub_answer_item_requires_facet_and_value() -> None:
    """facet + value are required non-empty; missing → ValidationError."""
    with pytest.raises(ValidationError):
        SubAnswerItem(value="10")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SubAnswerItem(facet="A price")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SubAnswerItem(facet="", value="10")


def test_generate_flat_output_has_no_sub_answers_field() -> None:
    """The flat schema must NOT advertise sub_answers (lean JSON schema)."""
    assert "sub_answers" not in GenerateFlatOutput.model_fields
    flat = GenerateFlatOutput(answer="single fact")
    assert flat.answer == "single fact"


# ---------------------------------------------------------------------------
# 3. Schema selector — flag + intent gate
# ---------------------------------------------------------------------------


def _state(*, flag: bool, intent: str) -> dict:
    return {
        "pipeline_config": {"structured_subanswer_enabled": flag},
        "intent": intent,
    }


@pytest.mark.parametrize(
    "intent",
    ["aggregation", "comparison", "multi_hop"],
)
def test_structured_schema_for_gated_intents_when_flag_on(intent: str) -> None:
    """Flag ON + gated multi-fact intent → structured GenerateOutput schema."""
    assert _resolve_generate_schema(_state(flag=True, intent=intent)) is GenerateOutput


@pytest.mark.parametrize(
    "intent",
    ["factoid", "greeting", "chitchat", "out_of_scope", "vu_vo", "feedback"],
)
def test_flat_schema_for_non_gated_intents_even_when_flag_on(intent: str) -> None:
    """Flag ON but non-multi-fact intent → keep lean flat schema (no bloat)."""
    assert _resolve_generate_schema(_state(flag=True, intent=intent)) is GenerateFlatOutput


@pytest.mark.parametrize(
    "intent",
    ["aggregation", "comparison", "multi_hop", "factoid"],
)
def test_flat_schema_when_flag_off(intent: str) -> None:
    """Flag OFF → flat schema for EVERY intent (default behaviour unchanged)."""
    assert _resolve_generate_schema(_state(flag=False, intent=intent)) is GenerateFlatOutput


def test_flag_defaults_off_when_config_absent() -> None:
    """No pipeline_config key → literal False fallback → flat schema."""
    state = {"pipeline_config": {}, "intent": "aggregation"}
    assert _resolve_generate_schema(state) is GenerateFlatOutput


def test_none_intent_with_flag_on_keeps_flat() -> None:
    """Missing/None intent must not crash and must keep the flat schema."""
    state = {"pipeline_config": {"structured_subanswer_enabled": True}, "intent": None}
    assert _resolve_generate_schema(state) is GenerateFlatOutput
