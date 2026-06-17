"""Pin test: /chat API _build_sources preview length 1500 chars.

Before 2026-05-27: preview was capped at 200 chars. External evaluators
(RAGAS LLM judge) received header-only snippet → Faith=0 false negatives
on 37% of turns despite bot answering correctly from corpus.

After fix: 1500 chars covers legal Điều full text + FAQ block, so judge
can verify claims against real chunk content.
"""
from __future__ import annotations

import inspect
import re

from ragbot.interfaces.http.routes.test_chat import chat_routes as _route


def test_build_sources_preview_is_at_least_1500_chars() -> None:
    """_build_sources source must contain ``[:1500]`` slice for preview."""
    # _build_sources is nested inside test_chat() async function — grep
    # the module source for the slice literal.
    src = inspect.getsource(_route)
    # Should contain new 1500 slice, not the old 200
    matches_1500 = re.findall(r'\[:1500\]', src)
    matches_200_preview = re.findall(r'or ""\)\[:200\]', src)
    assert matches_1500, (
        "_build_sources preview slice [:1500] missing — evaluator will "
        "receive truncated chunks again and RAGAS Faith will false-fail."
    )
    assert not matches_200_preview, (
        "stale [:200] preview slice still present — remove or bump to >= 1500."
    )


def test_build_sources_preview_documented() -> None:
    """Source must document why 1500 was chosen (avoid silent revert)."""
    src = inspect.getsource(_route)
    assert "1500" in src
    # Comment marker should be near the slice
    assert "evaluator" in src.lower() or "RAGAS" in src or "judge" in src.lower()
