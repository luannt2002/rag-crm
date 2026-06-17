"""Pin test: BM25 sparse query strips VN filler tokens.

Pre-2026-05-27: 'Chương 3 nói gì' → websearch_to_tsquery built AND of
4 tokens → 0 chunks match. Filler 'nói gì' loại bỏ tất cả.
After fix: filler stripped → 'Chương 3' tsquery → 66 chunks match.
"""
from __future__ import annotations
from ragbot.shared.text_utils import strip_vn_filler_tokens


def test_strip_basic_filler_pairs() -> None:
    assert strip_vn_filler_tokens("Chương 3 nói gì") == "Chương 3"
    assert strip_vn_filler_tokens("Điều 55 ra sao") == "Điều 55"
    assert strip_vn_filler_tokens("Mục 2 là sao") == "Mục 2"


def test_strip_idempotent() -> None:
    assert strip_vn_filler_tokens("Chương 3") == "Chương 3"


def test_strip_empty_input() -> None:
    assert strip_vn_filler_tokens("") == ""
    assert strip_vn_filler_tokens(None) == ""


def test_strip_preserves_unrelated_text() -> None:
    assert strip_vn_filler_tokens("Hello world") == "Hello world"
    assert strip_vn_filler_tokens("giá triệt lông bao nhiêu") == "giá triệt lông bao nhiêu"


def test_strip_case_insensitive() -> None:
    assert strip_vn_filler_tokens("Chương 3 NÓI GÌ") == "Chương 3"
    assert strip_vn_filler_tokens("Điều 55 Ra Sao") == "Điều 55"


def test_strip_handles_multiple_fillers() -> None:
    assert strip_vn_filler_tokens("Chương 3 nói gì ạ") == "Chương 3"
    assert strip_vn_filler_tokens("Mục 5 là sao nhé") == "Mục 5"


def test_strip_custom_filler_list() -> None:
    out = strip_vn_filler_tokens("foo BAR baz", filler_tokens=("bar",))
    assert out == "foo baz"


def test_strip_compound_phrase_before_short() -> None:
    """'nói về gì' must strip as one unit, not 'nói' + 'về' + 'gì'."""
    out = strip_vn_filler_tokens("Chương 3 nói về gì", filler_tokens=("nói về gì", "gì"))
    # Compound 'nói về gì' applied first → entire phrase stripped
    assert out == "Chương 3"
