"""[T1-Smartness] Tests for shared.query_range_parser.

Covers:
  - parse_money_vn: VN money shorthand → integer VND
  - parse_range_query: range-filter detection + RangeFilter fields
  - matches_summary_pattern: summary-query signal detection
"""

from __future__ import annotations

import pytest

from ragbot.shared.query_range_parser import (
    RangeFilter,
    matches_summary_pattern,
    parse_code_query,
    parse_list_query,
    parse_money_vn,
    parse_range_query,
)
from ragbot.shared.constants import RANGE_QUERY_MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# parse_money_vn
# ---------------------------------------------------------------------------


def test_parse_money_vn_triệu() -> None:
    assert parse_money_vn("2tr") == 2_000_000


def test_parse_money_vn_k_suffix() -> None:
    assert parse_money_vn("500k") == 500_000


def test_parse_money_vn_full_triệu() -> None:
    assert parse_money_vn("1.5 triệu") == 1_500_000


def test_parse_money_vn_tỷ() -> None:
    result = parse_money_vn("2tỷ")
    assert result == 2_000_000_000


def test_parse_money_vn_plain_integer() -> None:
    assert parse_money_vn("300") == 300


def test_parse_money_vn_comma_decimal() -> None:
    assert parse_money_vn("2,5 triệu") == 2_500_000


def test_parse_money_vn_empty_returns_none() -> None:
    assert parse_money_vn("") is None


def test_parse_money_vn_invalid_returns_none() -> None:
    assert parse_money_vn("abc") is None


# ---------------------------------------------------------------------------
# parse_range_query — "dưới X" (price_max only)
# ---------------------------------------------------------------------------


def test_parse_duoi_x_price_max() -> None:
    result = parse_range_query("dưới 2tr có bao nhiêu dịch vụ")
    assert result is not None
    assert result.price_max == 2_000_000
    assert result.price_min is None


def test_parse_duoi_x_confidence_above_threshold() -> None:
    result = parse_range_query("dưới 2tr")
    assert result is not None
    assert result.confidence >= RANGE_QUERY_MIN_CONFIDENCE


def test_parse_duoi_x_operation_count() -> None:
    result = parse_range_query("dưới 2tr có bao nhiêu")
    assert result is not None
    assert result.operation == "count"


def test_parse_duoi_x_operation_list() -> None:
    result = parse_range_query("liệt kê dịch vụ dưới 500k")
    assert result is not None
    assert result.operation == "list"


# ---------------------------------------------------------------------------
# parse_range_query — "trên X" (price_min only)
# ---------------------------------------------------------------------------


def test_parse_tren_x_price_min() -> None:
    result = parse_range_query("trên 500k")
    assert result is not None
    assert result.price_min == 500_000
    assert result.price_max is None


def test_parse_tren_x_confidence_above_threshold() -> None:
    result = parse_range_query("trên 500k")
    assert result is not None
    assert result.confidence >= RANGE_QUERY_MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# parse_range_query — "từ X đến Y" (both bounds)
# ---------------------------------------------------------------------------


def test_parse_tu_x_den_y_both_bounds() -> None:
    result = parse_range_query("từ 500k đến 2tr")
    assert result is not None
    assert result.price_min == 500_000
    assert result.price_max == 2_000_000


def test_parse_tu_x_den_y_swaps_if_reversed() -> None:
    result = parse_range_query("từ 2tr đến 500k")
    assert result is not None
    # Parser normalises: min < max regardless of order in query
    assert result.price_min == 500_000
    assert result.price_max == 2_000_000


def test_parse_tu_x_den_y_confidence_high() -> None:
    result = parse_range_query("từ 500k đến 2tr")
    assert result is not None
    assert result.confidence >= 0.85


# ---------------------------------------------------------------------------
# parse_range_query — "khoảng X" (fuzzy ±10%)
# ---------------------------------------------------------------------------


def test_parse_khoang_x_fuzzy_range() -> None:
    result = parse_range_query("khoảng 1 triệu")
    assert result is not None
    assert result.price_min == 900_000
    assert result.price_max == 1_100_000


def test_parse_khoang_x_confidence_above_threshold() -> None:
    result = parse_range_query("khoảng 1 triệu")
    assert result is not None
    assert result.confidence >= RANGE_QUERY_MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# parse_range_query — operation detection
# ---------------------------------------------------------------------------


def test_parse_count_intent_co_bao_nhieu() -> None:
    result = parse_range_query("dưới 2tr có bao nhiêu loại")
    assert result is not None
    assert result.operation == "count"


def test_parse_list_intent_liet_ke() -> None:
    result = parse_range_query("liệt kê tất cả dịch vụ dưới 500k")
    assert result is not None
    assert result.operation == "list"


def test_parse_filter_intent_default() -> None:
    result = parse_range_query("dưới 2tr")
    assert result is not None
    assert result.operation == "filter"


# ---------------------------------------------------------------------------
# parse_range_query — None when no range detected
# ---------------------------------------------------------------------------


def test_parse_no_range_returns_none() -> None:
    result = parse_range_query("sản phẩm nào tốt nhất")
    assert result is None


def test_parse_empty_query_returns_none() -> None:
    result = parse_range_query("")
    assert result is None


def test_parse_none_returns_none() -> None:
    result = parse_range_query(None)  # type: ignore[arg-type]
    assert result is None


def test_parse_invalid_no_money_returns_none() -> None:
    result = parse_range_query("dưới này kia nọ")
    assert result is None


def test_parse_non_price_query_returns_none() -> None:
    # Greeting / OOS should not be parsed as range
    result = parse_range_query("xin chào bạn khỏe không")
    assert result is None


# ---------------------------------------------------------------------------
# parse_range_query — RangeFilter is frozen / hashable
# ---------------------------------------------------------------------------


def test_range_filter_is_frozen() -> None:
    result = parse_range_query("dưới 2tr")
    assert result is not None
    with pytest.raises(AttributeError):
        result.price_max = 9999  # type: ignore[misc]


def test_range_filter_equality() -> None:
    a = RangeFilter(price_min=None, price_max=2_000_000, price_column="any",
                    operation="count", confidence=0.85)
    b = RangeFilter(price_min=None, price_max=2_000_000, price_column="any",
                    operation="count", confidence=0.85)
    assert a == b


# ---------------------------------------------------------------------------
# matches_summary_pattern
# ---------------------------------------------------------------------------


def test_matches_summary_tom_tat() -> None:
    assert matches_summary_pattern("tóm tắt các dịch vụ") is True


def test_matches_summary_tong_quan() -> None:
    assert matches_summary_pattern("tổng quan về bảng giá") is True


def test_matches_summary_tat_ca() -> None:
    assert matches_summary_pattern("tất cả dịch vụ là gì") is True


def test_matches_summary_toan_bo() -> None:
    assert matches_summary_pattern("toàn bộ danh mục") is True


def test_matches_summary_overview_en() -> None:
    assert matches_summary_pattern("give me an overview") is True


def test_not_summary_factoid() -> None:
    assert matches_summary_pattern("giá dịch vụ A là bao nhiêu") is False


def test_not_summary_empty() -> None:
    assert matches_summary_pattern("") is False


def test_not_summary_none() -> None:
    assert matches_summary_pattern(None) is False  # type: ignore[arg-type]


# ── Regression: document-number / date must NOT parse as a price range ──────
# Forensic 2026-06-05: "Thông tư 09/2020" ascii-folds "tư"→"tu" = range token
# "từ", and "09" parsed as price≥9 → false-routed legal queries to the stats
# path, bypassing hybrid retrieval (thong-tu Điều-56 hiệu lực miss).

def test_docnum_slash_year_not_price() -> None:
    assert parse_range_query(
        "Thông tư 09/2020 có hiệu lực thi hành từ ngày nào và thay thế Thông tư nào?"
    ) is None


def test_docnum_replacement_not_price() -> None:
    assert parse_range_query("Thông tư 18/2018 thay thế gì?") is None


def test_bare_small_number_no_unit_not_price() -> None:
    # A unit-less single/double-digit number is a doc/article number, not a price.
    assert parse_range_query("Điều 9 quy định gì") is None


def test_real_price_below_still_parses() -> None:
    rf = parse_range_query("dịch vụ nào dưới 800 nghìn")
    assert rf is not None and rf.price_max == 800_000


def test_real_price_above_still_parses() -> None:
    rf = parse_range_query("combo trên 1 triệu có gì")
    assert rf is not None and rf.price_min == 1_000_000


def test_real_price_k_suffix_still_parses() -> None:
    rf = parse_range_query("dịch vụ dưới 500k")
    assert rf is not None and rf.price_max == 500_000


# ---------------------------------------------------------------------------
# parse_range_query — superlative (đắt nhất / rẻ nhất → ORDER BY price)
# ---------------------------------------------------------------------------


def test_parse_superlative_dat_nhat_is_max() -> None:
    result = parse_range_query("dịch vụ nào đắt nhất")
    assert result is not None
    assert result.operation == "max"
    assert result.price_min is None and result.price_max is None
    assert result.confidence >= RANGE_QUERY_MIN_CONFIDENCE


def test_parse_superlative_re_nhat_is_min() -> None:
    result = parse_range_query("dịch vụ rẻ nhất là gì")
    assert result is not None
    assert result.operation == "min"


def test_parse_superlative_cao_nhat_is_max() -> None:
    result = parse_range_query("giá cao nhất bao nhiêu")
    assert result is not None
    assert result.operation == "max"


def test_parse_superlative_cheapest_en_is_min() -> None:
    result = parse_range_query("which service is the cheapest")
    assert result is not None
    assert result.operation == "min"


def test_parse_superlative_most_expensive_en_is_max() -> None:
    result = parse_range_query("the most expensive package")
    assert result is not None
    assert result.operation == "max"


def test_superlative_does_not_fire_on_plain_factoid() -> None:
    # No superlative + no range → None (falls back to vector retrieve).
    assert parse_range_query("trị mụn giá bao nhiêu") is None


def test_range_takes_priority_over_superlative() -> None:
    # An explicit range bound is more specific than a bare superlative.
    result = parse_range_query("dịch vụ rẻ nhất dưới 500k")
    assert result is not None
    assert result.price_max == 500_000  # range wins, not operation=min


# ---------------------------------------------------------------------------
# parse_code_query — product/spec code → structured name lookup
# ---------------------------------------------------------------------------


def test_parse_code_query_extracts_spec_code() -> None:
    """A query carrying a spec code routes to the keyword name lookup."""
    result = parse_code_query("lốp 195/65R15 còn hàng không?")
    assert result is not None
    assert result.operation == "keyword"
    assert result.keyword == "195/65R15"
    assert result.confidence >= RANGE_QUERY_MIN_CONFIDENCE


def test_parse_code_query_price_phrasing() -> None:
    result = parse_code_query("giá lốp 195/65R15")
    assert result is not None
    assert result.keyword == "195/65R15"


def test_code_query_wins_over_polluted_list_keyword() -> None:
    """When a price factoid splits 'giá … bao nhiêu' around a spec code
    ('giá lốp 275/55R20 bao nhiêu'), the list parser captures a POLLUTED
    keyword while the code parser extracts the clean code. The retrieve gate
    must prefer the code (more specific) — assert both so the ordering
    invariant is regression-guarded: code is clean, list is polluted.
    """
    q = "giá lốp 275/55R20 bao nhiêu?"
    code = parse_code_query(q)
    lst = parse_list_query(q)
    assert code is not None and code.keyword == "275/55R20"
    # The list parser DOES fire here with a non-matching phrase — proving why
    # the code route must be consulted first (else this masks the code).
    assert lst is None or "275/55R20" not in (lst.keyword or "") or "giá" in (
        lst.keyword or ""
    )


def test_parse_code_query_hyphen_code() -> None:
    result = parse_code_query("khi nào về hàng 2-R17")
    assert result is not None
    assert result.keyword == "2-R17"


def test_parse_code_query_no_code_returns_none() -> None:
    """A brand/keyword query with no code token → None (HALLU trap path)."""
    assert parse_code_query("có lốp Michelin không?") is None
    assert parse_code_query("dịch vụ nào đắt nhất") is None


def test_parse_code_query_rejects_date_docnumber() -> None:
    """A digits-only token (date / doc id / phone) is NOT a product code.

    Guards against hijacking a legal "Thông tư 09/2020" or a phone number
    away from its proper retrieval path.
    """
    assert parse_code_query("Thông tư 09/2020 quy định gì") is None
    assert parse_code_query("Nghị định 16/2017") is None
    assert parse_code_query("gọi 090-123-4567") is None


def test_parse_code_query_empty_returns_none() -> None:
    assert parse_code_query("") is None
    assert parse_code_query("   ") is None


# ---------------------------------------------------------------------------
# parse_list_query — keyword extraction strips connective fillers
#
# Regression: an existence/list query with a filler word between "dịch vụ" and
# the real keyword ("có dịch vụ VÀO/VỀ/NÀO da chết không") left the filler in
# the extracted keyword ("vào về da chết"), so the structured ILIKE matched
# nothing and the route silently fell back to vector (top-1 chunk) → only ONE
# service surfaced instead of the full list.
# ---------------------------------------------------------------------------


def test_parse_list_strips_filler_vao_ve() -> None:
    """'có dịch vụ vào về da chết không' → keyword 'da chết' (fillers removed)."""
    result = parse_list_query("có dịch vụ vào về da chết không?")
    assert result is not None
    assert result.operation == "keyword"
    assert result.keyword == "da chết"


def test_parse_list_strips_filler_nao_ve() -> None:
    result = parse_list_query("có dịch vụ nào về da chết không?")
    assert result is not None
    assert result.keyword == "da chết"


def test_parse_list_existence_question_routes_keyword() -> None:
    """A yes/no existence question still routes to the keyword list so EVERY
    matching service surfaces, not just the top-1 vector chunk."""
    for q in (
        "có dịch vụ về da chết không?",
        "có dịch vụ tẩy da chết không?",
    ):
        result = parse_list_query(q)
        assert result is not None and result.operation == "keyword", q


def test_parse_list_keyword_unchanged_no_filler() -> None:
    """No-filler queries keep their existing keyword (no over-stripping)."""
    assert parse_list_query("liệt kê dịch vụ tẩy da chết").keyword == "tẩy da chết"
    assert parse_list_query("có bao nhiêu dịch vụ massage").keyword == "massage"
    assert parse_list_query("tư vấn về da").keyword == "da"


def test_parse_list_filler_preserves_multiword_service() -> None:
    """Stripping the connective must not eat a real service token. 'ủ trắng'
    survives ('về' is a filler, 'trắng' is content)."""
    result = parse_list_query("có dịch vụ về ủ trắng body không?")
    assert result is not None
    assert "trắng" in (result.keyword or "")


def test_parse_list_strips_shop_and_help_fillers() -> None:
    """q02 (xe): 'Shop có những loại lốp nào, liệt kê giúp mình' left 'Shop' +
    'giúp' in the keyword ('Shop lốp , giúp') → ILIKE matched nothing → list_all
    fallback (oldest rows) missed the answer entity (CITYTRAXX). 'shop' (store
    colloquialism) + 'giúp' (help verb) are domain-neutral conversational
    fillers; after stripping, the keyword is the clean category noun 'lốp'."""
    rf = parse_list_query("Shop có những loại lốp nào, liệt kê giúp mình")
    assert rf is not None
    assert rf.keyword.strip() == "lốp", f"polluted keyword: {rf.keyword!r}"
