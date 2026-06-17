"""F14-HIGH-CC1-2 — segment_vi_compounds is gated on ``language``.

Multi-industry safety: underthesea is a Vietnamese-only segmenter. Running
it on EN / JP / KO / etc. text wastes CPU (~100-300 ms per chunk) and risks
corrupting non-VN tokens. The default behaves like a no-op for non-VN.
"""

from __future__ import annotations

from ragbot.shared import vi_tokenizer
from ragbot.shared.constants import DEFAULT_LANGUAGE, VI_DOMAIN_LANGUAGES


def test_no_op_for_english() -> None:
    """English text passes through unchanged when language='en'."""
    text = "skin care services pricing"
    out = vi_tokenizer.segment_vi_compounds(text, language="en")
    assert out == text, (
        "F14-HIGH-CC1-2 regression — non-VN languages must skip "
        "underthesea entirely (no-op)."
    )


def test_no_op_for_japanese() -> None:
    """Japanese text is returned untouched."""
    text = "こんにちは、お元気ですか"
    out = vi_tokenizer.segment_vi_compounds(text, language="ja")
    assert out == text, (
        "F14-HIGH-CC1-2 regression — JP bots must not touch underthesea."
    )


def test_runs_for_vietnamese() -> None:
    """Vietnamese still segments — the gate must not break the VN path."""
    text = "chăm sóc da mặt"
    out = vi_tokenizer.segment_vi_compounds(text, language="vi")
    # underthesea joins ``chăm sóc`` into ``chăm_sóc``; presence of any
    # underscore proves segmentation actually engaged.
    assert "_" in out, (
        f"VN gating regression — expected segmentation on VN text, got {out!r}"
    )


def test_default_language_constant() -> None:
    """Default kwarg uses DEFAULT_LANGUAGE so VN callers stay backward-compat."""
    # No language= passed: must still segment (DEFAULT_LANGUAGE = "vi").
    text = "chăm sóc da mặt"
    out = vi_tokenizer.segment_vi_compounds(text)
    assert "_" in out
    # And DEFAULT_LANGUAGE itself must be in the VN gate set so existing
    # call sites keep working.
    assert DEFAULT_LANGUAGE in VI_DOMAIN_LANGUAGES
