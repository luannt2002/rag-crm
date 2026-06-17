"""Language-gate tests for SuperlativeContextEnricher.

Multi-industry / multi-language safety: the platform ships with Vietnamese
and English regex packs. Bots whose ``language`` is outside
``SUPERLATIVE_SUPPORTED_LANGUAGES`` (e.g. ``zh``, ``jp``) MUST get a no-op
enricher so a Mandarin query is not mis-classified by Vietnamese phrases.
"""
from __future__ import annotations

import pytest

from ragbot.application.services.superlative_context_enricher import (
    SuperlativeContextEnricher,
    get_enricher_for_language,
)


# ---------------------------------------------------------------------------
# 1. Vietnamese language gate (current behaviour preserved)
# ---------------------------------------------------------------------------

class TestVietnameseGate:
    def test_vi_detects_max_price_dat_nhat(self) -> None:
        enricher = SuperlativeContextEnricher(language="vi")
        assert enricher.detect_intent("Gói nào đắt nhất?") == "max_price"

    def test_vi_detects_min_price_re_nhat(self) -> None:
        enricher = SuperlativeContextEnricher(language="vi")
        assert enricher.detect_intent("Loại nào rẻ nhất?") == "min_price"

    def test_vi_detects_longest_duration_lau_nhat(self) -> None:
        enricher = SuperlativeContextEnricher(language="vi")
        assert enricher.detect_intent("Liệu trình nào lâu nhất?") == "longest_duration"

    def test_vi_default_when_no_language_arg(self) -> None:
        # Backward compat — existing callers still get VN behaviour.
        enricher = SuperlativeContextEnricher()
        assert enricher.detect_intent("Gói nào đắt nhất?") == "max_price"


# ---------------------------------------------------------------------------
# 2. English language gate
# ---------------------------------------------------------------------------

class TestEnglishGate:
    def test_en_detects_max_price_most_expensive(self) -> None:
        enricher = SuperlativeContextEnricher(language="en")
        assert enricher.detect_intent("Which one is the most expensive?") == "max_price"

    def test_en_detects_min_price_cheapest(self) -> None:
        enricher = SuperlativeContextEnricher(language="en")
        assert enricher.detect_intent("What's the cheapest plan?") == "min_price"

    def test_en_detects_longest_duration(self) -> None:
        enricher = SuperlativeContextEnricher(language="en")
        assert enricher.detect_intent("Which has the longest term?") == "longest_duration"

    def test_en_detects_max_discount_biggest_discount(self) -> None:
        enricher = SuperlativeContextEnricher(language="en")
        assert enricher.detect_intent("Which has the biggest discount?") == "max_discount"

    def test_en_does_not_detect_vietnamese_phrase(self) -> None:
        # EN bot should NOT match Vietnamese regex.
        enricher = SuperlativeContextEnricher(language="en")
        assert enricher.detect_intent("Gói nào đắt nhất?") is None


# ---------------------------------------------------------------------------
# 3. Unsupported language → no-op (fail-soft)
# ---------------------------------------------------------------------------

class TestUnsupportedLanguageNoOp:
    @pytest.mark.parametrize("lang", ["zh", "jp", "ko", "ar", "th"])
    def test_unsupported_language_returns_none(self, lang: str) -> None:
        enricher = SuperlativeContextEnricher(language=lang)
        # Even with VN superlative phrases the unsupported pack is empty.
        assert enricher.detect_intent("Gói nào đắt nhất?") is None
        # Even with EN phrases.
        assert enricher.detect_intent("Most expensive plan?") is None

    def test_unsupported_language_enrich_state_no_op(self) -> None:
        enricher = SuperlativeContextEnricher(language="zh")
        state: dict = {}
        chunks = ["Premium plan: 1.500.000đ\nBasic plan: 500.000đ"]
        result = enricher.enrich_state(state, query="最贵的套餐?", chunks=chunks)
        # No context_base.superlative should be set.
        assert "context_base" not in result or "superlative" not in result.get("context_base", {})

    def test_unsupported_language_patterns_dict_empty(self) -> None:
        enricher = SuperlativeContextEnricher(language="zh")
        assert enricher.patterns == {}


# ---------------------------------------------------------------------------
# 4. Factory caching
# ---------------------------------------------------------------------------

class TestFactoryCache:
    def test_factory_returns_cached_instance_per_language(self) -> None:
        a = get_enricher_for_language("vi")
        b = get_enricher_for_language("vi")
        assert a is b

    def test_factory_different_instances_for_different_languages(self) -> None:
        a = get_enricher_for_language("vi")
        b = get_enricher_for_language("en")
        assert a is not b
        assert a.language == "vi"
        assert b.language == "en"

    def test_factory_unsupported_language_returns_no_op_instance(self) -> None:
        enricher = get_enricher_for_language("ko")
        assert enricher.patterns == {}
        assert enricher.detect_intent("Gói nào đắt nhất?") is None


# ---------------------------------------------------------------------------
# 5. End-to-end enrich_state with EN bot + EN chunks
# ---------------------------------------------------------------------------

def test_en_enrich_state_attaches_language_tag() -> None:
    enricher = SuperlativeContextEnricher(language="en")
    state: dict = {}
    chunks = [
        {"content": "Premium plan: 3.000.000đ", "chunk_id": "c-001"},
        {"content": "Basic plan: 800.000đ", "chunk_id": "c-002"},
    ]
    result = enricher.enrich_state(state, query="What's the most expensive?", chunks=chunks)
    sup = result["context_base"]["superlative"]
    assert sup["intent"] == "max_price"
    assert sup["language"] == "en"
    # Ranked items should be sorted by price descending.
    prices = [i["price"] for i in sup["ranked_items"] if i["price"] is not None]
    assert prices == sorted(prices, reverse=True)
