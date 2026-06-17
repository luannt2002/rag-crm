"""Pin tests: Roman ↔ Arabic numeral conversion helpers.

Pre-2026-05-27: corpus stored 'Chương III' literal; query 'chương 3' missed
all chunks. After fix: normalize both sides to arabic canonical → match.
"""
from __future__ import annotations

from ragbot.shared.chunking import (
    _arabic_to_roman,
    normalize_vn_section_numerals,
    roman_to_arabic,
)


def test_roman_to_arabic_basic() -> None:
    assert roman_to_arabic("I") == 1
    assert roman_to_arabic("III") == 3
    assert roman_to_arabic("IV") == 4
    assert roman_to_arabic("V") == 5
    assert roman_to_arabic("IX") == 9
    assert roman_to_arabic("X") == 10
    assert roman_to_arabic("XIII") == 13
    assert roman_to_arabic("XL") == 40
    assert roman_to_arabic("L") == 50
    assert roman_to_arabic("XC") == 90
    assert roman_to_arabic("C") == 100
    assert roman_to_arabic("CD") == 400
    assert roman_to_arabic("MCMXCIV") == 1994


def test_roman_to_arabic_case_insensitive() -> None:
    assert roman_to_arabic("iii") == 3
    assert roman_to_arabic("Iv") == 4
    assert roman_to_arabic("mcmxciv") == 1994


def test_roman_to_arabic_invalid_returns_none() -> None:
    assert roman_to_arabic("") is None
    assert roman_to_arabic("3") is None  # arabic, not roman
    assert roman_to_arabic("ABC") is None
    assert roman_to_arabic("IIII") is None  # malformed (round-trip fail)
    assert roman_to_arabic("VV") is None  # malformed
    assert roman_to_arabic("IC") is None  # not standard form
    assert roman_to_arabic("LL") is None
    assert roman_to_arabic("DD") is None


def test_roman_to_arabic_whitespace_trim() -> None:
    assert roman_to_arabic("  III  ") == 3
    assert roman_to_arabic("\tIV\n") == 4


def test_arabic_to_roman_internal_helper() -> None:
    """Internal helper used for round-trip validation."""
    assert _arabic_to_roman(3) == "III"
    assert _arabic_to_roman(4) == "IV"
    assert _arabic_to_roman(1994) == "MCMXCIV"
    assert _arabic_to_roman(0) == ""  # out of range
    assert _arabic_to_roman(4000) == ""  # out of range
    assert _arabic_to_roman(-5) == ""


def test_normalize_chuong_roman_to_arabic() -> None:
    assert normalize_vn_section_numerals("Chương III") == "Chương 3"
    assert normalize_vn_section_numerals("Chương I") == "Chương 1"
    assert normalize_vn_section_numerals("Chương V") == "Chương 5"
    assert normalize_vn_section_numerals("Chương XIII") == "Chương 13"


def test_normalize_muc_roman_to_arabic() -> None:
    assert normalize_vn_section_numerals("Mục V") == "Mục 5"
    assert normalize_vn_section_numerals("Mục II") == "Mục 2"


def test_normalize_phan_roman_to_arabic() -> None:
    assert normalize_vn_section_numerals("Phần II") == "Phần 2"


def test_normalize_path_with_dieu_preserved() -> None:
    """Điều luôn arabic — không convert; Chương convert."""
    assert (
        normalize_vn_section_numerals("Chương III > Điều 55")
        == "Chương 3 > Điều 55"
    )
    assert (
        normalize_vn_section_numerals("[Chương III > Mục 2 > Điều 55]")
        == "[Chương 3 > Mục 2 > Điều 55]"
    )


def test_normalize_idempotent_on_arabic() -> None:
    """Already-arabic input must pass through unchanged."""
    assert normalize_vn_section_numerals("Chương 3") == "Chương 3"
    assert normalize_vn_section_numerals("Mục 5") == "Mục 5"
    assert (
        normalize_vn_section_numerals("Chương 3 > Điều 55")
        == "Chương 3 > Điều 55"
    )


def test_normalize_preserves_unrelated_text() -> None:
    """Plain numbers / unrelated romans must not be touched."""
    assert normalize_vn_section_numerals("Vào lúc 5 giờ") == "Vào lúc 5 giờ"
    assert (
        normalize_vn_section_numerals("Class V student passed")
        == "Class V student passed"
    )
    # Standalone romans not after Chương|Mục|Phần stay untouched
    assert (
        normalize_vn_section_numerals("Section III paragraph IV")
        == "Section III paragraph IV"
    )


def test_normalize_canonicalizes_prefix_case() -> None:
    """Prefix is Title-cased regardless of input case.

    Required because embedding/BM25 treat 'chương 3' vs 'Chương 3' as
    different tokens. One canonical form aligns ingest + query sides.
    """
    assert normalize_vn_section_numerals("chương III") == "Chương 3"
    assert normalize_vn_section_numerals("CHƯƠNG III") == "Chương 3"
    assert normalize_vn_section_numerals("chương 3") == "Chương 3"
    assert normalize_vn_section_numerals("CHƯƠNG 3") == "Chương 3"
    assert normalize_vn_section_numerals("mục v") == "Mục 5"
    assert normalize_vn_section_numerals("MỤC 5") == "Mục 5"
    assert normalize_vn_section_numerals("phần II") == "Phần 2"


def test_normalize_arabic_query_capitalize_lowercase_prefix() -> None:
    """Critical case: user types 'chương 3 nói gì' (lowercase arabic).
    Must canonicalize to 'Chương 3 nói gì' so query matches chunks
    stored with 'Chương 3' (capital). Pre-2026-05-27 hotfix: regex only
    matched roman numerals, lowercase arabic prefix slipped through
    untouched → mismatch with chunk capitalization."""
    assert (
        normalize_vn_section_numerals("chương 3 nói gì")
        == "Chương 3 nói gì"
    )
    assert (
        normalize_vn_section_numerals("chương 1 có gì")
        == "Chương 1 có gì"
    )


def test_normalize_in_query_context() -> None:
    """End-user query patterns."""
    assert (
        normalize_vn_section_numerals("Chương III nói gì")
        == "Chương 3 nói gì"
    )
    assert (
        normalize_vn_section_numerals("tóm tắt Mục V Chương II")
        == "tóm tắt Mục 5 Chương 2"
    )
