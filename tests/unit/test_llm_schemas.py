"""Unit tests for `application.dto.llm_schemas` ( #3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.dto.llm_schemas import (
    DecomposeOutput,
    GradeOutput,
    ReflectOutput,
)


class TestGradeOutput:
    def test_valid_yes(self) -> None:
        g = GradeOutput(grade="yes", reason="matches query intent")
        assert g.grade == "yes"
        assert g.reason == "matches query intent"

    def test_valid_partial_with_default_reason(self) -> None:
        g = GradeOutput(grade="partial")
        assert g.grade == "partial"
        assert g.reason == ""

    def test_invalid_grade_raises(self) -> None:
        with pytest.raises(ValidationError):
            GradeOutput(grade="maybe", reason="x")  # type: ignore[arg-type]

    def test_reason_length_capped(self) -> None:
        with pytest.raises(ValidationError):
            GradeOutput(grade="no", reason="x" * 1001)


class TestReflectOutput:
    def test_valid_keep(self) -> None:
        r = ReflectOutput(action="keep", reason="answer is complete")
        assert r.action == "keep"

    def test_valid_rewrite(self) -> None:
        r = ReflectOutput(action="rewrite")
        assert r.action == "rewrite"
        assert r.reason == ""

    def test_invalid_action_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReflectOutput(action="retry", reason="x")  # type: ignore[arg-type]


class TestDecomposeOutput:
    def test_valid_two_subqueries(self) -> None:
        d = DecomposeOutput(sub_queries=["What is A?", "What is B?"])
        assert len(d.sub_queries) == 2

    def test_default_empty_list(self) -> None:
        d = DecomposeOutput()
        assert d.sub_queries == []

    def test_max_items_cap(self) -> None:
        # Schema enforces max length so >5 items must raise.
        too_many = [f"Q{i}" for i in range(99)]
        with pytest.raises(ValidationError):
            DecomposeOutput(sub_queries=too_many)


class TestSchemaJsonExport:
    """JSON-schema export must succeed — that's how `call_with_schema` plugs
    Pydantic into the OpenAI structured-output / Anthropic tool-use path."""

    def test_grade_json_schema(self) -> None:
        s = GradeOutput.model_json_schema()
        assert s["properties"]["grade"]["enum"] == ["yes", "no", "partial"]

    def test_reflect_json_schema(self) -> None:
        s = ReflectOutput.model_json_schema()
        assert s["properties"]["action"]["enum"] == ["keep", "rewrite", "reject"]

    def test_decompose_json_schema(self) -> None:
        s = DecomposeOutput.model_json_schema()
        assert "sub_queries" in s["properties"]
