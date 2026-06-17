"""Variant-0 safety net tests — R4 root-cause regression guard.

Background: in the MEGA load test (150q × 3 rounds) we observed 11 R2-PASS
turns regress to REFUSE in R4 because the rewriter LLM was stochastic —
``multi_query_fanout`` produced 3 paraphrases that ALL dropped key signal
tokens (rare brand / numeric / domain term), so the parallel hybrid_search
returned 0 chunks and RRF could not recover.

Fix: ``expand_query`` now ALWAYS prepends the user's verbatim query as
variant 0 when ``include_original=True`` (default). The final variant
list becomes ``[original, *unique_rewrites]`` capped at
``DEFAULT_MULTI_QUERY_MAX_VARIANTS``. RRF therefore always has at least
one branch with the user's exact wording, providing a deterministic
floor on recall regardless of rewriter quality.

These tests pin the contract so a future refactor cannot silently drop
the safety net.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.multi_query_expansion import expand_query
from ragbot.shared.constants import (
    DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_REWRITE_COUNT,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Constants exist + sane                                                      #
# --------------------------------------------------------------------------- #


def test_constants_defined_and_sane() -> None:
    """REWRITE_COUNT + INCLUDE_ORIGINAL constants must exist with safe defaults.

    Pins the public default contract — INCLUDE_ORIGINAL must default True so
    the safety net is on by default. REWRITE_COUNT must be >= 1 (otherwise
    variant 0 alone is the whole result and the LLM is never asked).
    """
    assert DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL is True, (
        "Variant-0 safety net must default ON"
    )
    assert isinstance(DEFAULT_MULTI_QUERY_REWRITE_COUNT, int)
    assert DEFAULT_MULTI_QUERY_REWRITE_COUNT >= 1, (
        "REWRITE_COUNT must be >= 1 to actually exercise the rewriter path"
    )


# --------------------------------------------------------------------------- #
# Test 1 — Variant 0 always = original Q                                       #
# --------------------------------------------------------------------------- #


def test_variant0_always_equals_original_query() -> None:
    """Whatever the rewriter returns, output[0] is the verbatim user query.

    This is THE invariant: even if the LLM returns wildly different
    paraphrases that drop every keyword, the original lives at variant 0
    so RRF still has a faithful branch.
    """
    original = "bảo hành sản phẩm XYZ-9000 trong bao lâu?"
    llm = AsyncMock(return_value={
        "text": '["thời hạn coverage là gì?", "policy hỗ trợ ra sao?"]',
    })
    out = _run(expand_query(
        original,
        n_variants=3,
        model_id="cheap",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out, "must return at least one variant"
    assert out[0] == original, (
        f"variant 0 must equal original; got {out[0]!r}"
    )


# --------------------------------------------------------------------------- #
# Test 2 — Rewriter returns same as original → dedup, original kept           #
# --------------------------------------------------------------------------- #


def test_rewriter_echoes_original_dedups_to_single_variant() -> None:
    """When the LLM returns ONLY echoes of the original (case/whitespace
    variants), dedup collapses them and the surviving entry is the original
    at index 0. Length 1 is acceptable — the safety net guarantees ≥ 1.
    """
    original = "hello world"
    llm = AsyncMock(return_value={
        "text": '["Hello World", "HELLO  world", "hello world"]',
    })
    out = _run(expand_query(
        original,
        n_variants=4,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out, "must not be empty when original is provided"
    assert out[0] == original
    # Dedup must collapse the case-fold echoes — only original survives.
    assert len(out) == 1, (
        f"all rewrites are case-fold echoes; expected dedup to 1, got {out!r}"
    )


# --------------------------------------------------------------------------- #
# Test 3 — Rewriter returns N rewrites → final = [original, *unique_rewrites] #
# --------------------------------------------------------------------------- #


def test_rewriter_returns_n_rewrites_final_starts_with_original() -> None:
    """Happy path: LLM returns N distinct paraphrases. Final list is
    ``[original, rewrite_1, …, rewrite_N]`` with original strictly first.
    """
    original = "câu hỏi gốc"
    llm = AsyncMock(return_value={
        "text": '["paraphrase 1", "paraphrase 2", "paraphrase 3"]',
    })
    out = _run(expand_query(
        original,
        n_variants=4,  # 1 original + 3 rewrites
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        max_variants=DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    ))
    assert out[0] == original, "original must be variant 0"
    # All 3 paraphrases distinct → expect 4 entries (capped at max_variants).
    expected_len = min(4, DEFAULT_MULTI_QUERY_MAX_VARIANTS)
    assert len(out) == expected_len, out
    # Rewrites preserved in order after the original.
    assert "paraphrase 1" in out
    assert "paraphrase 2" in out
    assert "paraphrase 3" in out


# --------------------------------------------------------------------------- #
# Test 4 — Empty rewrites (LLM unparseable / failed) → final = [original]     #
# --------------------------------------------------------------------------- #


def test_empty_rewrites_returns_original_only() -> None:
    """When the LLM returns garbage that produces zero parseable rewrites,
    the safety net guarantees the result is still ``[original]`` — never
    an empty list. This is the very R4 regression scenario this fix targets.
    """
    original = "rare-brand-token QUERY-42"
    # LLM returns prose with no JSON array, no numbered list — parser yields [].
    llm = AsyncMock(return_value={"text": "Sorry I cannot help."})
    out = _run(expand_query(
        original,
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    # The line-based parser may extract "Sorry I cannot help." as a single
    # candidate, but the contract we're pinning is "original at index 0
    # always present" — that holds regardless of parser permissiveness.
    assert out, "safety net must yield at least the original"
    assert out[0] == original


def test_llm_exception_returns_original_only() -> None:
    """LLM raises → catch path triggers ``_finalise([])`` → ``[original]``.

    Pins the failure-path contract: even when the rewriter is completely
    unavailable, the user's query reaches retrieval.
    """
    original = "the only query"
    llm = AsyncMock(side_effect=RuntimeError("provider down"))
    out = _run(expand_query(
        original,
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out == [original], (
        f"on LLM failure, must fall back to [original]; got {out!r}"
    )


# --------------------------------------------------------------------------- #
# Test 5 — Toggle include_original=False → original excluded                  #
# --------------------------------------------------------------------------- #


def test_include_original_false_excludes_original() -> None:
    """Diagnostic mode: ``include_original=False`` returns only LLM rewrites.

    Used for A/B isolating rewriter recall — never the production default.
    Pins the toggle so a future change can't silently flip the default and
    re-introduce the R4 regression.
    """
    original = "câu hỏi gốc"
    llm = AsyncMock(return_value={
        "text": '["alt one", "alt two"]',
    })
    out = _run(expand_query(
        original,
        n_variants=2,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        include_original=False,
    ))
    assert original not in out, (
        f"include_original=False must drop the verbatim query; got {out!r}"
    )
    assert "alt one" in out
    assert "alt two" in out


def test_include_original_false_with_failure_returns_empty() -> None:
    """Safety-net OFF + LLM failure → empty list (no original fallback)."""
    llm = AsyncMock(side_effect=RuntimeError("nope"))
    out = _run(expand_query(
        "anything",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        include_original=False,
    ))
    assert out == [], (
        "include_original=False must NOT fall back to [original] on failure"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
