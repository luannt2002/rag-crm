"""Tests for grounding fuzzy pre-checks.

Covers:
- _grounding_substring_match helper
- _extract_numbers helper
- OutputGuardrail.grounding_check substring + numeric passes
- Q6 hotline false-positive regression: "0900111222" answer grounded via
  numeric-overlap even when no [chunk_id] citation marker present.
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.guardrails.local_guardrail import (
    OutputGuardrail,
    _extract_numbers,
    _grounding_substring_match,
)
from ragbot.shared.constants import (
    DEFAULT_GROUNDING_NUMERIC_OVERLAP_ENABLED,
    DEFAULT_GROUNDING_SUBSTRING_MIN,
)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestExtractNumbers:
    def test_extracts_phone(self):
        assert "0900111222" in _extract_numbers("Hotline: 0900111222")

    def test_extracts_price(self):
        assert "250000" in _extract_numbers("Giá: 250000 VND")

    def test_ignores_single_digit(self):
        # Single digit should NOT be extracted (min 2 digits)
        nums = _extract_numbers("1 item")
        assert "1" not in nums

    def test_empty_string(self):
        assert _extract_numbers("") == set()

    def test_multiple_numbers(self):
        nums = _extract_numbers("Giờ 9h-21h, hotline 0900111222")
        assert "9" not in nums  # single digit excluded
        assert "21" in nums
        assert "0900111222" in nums


class TestGroundingSubstringMatch:
    def test_exact_match_returns_true(self):
        answer = "Hotline của spa là 0900111222 ạ."
        chunk = "Hotline: 0900111222, Email: support@example.com"
        # "0900111222" is 10 chars < DEFAULT_GROUNDING_SUBSTRING_MIN (20), but
        # "0900111222 ạ." + context gives >= 20 when anchor chosen correctly.
        # Use a 10-char min to verify the logic itself works:
        assert _grounding_substring_match(answer, chunk, min_len=10) is True

    def test_no_match_returns_false(self):
        answer = "Xin chào bạn!"
        chunk = "Hotline: 0900111222"
        assert _grounding_substring_match(answer, chunk, min_len=20) is False

    def test_too_short_answer_returns_false(self):
        answer = "Short"  # < min_len
        chunk = "Short answer text here in the chunk"
        assert _grounding_substring_match(answer, chunk, min_len=20) is False

    def test_verbatim_address_match(self):
        answer = "Cửa hàng ở 12 Đường Mẫu, Quận Thử Nghiệm, Thành Phố"
        chunk = "Địa chỉ: 12 Đường Mẫu, Quận Thử Nghiệm, Thành Phố"
        assert _grounding_substring_match(answer, chunk, min_len=20) is True

    def test_empty_inputs(self):
        assert _grounding_substring_match("", "some chunk", min_len=20) is False
        assert _grounding_substring_match("some answer", "", min_len=20) is False


# ---------------------------------------------------------------------------
# grounding_check — pass hierarchy
# ---------------------------------------------------------------------------

class TestGroundingCheckFuzzy:
    """grounding_check() new fuzzy passes — citation → substring → numeric."""

    # --- Pass 1: citation marker ---

    def test_citation_marker_still_passes(self):
        hit = OutputGuardrail.grounding_check(
            "Hotline là 0900111222 [chunk-xyz].",
            retrieved_chunks=[{"content": "Hotline: 0900111222"}],
        )
        assert hit is None

    # --- Pass 2: substring verbatim ---

    def test_substring_match_grounds_answer(self):
        """30-char verbatim substring → grounded without citation marker."""
        chunk_text = "Địa chỉ: 12 Đường Mẫu, Quận Thử Nghiệm, Thành Phố — mở cửa 9h-21h"
        answer = "Cửa hàng nằm ở 12 Đường Mẫu, Quận Thử Nghiệm, Thành Phố."
        hit = OutputGuardrail.grounding_check(
            answer,
            retrieved_chunks=[{"content": chunk_text}],
        )
        assert hit is None, f"Expected grounded via substring, got hit: {hit}"

    def test_substring_too_short_falls_through(self):
        """Short answer below substring_min falls through to numeric check."""
        answer = "0900111222"  # 10 chars < DEFAULT (20) — cannot pass substring
        chunk_text = "Hotline: 0900111222, Email: test@example.com"
        # Should still pass via numeric-overlap (all digits in answer ⊆ chunk)
        hit = OutputGuardrail.grounding_check(
            answer,
            retrieved_chunks=[{"content": chunk_text}],
        )
        assert hit is None, "Short numeric answer should be grounded via numeric pass"

    # --- Pass 3: numeric overlap ---

    def test_hotline_q6_regression(self):
        """Regression: Q6 'Hotline của spa là 0900111222 ạ.' must NOT fire grounding_fail.

        Agent B Vòng 0 finding: this exact answer was 100% grounded per human
        evaluation but still fired grounding_fail WARN.  The numeric-overlap pass
        (or substring pass at 20-char span) must catch it.
        """
        answer = "Hotline của spa là 0900111222 ạ."
        chunk_text = "Hours: 9h-21h T2-CN, Hotline: 0900111222, Email: support@example.com"
        hit = OutputGuardrail.grounding_check(
            answer,
            retrieved_chunks=[{"content": chunk_text}],
        )
        assert hit is None, (
            f"Q6 hotline regression: expected grounded=True, got hit={hit}.  "
            "grounding_fail fired on correctly grounded hotline answer."
        )

    def test_numeric_overlap_all_numbers_in_chunks(self):
        """All numbers in answer present in chunks → grounded."""
        answer = "Giờ mở cửa 9h đến 21h, số điện thoại 0900111222."
        chunk = "Giờ làm việc: 9h00 - 21h00. SĐT: 0900111222"
        hit = OutputGuardrail.grounding_check(
            answer,
            retrieved_chunks=[{"content": chunk}],
        )
        assert hit is None

    def test_numeric_overlap_number_not_in_chunks_fires(self):
        """Number in answer NOT found in any chunk → grounding_fail."""
        answer = "Giảm giá 50%."
        chunk = "Giá dịch vụ gội đầu 100k."
        # "50" not in chunk → should fire grounding_fail
        hit = OutputGuardrail.grounding_check(
            answer,
            retrieved_chunks=[{"content": chunk}],
        )
        assert hit is not None
        assert hit.rule_id == "grounding_fail"

    def test_no_numbers_in_answer_falls_to_rule(self):
        """Answer with no digits, no citation, no long substring → grounding_fail."""
        answer = "Spa mở cửa vào buổi sáng."
        chunk = "Mở cửa từ thứ 2 đến chủ nhật."
        # No numbers in answer → numeric pass skipped → no citation, no long substr
        # substr: "Spa mở cửa vào buổi sáng." = ~27 chars, check if any 20-char window in chunk
        # "ửa vào buổi sáng" is 17 chars, "mở cửa vào buổi sáng" is in chunk? No.
        # chunk has "Mở cửa từ thứ 2 đến chủ nhật." — "Mở" vs "mở" (case diff)
        # Let's use a chunk that clearly has no overlap:
        chunk_nomatch = "Dịch vụ gội đầu dưỡng tóc cao cấp."
        hit = OutputGuardrail.grounding_check(
            "Sản phẩm chất lượng cao.",
            retrieved_chunks=[{"content": chunk_nomatch}],
        )
        assert hit is not None
        assert hit.rule_id == "grounding_fail"

    # --- Edge: numeric_overlap_enabled=False ---

    def test_numeric_overlap_disabled_skips_numeric_pass(self):
        """When numeric_overlap_enabled=False, numeric pass is skipped → falls to rule."""
        answer = "0900111222"
        chunk = "Hotline: 0900111222"
        hit = OutputGuardrail.grounding_check(
            answer,
            retrieved_chunks=[{"content": chunk}],
            numeric_overlap_enabled=False,
        )
        # No citation, answer too short for substring (10 < 20), numeric disabled
        assert hit is not None
        assert hit.rule_id == "grounding_fail"

    # --- Edge: empty chunks ---

    def test_no_retrieved_chunks_returns_none(self):
        assert OutputGuardrail.grounding_check("answer", retrieved_chunks=None) is None
        assert OutputGuardrail.grounding_check("answer", retrieved_chunks=[]) is None

    # --- Constant values ---

    def test_constants_in_expected_range(self):
        assert DEFAULT_GROUNDING_SUBSTRING_MIN >= 10
        assert DEFAULT_GROUNDING_SUBSTRING_MIN <= 50
        assert DEFAULT_GROUNDING_NUMERIC_OVERLAP_ENABLED is True
