"""Unit tests for ``application.services.multi_query_expansion``.

multi-query (3-5 paraphrases + RRF merge) replaces
HyDE single-shot. Tests cover:
  - Successful JSON-array LLM response → expand returns N+1 (orig + paraphrases).
  - LLM exception → graceful fallback to ``[query]``.
  - LLM timeout → fallback (asyncio.TimeoutError handled).
  - Duplicate paraphrases → deduplicated case-fold.
  - n_variants <= 1 → skip LLM call (optimisation).
  - Code-fenced JSON output → still parses.
  - Non-JSON line list → fallback parser extracts.
  - Empty query → []
  - rrf_merge_chunks: chunks ranked by sum(1/(k+rank)) and dedup by id.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.multi_query_expansion import (
    expand_query,
    rrf_merge_chunks,
)
from ragbot.shared.constants import (
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_RRF_K,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# expand_query                                                                #
# --------------------------------------------------------------------------- #


def test_expand_query_returns_orig_plus_paraphrases() -> None:
    """LLM returns clean JSON array → caller gets [orig, p1, p2]."""
    llm = AsyncMock(return_value={
        "text": '["bao hành sản phẩm bao lâu?", "thời hạn bảo hành là gì?"]',
    })
    out = _run(expand_query(
        "sản phẩm bảo hành mấy năm?",
        n_variants=3,
        model_id="cheap-model",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert len(out) == 3, out
    assert out[0] == "sản phẩm bảo hành mấy năm?", "original must come first"
    assert "bao lâu" in out[1] or "thời hạn" in out[1]


def test_expand_query_fallback_on_exception() -> None:
    """LLM raises → graceful fallback returns [query]."""
    llm = AsyncMock(side_effect=RuntimeError("provider down"))
    out = _run(expand_query(
        "test query",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out == ["test query"]


def test_expand_query_fallback_on_timeout() -> None:
    """LLM hangs past timeout → fallback to [query]."""

    async def _slow(**_kw):
        await asyncio.sleep(10)
        return {"text": "[]"}

    out = _run(expand_query(
        "slow query",
        n_variants=3,
        model_id="x",
        timeout_s=1,  # short
        llm_complete_fn=_slow,
    ))
    assert out == ["slow query"]


def test_expand_query_dedups_duplicates() -> None:
    """If LLM emits duplicates / original — output dedups case-fold."""
    llm = AsyncMock(return_value={
        "text": '["hello world", "Hello World", "HELLO  world", "different"]',
    })
    out = _run(expand_query(
        "hello world",
        n_variants=5,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    # original "hello world" first; 3 dup-variants collapse; "different" added.
    assert out[0] == "hello world"
    # all entries unique under case-fold + whitespace collapse
    norms = [" ".join(s.split()).strip().casefold() for s in out]
    assert len(norms) == len(set(norms)), out
    assert "different" in out


def test_expand_query_n1_skips_llm() -> None:
    """N=1 → optimisation: skip LLM, return [query]."""
    llm = AsyncMock(return_value={"text": '["x"]'})
    out = _run(expand_query(
        "only original",
        n_variants=1,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out == ["only original"]
    llm.assert_not_called()


def test_expand_query_handles_code_fenced_json() -> None:
    """LLM wraps response in ```json ... ``` fences — still parses."""
    llm = AsyncMock(return_value={
        "text": '```json\n["alt one", "alt two"]\n```',
    })
    out = _run(expand_query(
        "orig",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out == ["orig", "alt one", "alt two"]


def test_expand_query_line_based_fallback_parse() -> None:
    """LLM ignores JSON instruction → numbered list still extracts."""
    llm = AsyncMock(return_value={
        "text": "1. paraphrase one\n2. paraphrase two",
    })
    out = _run(expand_query(
        "the query",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert out[0] == "the query"
    assert any("paraphrase one" in s for s in out)
    assert any("paraphrase two" in s for s in out)


def test_expand_query_empty_query_returns_empty() -> None:
    out = _run(expand_query(
        "",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=AsyncMock(),
    ))
    assert out == []


def test_expand_query_caps_at_max_variants() -> None:
    """LLM returns more paraphrases than allowed — output capped at max_variants."""
    llm = AsyncMock(return_value={
        "text": '["a", "b", "c", "d", "e", "f", "g", "h"]',
    })
    out = _run(expand_query(
        "orig",
        n_variants=10,  # caller asks 10 …
        max_variants=DEFAULT_MULTI_QUERY_MAX_VARIANTS,  # … but ceiling=5
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    assert len(out) <= DEFAULT_MULTI_QUERY_MAX_VARIANTS, out
    assert out[0] == "orig"


def test_expand_query_uses_default_n_variants_constant() -> None:
    """Service signature default tracks DEFAULT_MULTI_QUERY_N_VARIANTS const.

    Mock returns enough paraphrases (8) so the assertion holds for any
    reasonable value of ``DEFAULT_MULTI_QUERY_N_VARIANTS`` and survives the
    retrieval-tuning bump from 3→5.
    """
    llm = AsyncMock(return_value={
        "text": '["p1", "p2", "p3", "p4", "p5", "p6", "p7"]',
    })
    out = _run(expand_query(
        "q",
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
    ))
    # Output = original + (DEFAULT_MULTI_QUERY_N_VARIANTS - 1) paraphrases.
    assert len(out) == DEFAULT_MULTI_QUERY_N_VARIANTS


# --------------------------------------------------------------------------- #
# rrf_merge_chunks                                                            #
# --------------------------------------------------------------------------- #


def test_rrf_merge_chunks_dedups_by_chunk_id() -> None:
    """Chunk appearing in two lists → single output entry, summed score."""
    list_a = [{"chunk_id": "A", "text": "a"}, {"chunk_id": "B", "text": "b"}]
    list_b = [{"chunk_id": "A", "text": "a"}, {"chunk_id": "C", "text": "c"}]
    merged = rrf_merge_chunks([list_a, list_b], rrf_k=DEFAULT_RRF_K)
    ids = [c["chunk_id"] for c in merged]
    assert sorted(ids) == ["A", "B", "C"], "all 3 unique, dedup by id"
    # A appears in both at rank 0 → score = 2 * 1/(60+1) > B's single 1/61.
    a_score = next(c["score"] for c in merged if c["chunk_id"] == "A")
    b_score = next(c["score"] for c in merged if c["chunk_id"] == "B")
    assert a_score > b_score, "A double-hit must outrank B"


def test_rrf_merge_chunks_orders_by_score() -> None:
    """Output ordered by RRF score descending."""
    list_a = [{"chunk_id": str(i), "text": f"t{i}"} for i in range(3)]
    list_b = [{"chunk_id": str(i), "text": f"t{i}"} for i in range(3)]
    merged = rrf_merge_chunks([list_a, list_b])
    scores = [c["score"] for c in merged]
    assert scores == sorted(scores, reverse=True), "must be desc-ordered"


def test_rrf_merge_chunks_single_list_passthrough() -> None:
    """Only one non-empty list → return as-is, scores untouched."""
    src = [{"chunk_id": "A", "text": "a", "score": 0.99}]
    merged = rrf_merge_chunks([src, []])
    assert merged == src


def test_rrf_merge_chunks_handles_empty_input() -> None:
    assert rrf_merge_chunks([]) == []
    assert rrf_merge_chunks([[]]) == []
    assert rrf_merge_chunks([[], []]) == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
