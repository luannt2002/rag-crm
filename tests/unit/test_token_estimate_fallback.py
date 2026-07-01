"""Cost-metering fallback: estimate tokens when the provider omits usage.

The innocom gateway returns no ``usage`` block → prompt/completion tokens = 0 →
cost logs $0 (unmeasurable). estimate_tokens_fallback fills a MISSING count from
a local tiktoken estimate, and NEVER overwrites a real provider count.
"""
from __future__ import annotations

from ragbot.shared.llm_usage import estimate_tokens_fallback


def test_zero_counts_estimated_from_text() -> None:
    msgs = [{"role": "user", "content": "giá lốp 155/80R13 bao nhiêu"}]
    p, c = estimate_tokens_fallback(msgs, "Dạ, giá 684.000đ/lốp ạ.", 0, 0)
    assert p > 0, "prompt tokens must be estimated when provider returns 0"
    assert c > 0, "completion tokens must be estimated when provider returns 0"


def test_real_counts_never_overwritten() -> None:
    msgs = [{"role": "user", "content": "x" * 400}]
    p, c = estimate_tokens_fallback(msgs, "y" * 400, 123, 45)
    assert (p, c) == (123, 45), "a real provider count must never be overwritten"


def test_only_missing_side_filled() -> None:
    msgs = [{"role": "user", "content": "câu hỏi test dài hơn một chút"}]
    # provider gave prompt but not completion
    p, c = estimate_tokens_fallback(msgs, "một câu trả lời", 50, 0)
    assert p == 50, "real prompt count kept"
    assert c > 0, "missing completion estimated"


def test_multimodal_content_list_safe() -> None:
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hello world"}]}]
    p, c = estimate_tokens_fallback(msgs, "hi", 0, 0)
    assert p > 0 and c > 0
