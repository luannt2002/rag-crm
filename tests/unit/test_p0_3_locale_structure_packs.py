"""P0-3 locale-structure packs — multilang structure word-lists (EVOLVE, byte-id VN).

Proves the four spec contracts with real behavioural assertions:

1. A STRUCTURAL dotted-leader TOC (no literal "table of contents" marker, any
   language) sets ``has_toc`` True at ALL three sites (analyze_document,
   analyze_document_blocks, RuleBasedDocumentProfileAnalyzer).
2. The shipped en/ja structural-marker sets are reachable per-call via the new
   ``resolve_struct_markers(lang)`` helper.
3. Japanese is detected by Unicode SCRIPT range (kana), not the VN-vs-auto
   diacritic binary.
4. VN happy-path is BYTE-IDENTICAL: analyze_document() output +
   parse_table_chunks() entities unchanged; unknown locale = shape-only
   (empty), no Vietnamese leak.
"""
from __future__ import annotations

from dataclasses import dataclass

from ragbot.infrastructure.doc_profile.rule_based_doc_profile import (
    RuleBasedDocumentProfileAnalyzer,
    _detect_language,
)
from ragbot.shared.chunking import (
    analyze_document,
    analyze_document_blocks,
    resolve_struct_markers,
)
from ragbot.shared.document_stats import (
    _clause_opener_first,
    _discourse_openers,
    _is_discourse_opener,
    parse_table_chunks,
)


# --- 1. Structural dotted-leader TOC detection (any language) ----------------

_EN_TOC_DOC = (
    "COMPANY HANDBOOK\n"
    "\n"
    "Introduction ............ 3\n"
    "Chapter 1: Overview . . . . . . 12\n"
    "Appendix A ...... 45\n"
)


def test_english_dotted_leader_toc_sets_has_toc_analyze_document() -> None:
    """An English TOC page with NO 'table of contents' literal is detected by the
    structural dot-leader shape alone."""
    profile = analyze_document(_EN_TOC_DOC)
    assert profile["has_toc"] is True


def test_english_dotted_leader_toc_sets_has_toc_rule_based() -> None:
    analyzer = RuleBasedDocumentProfileAnalyzer()
    profile = analyzer.analyze(_EN_TOC_DOC)
    assert profile.has_toc is True


def test_english_dotted_leader_toc_sets_has_toc_block_path() -> None:
    @dataclass
    class _Block:
        type: str
        content: str

    blocks = [
        _Block("TEXT", "Introduction ............ 3"),
        _Block("TEXT", "Appendix A ...... 45"),
    ]
    profile = analyze_document_blocks(blocks)
    assert profile["has_toc"] is True


def test_dotted_leader_no_false_positive_on_prose_and_price() -> None:
    """Prose, dotted-thousands prices, version strings and URLs must NOT flip
    has_toc — the structural detector is shape-strict."""
    doc = (
        "This is a normal sentence about the product.\n"
        "The unit price is 1.234.000 VND.\n"
        "Build version 1.2.3 released.\n"
        "Visit www.example.com 2024 for details.\n"
    )
    assert analyze_document(doc)["has_toc"] is False
    assert RuleBasedDocumentProfileAnalyzer().analyze(doc).has_toc is False


# --- 2. en / ja structural markers reachable per-call ------------------------

def test_en_struct_markers_reachable() -> None:
    en = resolve_struct_markers("en")
    assert "Chapter" in en
    assert "Section" in en
    assert "Article" in en
    # NOT the Vietnamese set.
    assert "Điều" not in en


def test_ja_struct_markers_reachable_and_empty_placeholder() -> None:
    # ja is a curated-empty placeholder — reachable, never the VN literals.
    assert resolve_struct_markers("ja") == ()


def test_resolve_default_is_vi_and_unknown_is_empty() -> None:
    assert resolve_struct_markers() == ("Chương", "Phần", "Mục", "Điều")
    assert resolve_struct_markers("de") == ()  # unknown locale → no VN leak


# --- 3. Japanese detected by script range ------------------------------------

def test_japanese_detected_by_script_range() -> None:
    """Kana text classifies 'ja' via the Unicode script-range table — NOT the
    VN-vs-auto binary (it carries zero VN diacritics)."""
    ja_text = "これはテストの文書です。" * 5  # plenty of kana, no VN diacritics
    assert _detect_language(ja_text) == "ja"


def test_vietnamese_still_detected_by_diacritic_ratio() -> None:
    """VN text has no script-range hit, so it falls through to the unchanged
    diacritic-ratio path → 'vi' (byte-identical behaviour)."""
    vi_text = (
        "Điều khoản thi hành áp dụng cho mọi tổ chức tín dụng "
        "hoạt động trên lãnh thổ Việt Nam theo quy định hiện hành."
    )
    assert _detect_language(vi_text) == "vi"


def test_english_falls_back_to_auto() -> None:
    """Plain English (no VN diacritics, no kana) → 'auto' fallback, unchanged."""
    en_text = (
        "This English handbook describes the company policy in plain prose "
        "with enough alphabetic characters to pass the minimum length gate."
    )
    assert _detect_language(en_text) == "auto"


# --- 4. VN byte-identical snapshots + unknown-locale shape-only --------------

_VN_LEGAL_DOC = (
    "Chương I\n"
    "Quy định chung\n"
    "Điều 1. Phạm vi điều chỉnh của văn bản này.\n"
    "Điều 2. Đối tượng áp dụng gồm các tổ chức tín dụng.\n"
    "Mục 1\n"
    "Điều 3. Giải thích từ ngữ trong văn bản.\n"
)


def test_vn_analyze_document_has_toc_unchanged_false() -> None:
    """VN doc (no dot-leader, no literal TOC marker) → has_toc stays False: the
    new dotted-leader OR-branch is inert here (byte-identical)."""
    assert analyze_document(_VN_LEGAL_DOC)["has_toc"] is False


def test_vn_analyze_document_vn_markers_counted() -> None:
    """The VN hierarchical-marker count is unchanged by the P0-3 edit (the marker
    detection regex is byte-identical via the default-vi resolver)."""
    profile = analyze_document(_VN_LEGAL_DOC)
    # Chương I + 3×Điều + Mục 1 = 5 plain-text hierarchy markers.
    assert profile["vn_hierarchical_markers"] == 5


def test_vn_parse_table_chunks_byte_identical_f1_untouched() -> None:
    """The F1 structural-header (col_N) path is unchanged: a VN pipe table with a
    | --- | separator binds to its real labels, zero col_N, prices intact."""
    content = (
        "| Sản phẩm | Giá |\n"
        "| --- | --- |\n"
        "| Áo thun | 199000 |\n"
        "| Quần jean | 350000 |\n"
    )
    entities = parse_table_chunks([{"content": content}])
    assert {e.name for e in entities} == {"Áo thun", "Quần jean"}
    assert {e.price_primary for e in entities} == {199000, 350000}
    keys = {k for e in entities for k in e.attributes}
    assert not any(k.lower().startswith("col_") for k in keys), keys


def test_vn_discourse_openers_byte_identical() -> None:
    """Default-locale (vi) opener sets equal the prior hardcoded frozensets."""
    assert _discourse_openers() == frozenset(
        {"hiện tại", "hiện nay", "bây giờ", "tuy nhiên"}
    )
    assert _clause_opener_first() == frozenset(
        {"khi", "nếu", "vì", "tuy", "do", "bởi"}
    )
    # Behaviour preserved: a VN discourse opener still rejected as a row name.
    assert _is_discourse_opener("Hiện tại") is True
    assert _is_discourse_opener("Khi đến với chúng tôi") is True
    assert _is_discourse_opener("Áo thun nam") is False


def test_unknown_locale_opener_sets_empty_no_vn_leak() -> None:
    """An unknown locale resolves to empty opener sets — never the VN literals."""
    assert _discourse_openers("de") == frozenset()
    assert _clause_opener_first("de") == frozenset()
    # Under an English locale the VN word 'khi' is NOT a clause opener.
    assert _is_discourse_opener("khi đó", lang="en") is False
    # …but an English clause opener IS detected under the en locale.
    assert _is_discourse_opener("when you arrive", lang="en") is True


def test_en_opener_sets_present() -> None:
    en_disc = _discourse_openers("en")
    en_clause = _clause_opener_first("en")
    assert "currently" in en_disc
    assert "when" in en_clause
    assert "if" in en_clause
