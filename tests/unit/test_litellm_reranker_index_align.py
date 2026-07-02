"""[audit Q13 guard] LiteLLMReranker must map reranker scores back to the CORRECT
chunk even when some chunks have empty content.

Empty-content chunks are dropped from the reranker input; the reranker returns an
``index`` into that FILTERED list. Mapping it against the original chunk list shifts
the score onto the wrong chunk. This pins the passage→chunk index translation.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.infrastructure.reranker.litellm_reranker import LiteLLMReranker


def _rerank_response(pairs):
    """pairs = list of (index, relevance_score) as litellm.arerank returns."""
    return SimpleNamespace(
        results=[{"index": i, "relevance_score": s} for i, s in pairs]
    )


@pytest.mark.asyncio
async def test_score_maps_to_correct_chunk_with_empty_chunk_in_middle():
    reranker = LiteLLMReranker(model="cohere/rerank")
    # chunk 1 (index 1) has EMPTY content → dropped from passages.
    # passages = [A, C]; reranker indexes into THAT: 0→A, 1→C.
    chunks = [
        {"chunk_id": "A", "content": "alpha relevant text"},
        {"chunk_id": "B", "content": ""},                     # empty → filtered out
        {"chunk_id": "C", "content": "gamma most relevant text"},
    ]
    # reranker says passage index 1 (=C) is most relevant, passage 0 (=A) less.
    resp = _rerank_response([(1, 0.9), (0, 0.2)])

    with patch("litellm.arerank", AsyncMock(return_value=resp)):
        out = await reranker.rerank("q", chunks, top_n=2)

    by_id = {c["chunk_id"]: c["score"] for c in out}
    # The 0.9 must land on C (the actually-relevant chunk), NOT on B (the empty one
    # that would sit at original index 1). Pre-fix, chunks[1] = B got the 0.9.
    assert by_id.get("C") == pytest.approx(0.9), f"score misaligned: {by_id}"
    assert by_id.get("A") == pytest.approx(0.2)
    assert "B" not in by_id, "empty-content chunk must not receive a rerank score"
