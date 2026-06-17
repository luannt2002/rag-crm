"""Unit tests for `application.dto.llm_schemas.GenerateOutput` + `CitationItem`.

Phase-3 CRIT-1 fix: structured output for the generation node so citations
the user sees are the citations the LLM actually claimed, not platform-
synthesized ones.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.dto.llm_schemas import CitationItem, GenerateOutput
from ragbot.shared.constants import (
    DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS,
    DEFAULT_GENERATE_CITATIONS_MAX_N,
)


def test_citation_item_requires_chunk_id_and_quote() -> None:
    with pytest.raises(ValidationError):
        CitationItem(chunk_id="", quote="some quote")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CitationItem(chunk_id="abc-123", quote="")  # type: ignore[arg-type]


def test_citation_item_quote_length_capped() -> None:
    too_long = "x" * (DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS + 1)
    with pytest.raises(ValidationError):
        CitationItem(chunk_id="abc-123", quote=too_long)


def test_citation_item_at_quote_max_chars_passes() -> None:
    just_right = "x" * DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS
    item = CitationItem(chunk_id="abc-123", quote=just_right)
    assert item.chunk_id == "abc-123"
    assert len(item.quote) == DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS


def test_generate_output_default_citations_empty_and_used_no_context_false() -> None:
    out = GenerateOutput(answer="Hello.")
    assert out.citations == []
    assert out.used_no_context is False


def test_generate_output_answer_required_min_length_one() -> None:
    with pytest.raises(ValidationError):
        GenerateOutput(answer="")


def test_generate_output_citations_max_length_enforced() -> None:
    cites = [
        CitationItem(chunk_id=f"id-{i}", quote=f"quote {i}")
        for i in range(DEFAULT_GENERATE_CITATIONS_MAX_N + 1)
    ]
    with pytest.raises(ValidationError):
        GenerateOutput(answer="ans", citations=cites)


def test_generate_output_extra_fields_forbidden() -> None:
    # OpenAI strict json_schema mode requires additionalProperties: false.
    with pytest.raises(ValidationError):
        GenerateOutput(answer="ok", surprise="x")  # type: ignore[call-arg]


def test_generate_output_used_no_context_true_round_trip() -> None:
    out = GenerateOutput(answer="Tôi không có thông tin.", used_no_context=True)
    assert out.used_no_context is True
    assert out.citations == []
