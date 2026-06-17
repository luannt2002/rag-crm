"""Pin + behaviour tests for per-language structural markers (multi-lang hardening).

The VN legal-structure markers used by ``vn_structural`` were lifted out of
inline literals into ``DEFAULT_STRUCTURAL_MARKERS_BY_LANG`` so non-VN bots
(EN / JP) get their own marker / aggregation-keyword set instead of silently
inheriting the Vietnamese ones. These tests:

1. PIN the ``vi`` config values byte-identical to the prior hardcoded set,
   and PIN the rebuilt VN regexes byte-identical to the original patterns —
   so VN behaviour is provably unchanged.
2. ASSERT the new EN aggregation-keyword detection fires (the whole point of
   the hardening) and that JP / unknown languages resolve to an empty set
   (no VN leak).
"""
from __future__ import annotations

from ragbot.orchestration.nodes.query_complexity import has_aggregation_keyword
from ragbot.shared.chunking import (
    detect_vn_structural_anchor,
    normalize_vn_section_numerals,
)
from ragbot.shared.chunking import vn_structural as v
from ragbot.shared.constants import (
    DEFAULT_AGGREGATION_KEYWORDS_BY_LANG,
    DEFAULT_STRUCTURAL_MARKERS_BY_LANG,
    DEFAULT_STRUCTURAL_MARKERS_LANG,
)


# --- VN byte-identity pins --------------------------------------------------

def test_vi_markers_byte_identical_to_prior_hardcoded() -> None:
    """The vi marker tuple MUST equal the prior inline literals exactly."""
    assert DEFAULT_STRUCTURAL_MARKERS_BY_LANG["vi"] == (
        "Chương",
        "Phần",
        "Mục",
        "Điều",
    )
    assert DEFAULT_STRUCTURAL_MARKERS_LANG == "vi"


def test_vn_regexes_byte_identical_after_refactor() -> None:
    """Rebuilt regex pattern strings == the original hardcoded patterns."""
    assert v._VN_CHAPTER_RE.pattern == (
        r"^(Chương|Phần)\s+([IVXLCDM]+|[0-9]+)(\s*[\.:\-].*)?$"
    )
    assert v._VN_SECTION_RE.pattern == (
        r"^Mục\s+([IVXLCDM]+|[0-9]+)(\s*[\.:\-].*)?$"
    )
    assert v._VN_ARTICLE_RE.pattern == r"^Điều\s+([0-9]+)\s*[\.:].*$"
    assert v._VN_HEADING_DETECT_RE.pattern == (
        r"^(Chương|Phần|Mục|Điều)\s+([IVXLCDM0-9]+)"
    )
    assert v._VN_SECTION_NORMALIZE_RE.pattern == (
        r"\b(Chương|Phần|Mục)\s+([IVXLCDM]+|[0-9]+)\b"
    )
    assert v._VN_STRUCTURAL_QUERY_DETECT_RE.pattern == (
        r"\b(Chương|Phần|Mục|Điều)\s+([0-9]+|[IVXLCDM]+)\b"
    )


def test_vn_canonical_map_unchanged() -> None:
    assert v._VN_PREFIX_CANONICAL == {
        "chương": "Chương",
        "phần": "Phần",
        "mục": "Mục",
        "điều": "Điều",
    }


def test_vn_anchor_behaviour_intact() -> None:
    assert detect_vn_structural_anchor("Điều 55") == ("Điều", "55")
    assert normalize_vn_section_numerals("Chương III") == "Chương 3"


# --- EN / JP new behaviour --------------------------------------------------

def test_en_aggregation_keyword_detection() -> None:
    """An EN bot now gets aggregation detection it previously lacked."""
    assert has_aggregation_keyword("list all services", lang="en") is True
    assert has_aggregation_keyword("how many plans are there", lang="en") is True
    assert has_aggregation_keyword("compare the two tiers", lang="en") is True
    assert has_aggregation_keyword("what is the price", lang="en") is False


def test_vi_aggregation_keyword_detection_default() -> None:
    assert has_aggregation_keyword("liệt kê tất cả dịch vụ") is True
    assert has_aggregation_keyword("giá triệt lông") is False


def test_jp_and_unknown_lang_no_vn_leak() -> None:
    """JP placeholder + unknown languages resolve empty — never VN literals."""
    assert DEFAULT_AGGREGATION_KEYWORDS_BY_LANG["ja"] == ()
    assert DEFAULT_STRUCTURAL_MARKERS_BY_LANG["ja"] == ()
    # An English aggregation phrase must NOT match under an unknown language.
    assert has_aggregation_keyword("list all", lang="de") is False
    assert has_aggregation_keyword("全部リスト", lang="ja") is False


def test_en_markers_present_for_chunker() -> None:
    """EN structural markers exist so an EN corpus can promote hierarchy."""
    en = DEFAULT_STRUCTURAL_MARKERS_BY_LANG["en"]
    assert "Chapter" in en
    assert "Section" in en
    assert "Article" in en
