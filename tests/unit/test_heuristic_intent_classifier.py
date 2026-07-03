"""[T2-CostPerf] Unit tests for heuristic_intent_classifier Layer-1.

Covers:
- Per-intent class classification (greeting, chitchat, aggregation, multi_hop, comparison)
- No-match / uncertain → None intent with 0.0 confidence
- Confidence levels consistent with anchored vs mid-string patterns
- Domain query correctly falls through (factoid has no pattern)
- Empty / whitespace-only queries degrade gracefully
- HALLU=0 guard: confidence below threshold must NOT produce a usable intent
"""

from __future__ import annotations

import pytest

from ragbot.application.services.heuristic_intent_classifier import (
    HeuristicResult,
    classify_heuristic,
)
from ragbot.shared.constants import (
    HEURISTIC_INTENT_CONFIDENCE_STRONG,
    HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
    HEURISTIC_INTENT_CONFIDENCE_WEAK,
    INTENT_AGGREGATION,
    INTENT_CHITCHAT_LABEL as _CHITCHAT_LABEL,
    INTENT_COMPARISON,
    INTENT_GREETING,
    INTENT_MULTI_HOP,
)


def test_confidence_tiers_straddle_the_threshold() -> None:
    """Q9 invariant guard: WEAK < THRESHOLD < STRONG. If a future edit collapses
    the WEAK tier back to the threshold, ``confidence >= threshold`` would skip
    the LLM for aggregation/multi_hop/comparison — the exact bug this fixes."""
    assert HEURISTIC_INTENT_CONFIDENCE_WEAK < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD
    assert HEURISTIC_INTENT_CONFIDENCE_THRESHOLD < HEURISTIC_INTENT_CONFIDENCE_STRONG


# ---------------------------------------------------------------------------
# Greeting patterns
# ---------------------------------------------------------------------------

class TestGreetingIntent:
    def test_xin_chao_vi(self):
        result = classify_heuristic("xin chào")
        assert result.intent == INTENT_GREETING
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_hi_case_insensitive(self):
        result = classify_heuristic("Hi")
        assert result.intent == INTENT_GREETING
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_hello_followed_by_text(self):
        result = classify_heuristic("hello bạn ơi")
        assert result.intent == INTENT_GREETING
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_chao_em(self):
        result = classify_heuristic("chào em")
        assert result.intent == INTENT_GREETING
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_chao_ban(self):
        result = classify_heuristic("chào bạn")
        assert result.intent == INTENT_GREETING
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_greeting_pattern_stored(self):
        result = classify_heuristic("hi")
        assert result.matched_pattern is not None
        assert len(result.matched_pattern) > 0

    def test_greeting_confidence_is_high(self):
        """Anchored greeting patterns should return confidence = 0.90."""
        result = classify_heuristic("xin chào bạn")
        assert result.confidence == pytest.approx(0.90, abs=0.01)


# ---------------------------------------------------------------------------
# Chitchat patterns
# ---------------------------------------------------------------------------

class TestChitchatIntent:
    def test_cam_on_vi(self):
        result = classify_heuristic("cảm ơn")
        assert result.intent == _CHITCHAT_LABEL
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_cam_on_alternate_spelling(self):
        result = classify_heuristic("cám ơn")
        assert result.intent == _CHITCHAT_LABEL
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_thanks_en(self):
        result = classify_heuristic("thanks")
        assert result.intent == _CHITCHAT_LABEL
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_ok_short(self):
        result = classify_heuristic("ok")
        assert result.intent == _CHITCHAT_LABEL
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_duoc_roi(self):
        result = classify_heuristic("được rồi")
        assert result.intent == _CHITCHAT_LABEL
        assert result.confidence >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Aggregation patterns
# ---------------------------------------------------------------------------

# Q9: aggregation/multi_hop/comparison are mid-string HINTS — the classifier
# detects the intent but assigns the WEAK tier (BELOW the trust floor) so the
# understand node forces an LLM check. The old assertion ``>= THRESHOLD`` pinned
# the bug (these skipped the LLM). Correct invariant: intent detected AND
# confidence < threshold (→ caller falls back to LLM).

class TestAggregationIntent:
    def test_bao_nhieu(self):
        result = classify_heuristic("bao nhiêu loại dịch vụ có ở đây?")
        assert result.intent == INTENT_AGGREGATION
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_co_may(self):
        result = classify_heuristic("có mấy sản phẩm trong danh mục này")
        assert result.intent == INTENT_AGGREGATION
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_liet_ke(self):
        result = classify_heuristic("liệt kê tất cả các gói cước")
        assert result.intent == INTENT_AGGREGATION
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Multi-hop patterns
# ---------------------------------------------------------------------------

class TestMultiHopIntent:
    def test_tai_sao(self):
        result = classify_heuristic("tại sao phí tăng vậy?")
        assert result.intent == INTENT_MULTI_HOP
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_vi_sao(self):
        result = classify_heuristic("vì sao cần đăng ký trước?")
        assert result.intent == INTENT_MULTI_HOP
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_giai_thich(self):
        result = classify_heuristic("giải thích quy trình này")
        assert result.intent == INTENT_MULTI_HOP
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Comparison patterns
# ---------------------------------------------------------------------------

class TestComparisonIntent:
    def test_so_sanh(self):
        result = classify_heuristic("so sánh gói A và gói B")
        assert result.intent == INTENT_COMPARISON
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_khac_nhau(self):
        result = classify_heuristic("khác nhau như thế nào?")
        assert result.intent == INTENT_COMPARISON
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD

    def test_vs_keyword(self):
        result = classify_heuristic("gói A vs gói B giá thế nào")
        assert result.intent == INTENT_COMPARISON
        assert result.confidence < HEURISTIC_INTENT_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# No-match / fallthrough cases (HALLU=0 guard)
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_domain_query_no_match(self):
        """Domain-specific factoid question must NOT match — LLM path required."""
        result = classify_heuristic("quy trình đăng ký dịch vụ internet như thế nào?")
        assert result.intent is None
        assert result.confidence == pytest.approx(0.0)

    def test_empty_string_returns_none(self):
        result = classify_heuristic("")
        assert result.intent is None
        assert result.confidence == pytest.approx(0.0)
        assert result.matched_pattern is None

    def test_whitespace_only_returns_none(self):
        result = classify_heuristic("   ")
        assert result.intent is None
        assert result.confidence == pytest.approx(0.0)

    def test_price_inquiry_no_match(self):
        result = classify_heuristic("giá dịch vụ là bao nhiêu tiền một tháng?")
        # "bao nhiêu" would match aggregation pattern — this is intentional;
        # the heuristic detects surface-level signals. LLM verifies.
        # This test just verifies we get a *consistent* result.
        assert result.intent is None or result.intent == INTENT_AGGREGATION

    def test_heuristic_result_is_dataclass(self):
        result = classify_heuristic("xin chào")
        assert isinstance(result, HeuristicResult)
        assert hasattr(result, "intent")
        assert hasattr(result, "confidence")
        assert hasattr(result, "matched_pattern")

    def test_frozen_result_immutable(self):
        result = classify_heuristic("xin chào")
        with pytest.raises((AttributeError, TypeError)):
            result.intent = "other"  # type: ignore[misc]
