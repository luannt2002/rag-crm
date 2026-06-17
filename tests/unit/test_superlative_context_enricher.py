"""Unit tests for SuperlativeContextEnricher.

Domain-neutral tests — no hardcoded brand/product names.
All 12+ tests verify real assertions (no `assert True` / `assert is not None`).
"""
from __future__ import annotations

import pytest

from ragbot.application.services.superlative_context_enricher import (
    RankedItem,
    SUPERLATIVE_PATTERNS,
    SuperlativeContextEnricher,
)
from ragbot.shared.constants import DEFAULT_SUPERLATIVE_TOP_K


@pytest.fixture()
def enricher() -> SuperlativeContextEnricher:
    return SuperlativeContextEnricher()


# ---------------------------------------------------------------------------
# 1. detect_intent — positive cases
# ---------------------------------------------------------------------------

class TestDetectIntent:
    def test_detect_max_price_intent_dat_nhat(self, enricher):
        intent = enricher.detect_intent("Gói nào đắt nhất?")
        assert intent == "max_price"

    def test_detect_max_price_intent_cao_nhat(self, enricher):
        intent = enricher.detect_intent("Giá cao nhất là bao nhiêu?")
        assert intent == "max_price"

    def test_detect_min_price_intent_re_nhat(self, enricher):
        intent = enricher.detect_intent("Loại nào rẻ nhất?")
        assert intent == "min_price"

    def test_detect_min_price_intent_thap_nhat(self, enricher):
        intent = enricher.detect_intent("Gói có giá thấp nhất?")
        assert intent == "min_price"

    def test_detect_longest_duration_lau_nhat(self, enricher):
        intent = enricher.detect_intent("Liệu trình nào lâu nhất?")
        assert intent == "longest_duration"

    def test_detect_shortest_duration_nhanh_nhat(self, enricher):
        intent = enricher.detect_intent("Gói nào nhanh nhất?")
        assert intent == "shortest_duration"

    def test_detect_max_discount_giam_nhieu_nhat(self, enricher):
        intent = enricher.detect_intent("Gói nào được giảm giá nhiều nhất?")
        assert intent == "max_discount"

    def test_detect_max_discount_uu_dai_nhat(self, enricher):
        intent = enricher.detect_intent("Ưu đãi nhất là gì?")
        assert intent == "max_discount"

    def test_detect_max_bonus_tang_nhieu_nhat(self, enricher):
        intent = enricher.detect_intent("Gói nào tặng nhiều nhất?")
        assert intent == "max_bonus"

    def test_no_superlative_returns_none(self, enricher):
        intent = enricher.detect_intent("Gói này có tốt không?")
        assert intent is None

    def test_empty_query_returns_none(self, enricher):
        assert enricher.detect_intent("") is None

    def test_query_case_insensitive(self, enricher):
        # Vietnamese is typically lowercase, but verify that uppercase ASCII doesn't break
        intent = enricher.detect_intent("gói VIP NHẤT")
        assert intent == "max_price"


# ---------------------------------------------------------------------------
# 2. parse_chunks — price / duration / discount / bonus
# ---------------------------------------------------------------------------

class TestParseChunks:
    def test_parse_pricing_pattern_dot_separator(self, enricher):
        chunks = ["Gói Cơ Bản: 500.000đ\nGói Nâng Cao: 1.200.000đ"]
        items = enricher.parse_chunks(chunks)
        names = {i.name for i in items}
        assert "Gói Cơ Bản" in names
        assert "Gói Nâng Cao" in names
        prices = {i.name: i.price for i in items}
        assert prices["Gói Cơ Bản"] == 500000
        assert prices["Gói Nâng Cao"] == 1200000

    def test_parse_pricing_pattern_comma_separator(self, enricher):
        chunks = ["Liệu trình X: 700,000 VND"]
        items = enricher.parse_chunks(chunks)
        assert len(items) == 1
        assert items[0].price == 700000

    def test_parse_dict_chunks(self, enricher):
        chunks = [{"content": "Gói A: 300.000đ", "chunk_id": "abc-123"}]
        items = enricher.parse_chunks(chunks)
        assert len(items) == 1
        assert items[0].name == "Gói A"
        assert items[0].source_chunk_id == "abc-123"
        assert items[0].price == 300000

    def test_parse_duration_pattern(self, enricher):
        # Duration matched requires an existing price item with same name prefix
        chunk = "Gói Thư Giãn: 400.000đ\nGói Thư Giãn 60 phút"
        items = enricher.parse_chunks([chunk])
        assert any(i.duration_minutes is not None for i in items)

    def test_parse_discount_pattern(self, enricher):
        chunk = "Gói Premium: 2.000.000đ\nGiảm 20%"
        items = enricher.parse_chunks([chunk])
        # After parsing price, discount should attach to last item
        assert len(items) >= 1
        assert items[-1].discount_percent == 20

    def test_parse_bonus_pattern(self, enricher):
        chunk = "Gói Vàng: 1.500.000đ\nTặng 3 buổi massage"
        items = enricher.parse_chunks([chunk])
        assert len(items) >= 1
        assert items[-1].bonus_count == 3

    def test_parse_empty_chunks_returns_empty(self, enricher):
        items = enricher.parse_chunks([])
        assert items == []

    def test_parse_no_pattern_returns_empty(self, enricher):
        items = enricher.parse_chunks(["Không có giá nào ở đây"])
        assert items == []

    def test_parse_deduplicates_by_name_keeps_highest_price(self, enricher):
        # Same name appears twice — second with higher price should win
        chunks = [
            "Gói Alpha: 500.000đ",
            "Gói Alpha: 800.000đ",
        ]
        items = enricher.parse_chunks(chunks)
        alpha_items = [i for i in items if i.name == "Gói Alpha"]
        assert len(alpha_items) == 1
        assert alpha_items[0].price == 800000


# ---------------------------------------------------------------------------
# 3. rank_for_intent
# ---------------------------------------------------------------------------

class TestRankForIntent:
    def _make_items(self) -> list[RankedItem]:
        return [
            RankedItem(name="A", price=100000, duration_minutes=30, discount_percent=5),
            RankedItem(name="B", price=500000, duration_minutes=90, discount_percent=20),
            RankedItem(name="C", price=300000, duration_minutes=60, discount_percent=10),
        ]

    def test_rank_max_price_returns_descending(self, enricher):
        items = self._make_items()
        ranked = enricher.rank_for_intent(items, "max_price")
        assert ranked[0].name == "B"
        assert ranked[0].price == 500000
        assert ranked[-1].price <= ranked[0].price

    def test_rank_min_price_returns_ascending(self, enricher):
        items = self._make_items()
        ranked = enricher.rank_for_intent(items, "min_price")
        assert ranked[0].name == "A"
        assert ranked[0].price == 100000

    def test_rank_longest_duration_returns_descending(self, enricher):
        items = self._make_items()
        ranked = enricher.rank_for_intent(items, "longest_duration")
        assert ranked[0].duration_minutes == 90

    def test_rank_shortest_duration_returns_ascending(self, enricher):
        items = self._make_items()
        ranked = enricher.rank_for_intent(items, "shortest_duration")
        assert ranked[0].duration_minutes == 30

    def test_rank_max_discount_returns_descending(self, enricher):
        items = self._make_items()
        ranked = enricher.rank_for_intent(items, "max_discount")
        assert ranked[0].discount_percent == 20

    def test_rank_respects_top_k(self, enricher):
        # Create more items than DEFAULT_SUPERLATIVE_TOP_K
        items = [RankedItem(name=f"Item{i}", price=i * 10000) for i in range(1, 12)]
        ranked = enricher.rank_for_intent(items, "max_price")
        assert len(ranked) == DEFAULT_SUPERLATIVE_TOP_K

    def test_rank_filters_items_missing_dimension(self, enricher):
        # Items without price should be excluded from max_price rank
        items = [
            RankedItem(name="WithPrice", price=100000),
            RankedItem(name="NoPrice", price=None),
        ]
        ranked = enricher.rank_for_intent(items, "max_price")
        names = {i.name for i in ranked}
        assert "WithPrice" in names
        assert "NoPrice" not in names


# ---------------------------------------------------------------------------
# 4. enrich_state
# ---------------------------------------------------------------------------

class TestEnrichState:
    _PRICE_CHUNK = "Gói Đặc Biệt: 2.000.000đ\nGói Tiêu Chuẩn: 500.000đ\nGói Phổ Thông: 200.000đ"

    def test_enrich_state_adds_context_base(self, enricher):
        state: dict = {}
        state = enricher.enrich_state(state, query="Gói nào đắt nhất?", chunks=[self._PRICE_CHUNK])
        assert "context_base" in state
        sup = state["context_base"]["superlative"]
        assert sup["intent"] == "max_price"
        assert len(sup["ranked_items"]) >= 1
        assert sup["ranked_items"][0]["name"] == "Gói Đặc Biệt"
        assert sup["ranked_items"][0]["price"] == 2000000

    def test_enrich_state_does_not_set_answer(self, enricher):
        """Critical: Application layer NEVER sets state['answer']."""
        state: dict = {}
        enricher.enrich_state(state, query="Gói nào đắt nhất?", chunks=[self._PRICE_CHUNK])
        assert "answer" not in state

    def test_enrich_state_no_op_when_no_intent(self, enricher):
        state: dict = {"existing_key": "value"}
        result = enricher.enrich_state(state, query="Gói này tốt không?", chunks=[self._PRICE_CHUNK])
        assert "context_base" not in result
        assert result.get("existing_key") == "value"

    def test_enrich_state_no_op_when_no_items(self, enricher):
        state: dict = {}
        result = enricher.enrich_state(
            state,
            query="Gói nào đắt nhất?",
            chunks=["Không có thông tin giá nào ở đây"],
        )
        assert "context_base" not in result

    def test_enrich_state_no_op_when_no_ranked_for_intent(self, enricher):
        # Items parsed but none have duration — shouldn't populate superlative
        state: dict = {}
        chunks = ["Gói A: 500.000đ"]  # no duration info
        result = enricher.enrich_state(state, query="Liệu trình nào lâu nhất?", chunks=chunks)
        # Items exist but duration=None → rank returns [] → no-op
        assert "context_base" not in result

    def test_enrich_state_preserves_existing_state(self, enricher):
        state = {"retrieved_chunks": ["chunk1"], "query": "test"}
        enricher.enrich_state(state, query="Gói nào đắt nhất?", chunks=[self._PRICE_CHUNK])
        assert state.get("retrieved_chunks") == ["chunk1"]
        assert state.get("query") == "test"

    def test_enrich_state_method_tag(self, enricher):
        state: dict = {}
        enricher.enrich_state(state, query="Gói nào đắt nhất?", chunks=[self._PRICE_CHUNK])
        sup = state["context_base"]["superlative"]
        assert sup["method"] == "application_layer_enrichment"

    def test_enrich_state_ranked_items_schema(self, enricher):
        """Verify ranked_items have expected keys."""
        state: dict = {}
        enricher.enrich_state(state, query="Gói nào đắt nhất?", chunks=[self._PRICE_CHUNK])
        sup = state["context_base"]["superlative"]
        item = sup["ranked_items"][0]
        assert "name" in item
        assert "price" in item
        assert "duration" in item
        assert "discount_pct" in item
        assert "bonus" in item
        assert "source_chunk_id" in item

    def test_enrich_state_min_price_sorted_ascending(self, enricher):
        state: dict = {}
        enricher.enrich_state(state, query="Gói nào rẻ nhất?", chunks=[self._PRICE_CHUNK])
        sup = state["context_base"]["superlative"]
        assert sup["intent"] == "min_price"
        prices = [i["price"] for i in sup["ranked_items"] if i["price"] is not None]
        assert prices == sorted(prices)

    def test_enrich_state_with_dict_chunks(self, enricher):
        """Chunks as list[dict] with content + chunk_id."""
        chunks = [
            {"content": "Gói Platinum: 3.000.000đ", "chunk_id": "c-001"},
            {"content": "Gói Silver: 800.000đ", "chunk_id": "c-002"},
        ]
        state: dict = {}
        enricher.enrich_state(state, query="Gói đắt nhất?", chunks=chunks)
        sup = state["context_base"]["superlative"]
        assert sup["ranked_items"][0]["name"] == "Gói Platinum"
        assert sup["ranked_items"][0]["source_chunk_id"] == "c-001"


# ---------------------------------------------------------------------------
# 5. SUPERLATIVE_PATTERNS completeness
# ---------------------------------------------------------------------------

class TestSuperlativePatterns:
    def test_all_7_intent_families_present(self):
        expected = {
            "max_price", "min_price",
            "longest_duration", "shortest_duration",
            "max_discount", "min_discount",
            "max_bonus",
        }
        assert set(SUPERLATIVE_PATTERNS.keys()) == expected

    def test_each_intent_has_at_least_one_pattern(self):
        for intent, patterns in SUPERLATIVE_PATTERNS.items():
            assert len(patterns) >= 1, f"Intent '{intent}' has no patterns"
