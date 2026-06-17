"""Unit tests for `application.dto.llm_schemas.GradeBatchOutput`.

Phase-3 HIGH-7 fix: batch grading via single LLM call with structured
output replaces N parallel per-chunk calls (5 -> 1 on top_K=5).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.dto.llm_schemas import ChunkGradeItem, GradeBatchOutput


def test_chunk_grade_item_accepts_three_grades() -> None:
    for verdict in ("yes", "no", "partial"):
        item = ChunkGradeItem(chunk_id="abc-123", grade=verdict)  # type: ignore[arg-type]
        assert item.grade == verdict


def test_chunk_grade_item_rejects_unknown_grade() -> None:
    with pytest.raises(ValidationError):
        ChunkGradeItem(chunk_id="abc-123", grade="maybe")  # type: ignore[arg-type]


def test_chunk_grade_item_requires_chunk_id() -> None:
    with pytest.raises(ValidationError):
        ChunkGradeItem(chunk_id="", grade="yes")  # type: ignore[arg-type]


def test_grade_batch_output_min_one_grade_required() -> None:
    with pytest.raises(ValidationError):
        GradeBatchOutput(grades=[])


def test_grade_batch_output_round_trip_three_chunks() -> None:
    grades = [
        ChunkGradeItem(chunk_id=f"id-{i}", grade=g)  # type: ignore[arg-type]
        for i, g in enumerate(("yes", "no", "partial"))
    ]
    batch = GradeBatchOutput(grades=grades)
    assert len(batch.grades) == 3
    assert [g.grade for g in batch.grades] == ["yes", "no", "partial"]


def test_grade_batch_output_extra_fields_forbidden() -> None:
    grades = [ChunkGradeItem(chunk_id="id-0", grade="yes")]  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GradeBatchOutput(grades=grades, surprise="x")  # type: ignore[call-arg]
