"""Price ceiling — stats extraction must reject dates/timestamps as prices.

Lever #1 (RAG scorecard): xe had 2 rows with price_primary=2025122435548 (a
Google-Sheet serial leaking into a price column). parse_money_vn had a floor but no
ceiling. With DEFAULT_PRICE_MAX_VND, the corrupted value is rejected while every real
price still parses.
"""
from __future__ import annotations

from ragbot.shared.constants import DEFAULT_PRICE_MAX_VND, DEFAULT_PRICE_MIN_VND
from ragbot.shared.document_stats import parse_money_vn as ingest_parse
from ragbot.shared.number_format import parse_money_vn


def test_date_serial_rejected_as_price() -> None:
    # the exact corrupted value found in xe stats_index
    assert parse_money_vn("2025122435548", min_value=DEFAULT_PRICE_MIN_VND,
                          max_value=DEFAULT_PRICE_MAX_VND) is None
    # the ingest wrapper applies the ceiling by default
    assert ingest_parse("2025122435548") is None


def test_real_prices_still_parse() -> None:
    # representative real prices from the 3 corpora — all must survive the ceiling
    assert ingest_parse("1.044.000") == 1_044_000   # xe tire
    assert ingest_parse("810.000") == 810_000       # xe tire
    assert ingest_parse("2.499.000") == 2_499_000   # spa triệt lông toàn thân
    assert ingest_parse("60.000") == 60_000         # spa gội đầu
    assert ingest_parse("1.5tr") == 1_500_000       # VN compound


def test_ceiling_boundary() -> None:
    assert ingest_parse(str(DEFAULT_PRICE_MAX_VND)) == DEFAULT_PRICE_MAX_VND  # exactly at ceiling = valid
    assert parse_money_vn(str(DEFAULT_PRICE_MAX_VND + 1), max_value=DEFAULT_PRICE_MAX_VND) is None


def test_no_ceiling_when_max_none_backward_compat() -> None:
    # query side passes no ceiling — behaviour unchanged
    assert parse_money_vn("2025122435548") == 2025122435548
