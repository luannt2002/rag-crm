"""Structured-reference metadata extraction — unit tests.

Pins:
- ``Điều N`` / ``Khoản N`` / ``Mục N`` / ``Phụ lục X`` / ``Chương ROMAN``
  regex correctness across realistic VN-legal phrasings
- First-occurrence-wins semantics (chunks spanning multiple articles store
  the leading one)
- Empty / no-match input returns ``{}`` (no metadata pollution)
- Query-side extractor returns identical shape to ingest-side extractor
- Case-insensitive matching ("ĐIỀU 3" / "điều 3")
- Domain-neutral: returns empty dict on non-VN text — bots without
  structured refs see no metadata leak
"""

from __future__ import annotations

from ragbot.application.services.structured_ref_extractor import (
    extract_structured_ref_from_query,
    extract_structured_refs,
)


# --------------------------------------------------------------------------- #
# Article / Điều extraction                                                   #
# --------------------------------------------------------------------------- #


def test_extracts_article_number_from_chunk_lead() -> None:
    """Chunk starting with 'Điều 3. Nguyên tắc chung' → article_no='3'."""
    out = extract_structured_refs(
        "Điều 3. Nguyên tắc chung\n1. Tổ chức có trách nhiệm bảo đảm ...",
    )
    assert out["article_no"] == "3"


def test_extracts_article_number_case_insensitive() -> None:
    """Upper / lower / title case all match."""
    assert extract_structured_refs("ĐIỀU 8")["article_no"] == "8"
    assert extract_structured_refs("điều 11")["article_no"] == "11"
    assert extract_structured_refs("Điều 1")["article_no"] == "1"


def test_first_article_wins_when_chunk_spans_multiple() -> None:
    """Chunks that include two article headers store the leading one."""
    out = extract_structured_refs(
        "Điều 3. Nguyên tắc chung\n...\nĐiều 4. Nguyên tắc khác\n...",
    )
    assert out["article_no"] == "3"


def test_extracts_three_digit_article_number() -> None:
    """Civil code corpora have Điều 100..999 — pattern must accept them."""
    assert extract_structured_refs("Điều 117. Hiệu lực")["article_no"] == "117"
    assert extract_structured_refs("Điều 999")["article_no"] == "999"


def test_does_not_match_partial_word() -> None:
    """'Điều khiển' or 'Điều ước' must NOT pull a number from later text."""
    # No digit immediately after "Điều" → no match.
    out = extract_structured_refs(
        "Điều khiển từ xa. Tổ chức thực hiện theo 5 bước sau đây.",
    )
    assert "article_no" not in out


# --------------------------------------------------------------------------- #
# Khoản / Mục / Phụ lục / Chương                                              #
# --------------------------------------------------------------------------- #


def test_extracts_clause_number() -> None:
    out = extract_structured_refs("Khoản 3 Điều này quy định ...")
    assert out["clause_no"] == "3"
    # Chunk also contains 'Điều này' (no digit) — article must NOT be set.
    assert "article_no" not in out


def test_extracts_section_number() -> None:
    out = extract_structured_refs("Mục 2. Quyền và nghĩa vụ")
    assert out["section_no"] == "2"


def test_extracts_appendix_letter_and_digit() -> None:
    """Phụ lục can be A / B / 1 / 2."""
    assert extract_structured_refs("Phụ lục A. Biểu mẫu")["appendix_no"] == "A"
    assert extract_structured_refs("Phụ lục 1")["appendix_no"] == "1"


def test_extracts_chapter_roman_numeral() -> None:
    """Chương I / Chương II / Chương IX must all parse."""
    assert extract_structured_refs("Chương I. Quy định chung")["chapter_no"] == "I"
    assert extract_structured_refs("Chương IX")["chapter_no"] == "IX"


def test_extracts_chapter_arabic_digit_fallback() -> None:
    """Some corpora use Arabic digits for chapters — pattern allows both."""
    assert extract_structured_refs("Chương 3")["chapter_no"] == "3"


# --------------------------------------------------------------------------- #
# Negative / empty                                                            #
# --------------------------------------------------------------------------- #


def test_empty_string_returns_empty_dict() -> None:
    assert extract_structured_refs("") == {}


def test_no_match_returns_empty_dict() -> None:
    """Non-legal text leaves metadata empty (no pollution)."""
    out = extract_structured_refs(
        "Quy trình vận hành dịch vụ chăm sóc khách hàng tiêu chuẩn.",
    )
    assert out == {}


def test_english_text_returns_empty_dict() -> None:
    """English chunks (international corpus) → no false matches."""
    out = extract_structured_refs(
        "Article 3 applies to all parties under chapter II.",
    )
    # The extractor is Vietnamese-keyword anchored — English keywords skip.
    assert out == {}


# --------------------------------------------------------------------------- #
# Query-side extractor — same shape, same regex                               #
# --------------------------------------------------------------------------- #


def test_query_side_extractor_matches_ingest_side() -> None:
    """Symmetric tokenisation: user typing 'Điều 3?' yields the same key."""
    query_out = extract_structured_ref_from_query("Điều 3 quy định gì?")
    ingest_out = extract_structured_refs("Điều 3. Nguyên tắc chung")
    assert query_out["article_no"] == ingest_out["article_no"] == "3"


def test_query_with_no_structured_ref_returns_empty() -> None:
    """'Nguyên tắc chung là gì?' has no structured anchor → no filter."""
    assert extract_structured_ref_from_query("Nguyên tắc chung là gì?") == {}


# --------------------------------------------------------------------------- #
# Combined chunk — multiple anchors                                           #
# --------------------------------------------------------------------------- #


def test_chunk_with_chapter_article_clause_all_extracted() -> None:
    """Realistic legal chunk header carries all three anchors."""
    text = (
        "Chương II. An toàn thông tin\n"
        "Điều 7. Trách nhiệm\n"
        "Khoản 1. Tổ chức cung cấp dịch vụ ..."
    )
    out = extract_structured_refs(text)
    assert out["chapter_no"] == "II"
    assert out["article_no"] == "7"
    assert out["clause_no"] == "1"


# --------------------------------------------------------------------------- #
# Domain-neutral guard                                                        #
# --------------------------------------------------------------------------- #


def test_extractor_returns_only_strings() -> None:
    """JSONB persistence requires all values be str (not int)."""
    out = extract_structured_refs(
        "Chương III\nĐiều 42. Quy định\nKhoản 5",
    )
    for value in out.values():
        assert isinstance(value, str), f"non-str value {value!r}"
