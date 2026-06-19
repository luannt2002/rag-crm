"""BUG-1 CONFLATE fix: price-of-entity questions must route to the structured
name lookup (operation="keyword"), not the vector path where a multi-entity
chunk lets the LLM attribute the wrong entity's price.

Routing-layer unit tests — no DB / no LLM. Verifies the parser produces the
right RangeFilter shape; the runtime keyword lookup is covered separately.
"""
from __future__ import annotations

import pytest

from ragbot.shared.query_range_parser import (
    parse_price_of_entity_query,
    parse_range_query,
)


@pytest.mark.parametrize(
    "query, kw_substr",
    [
        ("tẩy da chết giá bao nhiêu", "tẩy da chết"),
        ("massage body bao nhiêu tiền", "massage body"),
        ("triệt lông nách giá bao nhiêu ạ?", "triệt lông nách"),
        ("lốp 195/65R15 giá bao nhiêu", "195"),
        ("how much is the deluxe facial", "deluxe facial"),
    ],
)
def test_price_of_entity_routes_to_keyword(query: str, kw_substr: str) -> None:
    rf = parse_price_of_entity_query(query)
    assert rf is not None, f"expected keyword route for {query!r}"
    assert rf.operation == "keyword"
    assert rf.keyword is not None
    folded = rf.keyword.lower()
    # the residual keyword must retain the entity (price-ask words stripped)
    assert kw_substr.split()[0].lower() in folded, (query, rf.keyword)
    assert "bao nhiêu" not in folded and "giá" not in folded


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "spa có những dịch vụ gì",          # list, no price-ask
        "tư vấn về da",                     # category, no price-ask
        "xin chào",                          # greeting
        "dịch vụ nào dưới 500k",            # numeric range → parse_range_query owns it
        "dịch vụ đắt nhất là gì",          # superlative → parse_range_query owns it
        "Điều 4 quy định giá bao nhiêu",   # legal clause anchor, not a catalog price
        "Khoản 2 giá trị pháp lý bao nhiêu",
    ],
)
def test_non_price_of_entity_returns_none(query: str) -> None:
    assert parse_price_of_entity_query(query) is None


def test_range_query_precedence_preserved() -> None:
    # A numeric-range price question must still be owned by parse_range_query,
    # and must NOT be hijacked by the price-of-entity keyword route.
    q = "dịch vụ dưới 500k giá bao nhiêu"
    assert parse_range_query(q) is not None
    assert parse_price_of_entity_query(q) is None
