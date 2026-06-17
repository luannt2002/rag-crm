"""Pin test: VN structural anchor detection + LIKE pattern builder.

Pre-2026-05-27: query 'Chương 3 nói gì' → embedding zembed-1 retrieved
chunks Chương 2 (majority) because zero-shot model doesn't grasp
structural identifiers. After fix: anchor detected → pgvector pre-filter
narrows scope to chunks containing 'Chương 3' literal.
"""
from __future__ import annotations

from ragbot.shared.chunking import (
    build_vn_structural_like_clauses,
    detect_vn_structural_anchor,
)


def test_detect_arabic_anchor() -> None:
    assert detect_vn_structural_anchor("Chương 3 nói gì") == ("Chương", "3")
    assert detect_vn_structural_anchor("Điều 55 quy định") == ("Điều", "55")
    assert detect_vn_structural_anchor("Mục 2") == ("Mục", "2")


def test_detect_roman_anchor_converts_to_arabic() -> None:
    assert detect_vn_structural_anchor("Chương III nói gì") == ("Chương", "3")
    assert detect_vn_structural_anchor("Mục V") == ("Mục", "5")
    assert detect_vn_structural_anchor("Phần II") == ("Phần", "2")


def test_detect_lowercase_prefix_canonicalized() -> None:
    assert detect_vn_structural_anchor("chương 3 nói gì") == ("Chương", "3")
    assert detect_vn_structural_anchor("điều 55") == ("Điều", "55")


def test_detect_returns_none_when_no_anchor() -> None:
    assert detect_vn_structural_anchor("giá triệt lông") is None
    assert detect_vn_structural_anchor("Hello world") is None
    assert detect_vn_structural_anchor("") is None
    assert detect_vn_structural_anchor(None) is None


def test_detect_returns_none_for_multi_anchor() -> None:
    """Compound queries skip pre-filter — let normal retrieve handle them."""
    assert detect_vn_structural_anchor("So sánh Chương 2 và Chương 3") is None
    assert detect_vn_structural_anchor("Điều 22 và Điều 55") is None


def test_build_like_clauses_covers_corpus_formats() -> None:
    patterns = build_vn_structural_like_clauses(("Chương", "3"))
    assert len(patterns) == 4
    # Cover the 4 dominant formats observed in TT 09/2020 chunks
    assert "%[Chương 3%" in patterns
    assert "%Chương 3,%" in patterns
    assert "%Chương 3 >%" in patterns
    assert "%Chương 3]%" in patterns


def test_build_like_clauses_for_dieu() -> None:
    patterns = build_vn_structural_like_clauses(("Điều", "55"))
    assert all("Điều 55" in p for p in patterns)
