"""Unit tests for scripts/test_universal_cases.py::classify_case.

Covers all 12 categories × {PASS, FAIL, REFUSE, ERROR, placeholder-leak} paths.
Domain-neutral: every answer/chunk is a generic Vietnamese phrase — no brand,
no industry literal. Behaviour is validated structurally (refuse cue, redirect
cue, length, intent) per CLAUDE.md mindset.

Tier: T1 — verifies harness grading correctness (single source of truth for
universal-load-test pass-rate metrics).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so `test_universal_cases` is importable.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from test_universal_cases import (  # noqa: E402  — sys.path shim required
    GREETING_MIN_LEN,
    MIN_PASS_ANSWER_LEN,
    classify_case,
)

ALL_CATEGORIES = (
    "factoid_in_corpus",
    "comparison_in_corpus",
    "aggregation_in_corpus",
    "factoid_no_corpus",
    "greeting",
    "chitchat",
    "vu_vo",
    "off_topic",
    "booking",
    "hallucination_trap",
    "numeric_compute",
    "multi_intent",
)

# Generic Vietnamese substantive answer — long enough, no refuse/clarify/redirect cues.
_LONG_NEUTRAL = (
    "Nội dung này có đầy đủ thông tin chi tiết theo tài liệu nội bộ "
    "phục vụ cho yêu cầu của người dùng cuối."
)
assert len(_LONG_NEUTRAL) >= MIN_PASS_ANSWER_LEN  # safety guard

# Refuse cues (from REFUSE_PATTERNS).
_REFUSE_ANS = "Em chưa có thông tin về vấn đề này, anh/chị vui lòng liên hệ chuyên viên để được hỗ trợ thêm."

# Clarify cue (uses 'anh/chị muốn' — pure clarify, NO 'để em' which is REFUSE).
_CLARIFY_ANS = "Anh/chị muốn tìm hiểu cụ thể nội dung gì ạ?"

# Redirect cue (uses 'em chỉ hỗ trợ' — purely redirect, no refuse word).
_REDIRECT_ANS = "Em chỉ hỗ trợ trong lĩnh vực chuyên môn đã được cấu hình, anh/chị quay lại chủ đề chính giúp em."

# Booking cue.
_BOOKING_ANS = "Anh/chị cho em xin số điện thoại và khung giờ phù hợp để em sắp xếp lịch nhé."

# Greeting cue (>= GREETING_MIN_LEN).
_GREETING_ANS = "Xin chào anh/chị, em là trợ lý tư vấn rất vui được hỗ trợ anh chị hôm nay."
assert len(_GREETING_ANS) >= GREETING_MIN_LEN


def _call(
    *,
    category: str,
    answer: str = "",
    chunks_used: int = 0,
    intent: str = "",
    error: str | None = None,
    placeholder_leak: bool = False,
) -> str:
    return classify_case(
        category=category,
        answer=answer,
        chunks_used=chunks_used,
        intent=intent,
        error=error,
        placeholder_leak=placeholder_leak,
    )


# ─── Cross-cutting parametrized tests (apply to every category) ─────────────

@pytest.mark.parametrize("category", ALL_CATEGORIES)
def test_error_returns_ERROR_regardless_of_category(category: str):
    """error != None short-circuits to ERROR for every category."""
    assert _call(category=category, answer=_LONG_NEUTRAL, chunks_used=5,
                 error="HTTP 500 boom") == "ERROR"


@pytest.mark.parametrize("category", ALL_CATEGORIES)
def test_empty_answer_returns_FAIL_regardless_of_category(category: str):
    """Empty answer → FAIL for every category (no error)."""
    assert _call(category=category, answer="", chunks_used=5) == "FAIL"


@pytest.mark.parametrize("category", ALL_CATEGORIES)
def test_whitespace_only_answer_returns_FAIL_regardless_of_category(category: str):
    """Whitespace-only answer is treated as empty → FAIL."""
    assert _call(category=category, answer="   \n\t  ", chunks_used=5) == "FAIL"


@pytest.mark.parametrize("category", ALL_CATEGORIES)
def test_placeholder_leak_returns_FAIL_regardless_of_category(category: str):
    """placeholder_leak=True → FAIL even when content would otherwise PASS."""
    assert _call(category=category, answer=_LONG_NEUTRAL, chunks_used=5,
                 placeholder_leak=True) == "FAIL"


# ─── factoid_in_corpus ──────────────────────────────────────────────────────

class TestFactoidInCorpus:
    CAT = "factoid_in_corpus"

    def test_pass_when_chunks_and_long_and_not_refuse(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=3) == "PASS"

    def test_fail_when_no_chunks(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=0) == "FAIL"

    def test_fail_when_too_short(self):
        assert _call(category=self.CAT, answer="Có ạ.", chunks_used=3) == "FAIL"

    def test_refuse_when_answer_has_refuse_cue(self):
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     chunks_used=3) == "REFUSE"

    def test_refuse_takes_priority_over_chunks_count(self):
        """Even with chunks > 0, refuse cue → REFUSE not PASS."""
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     chunks_used=10) == "REFUSE"


# ─── comparison_in_corpus ───────────────────────────────────────────────────

class TestComparisonInCorpus:
    CAT = "comparison_in_corpus"

    def test_pass_when_chunks_and_long_and_not_refuse(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=2) == "PASS"

    def test_fail_when_no_chunks(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=0) == "FAIL"

    def test_refuse_when_refuse_cue(self):
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     chunks_used=4) == "REFUSE"

    def test_fail_when_short_answer_even_with_chunks(self):
        assert _call(category=self.CAT, answer="ngắn", chunks_used=4) == "FAIL"


# ─── aggregation_in_corpus ──────────────────────────────────────────────────

class TestAggregationInCorpus:
    CAT = "aggregation_in_corpus"

    def test_pass_when_chunks_and_long(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=8) == "PASS"

    def test_refuse_when_refuse_cue(self):
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     chunks_used=2) == "REFUSE"

    def test_fail_when_no_chunks(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=0) == "FAIL"


# ─── factoid_no_corpus ──────────────────────────────────────────────────────

class TestFactoidNoCorpus:
    CAT = "factoid_no_corpus"

    def test_pass_when_refuse(self):
        """Correct behaviour: refuse info NOT in docs → PASS."""
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     chunks_used=0) == "PASS"

    def test_fail_when_substantive_answer_without_refuse(self):
        """Bot fabricated content → FAIL (no refuse cue)."""
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=0) == "FAIL"

    def test_fail_when_clarify_only_no_refuse(self):
        assert _call(category=self.CAT, answer=_CLARIFY_ANS,
                     chunks_used=0) == "FAIL"


# ─── greeting ───────────────────────────────────────────────────────────────

class TestGreeting:
    CAT = "greeting"

    def test_pass_when_intent_is_greeting(self):
        assert _call(category=self.CAT, answer="hi", intent="greeting") == "PASS"

    def test_pass_when_greeting_cue_and_long_enough(self):
        assert _call(category=self.CAT, answer=_GREETING_ANS,
                     intent="other") == "PASS"

    def test_fail_when_greeting_cue_but_too_short(self):
        # "Chào." is < GREETING_MIN_LEN and intent != greeting.
        short = "Chào ạ."
        assert len(short) < GREETING_MIN_LEN
        assert _call(category=self.CAT, answer=short, intent="other") == "FAIL"

    def test_refuse_when_refuse_cue_and_no_greeting_intent(self):
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     intent="other") == "REFUSE"

    def test_fail_when_no_cue_and_no_intent(self):
        # Long substantive answer with no greeting / refuse cue token.
        # Avoid words: chào, hello, hi anh, hi chị, rất vui, em là, xin chào,
        # plus the full canonical REFUSE cue set in
        # ``shared.constants.DEFAULT_LOADTEST_REFUSE_PATTERNS`` (33 fragments
        # post DRY-consolidation 2026-04-30 — includes `không nằm trong`,
        # `không thể`, `tôi không`, etc. that came from the legacy helper).
        ans = "Phản hồi dài bình thường thuộc nhóm cụm từ trung tính ạ."
        assert _call(category=self.CAT, answer=ans, intent="other") == "FAIL"


# ─── chitchat ───────────────────────────────────────────────────────────────

class TestChitchat:
    CAT = "chitchat"

    def test_pass_when_clarify(self):
        assert _call(category=self.CAT, answer=_CLARIFY_ANS) == "PASS"

    def test_pass_when_redirect(self):
        assert _call(category=self.CAT, answer=_REDIRECT_ANS) == "PASS"

    def test_refuse_when_pure_refuse_no_clarify_no_redirect(self):
        # 'không hỗ trợ' is REFUSE-only; ensure no clarify/redirect token.
        ans = "Em không hỗ trợ chủ đề này ạ."
        assert _call(category=self.CAT, answer=ans) == "REFUSE"

    def test_fail_when_neither_clarify_nor_redirect_nor_refuse(self):
        ans = "Trời hôm nay đẹp thật."
        assert _call(category=self.CAT, answer=ans) == "FAIL"


# ─── vu_vo ──────────────────────────────────────────────────────────────────

class TestVuVo:
    CAT = "vu_vo"

    def test_pass_when_clarify(self):
        assert _call(category=self.CAT, answer=_CLARIFY_ANS) == "PASS"

    def test_pass_when_redirect(self):
        assert _call(category=self.CAT, answer=_REDIRECT_ANS) == "PASS"

    def test_refuse_when_pure_refuse(self):
        ans = "Em chưa có thông tin về điều này."
        assert _call(category=self.CAT, answer=ans) == "REFUSE"

    def test_fail_when_random_substantive_no_cue(self):
        ans = "Mặt trời mọc đằng đông."
        assert _call(category=self.CAT, answer=ans) == "FAIL"


# ─── off_topic ──────────────────────────────────────────────────────────────

class TestOffTopic:
    CAT = "off_topic"

    def test_pass_when_redirect(self):
        assert _call(category=self.CAT, answer=_REDIRECT_ANS) == "PASS"

    def test_refuse_when_refuse_no_redirect(self):
        # Pure refuse — no redirect cue.
        ans = "Em không hỗ trợ ạ."
        assert _call(category=self.CAT, answer=ans) == "REFUSE"

    def test_fail_when_neither_redirect_nor_refuse(self):
        ans = "Mưa rào tháng năm rất to."
        assert _call(category=self.CAT, answer=ans) == "FAIL"


# ─── booking ────────────────────────────────────────────────────────────────

class TestBooking:
    CAT = "booking"

    def test_pass_when_booking_cue(self):
        assert _call(category=self.CAT, answer=_BOOKING_ANS) == "PASS"

    def test_pass_when_clarify_cue(self):
        assert _call(category=self.CAT, answer=_CLARIFY_ANS) == "PASS"

    def test_refuse_when_pure_refuse_no_booking_no_clarify(self):
        # Use refuse cue that is NOT also clarify/booking — 'không hỗ trợ'.
        ans = "Em chưa hỗ trợ tính năng này ạ."
        assert _call(category=self.CAT, answer=ans) == "REFUSE"

    def test_fail_when_no_cue(self):
        ans = "Cảm ơn anh/chị đã quan tâm tới sản phẩm."
        assert _call(category=self.CAT, answer=ans) == "FAIL"


# ─── hallucination_trap ────────────────────────────────────────────────────

class TestHallucinationTrap:
    CAT = "hallucination_trap"

    def test_pass_when_refuse(self):
        """Correct: refuse the false claim → PASS."""
        assert _call(category=self.CAT, answer=_REFUSE_ANS) == "PASS"

    def test_fail_when_substantive_confirms(self):
        """Bot confirmed false claim → FAIL."""
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL) == "FAIL"

    def test_fail_when_clarify_only(self):
        assert _call(category=self.CAT, answer=_CLARIFY_ANS) == "FAIL"


# ─── numeric_compute ───────────────────────────────────────────────────────

class TestNumericCompute:
    CAT = "numeric_compute"

    def test_pass_when_refuse(self):
        assert _call(category=self.CAT, answer=_REFUSE_ANS) == "PASS"

    def test_pass_when_booking_cue(self):
        """Redirect to staff via booking-capture is also acceptable."""
        assert _call(category=self.CAT, answer=_BOOKING_ANS) == "PASS"

    def test_fail_when_bot_attempts_calculation(self):
        ans = "Tổng cộng là 1 triệu hai trăm nghìn đồng cho ba tháng."
        assert _call(category=self.CAT, answer=ans) == "FAIL"


# ─── multi_intent ──────────────────────────────────────────────────────────

class TestMultiIntent:
    CAT = "multi_intent"

    def test_pass_when_chunks_and_long(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=4) == "PASS"

    def test_refuse_when_refuse_cue(self):
        assert _call(category=self.CAT, answer=_REFUSE_ANS,
                     chunks_used=4) == "REFUSE"

    def test_fail_when_no_chunks(self):
        assert _call(category=self.CAT, answer=_LONG_NEUTRAL,
                     chunks_used=0) == "FAIL"

    def test_fail_when_short_answer(self):
        assert _call(category=self.CAT, answer="ok", chunks_used=4) == "FAIL"


# ─── Unknown / fallback category ────────────────────────────────────────────

class TestUnknownCategory:
    """`classify_case` fallback for unknown category — length-only gate."""

    def test_unknown_pass_when_long(self):
        assert _call(category="zzz_unknown", answer=_LONG_NEUTRAL) == "PASS"

    def test_unknown_fail_when_short(self):
        assert _call(category="zzz_unknown", answer="hi") == "FAIL"


# ─── Edge: placeholder leak token variants ─────────────────────────────────

class TestPlaceholderLeakDetection:
    """The harness sets placeholder_leak via `_has_placeholder_leak` upstream;
    `classify_case` itself only consumes the bool. Confirm bool is honoured."""

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_leak_overrides_pass_path(self, category: str):
        # Even with all PASS-conditions met, leak=True → FAIL.
        assert _call(category=category, answer=_LONG_NEUTRAL, chunks_used=10,
                     intent="greeting", placeholder_leak=True) == "FAIL"

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_error_priority_over_leak(self, category: str):
        # error wins over leak.
        assert _call(category=category, answer=_LONG_NEUTRAL, chunks_used=10,
                     placeholder_leak=True, error="boom") == "ERROR"

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_error_priority_over_empty(self, category: str):
        assert _call(category=category, answer="", error="boom") == "ERROR"
