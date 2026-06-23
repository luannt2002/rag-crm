"""Hygiene: LiteLLMReranker must tag enriched chunks with reranker_used.

Parity with the Jina / ZeroEntropy adapters, which stamp ``reranker_used``
onto each returned chunk so downstream telemetry can attribute the score to
the reranker that produced it.
"""

from __future__ import annotations

import pytest

import ragbot.infrastructure.reranker.litellm_reranker as litemod
from ragbot.infrastructure.reranker.litellm_reranker import LiteLLMReranker


class _FakeResponse:
    def __init__(self, results):
        self.results = results


@pytest.mark.asyncio
async def test_enriched_chunks_carry_reranker_used(monkeypatch) -> None:
    async def _fake_arerank(*, model, query, documents, top_n):
        return _FakeResponse(
            [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.4},
            ]
        )

    monkeypatch.setattr(litemod.litellm, "arerank", _fake_arerank)

    rr = LiteLLMReranker(model="cohere/rerank-v3.5")
    chunks = [
        {"chunk_id": "c1", "content": "alpha", "score": 0.02},
        {"chunk_id": "c2", "content": "beta", "score": 0.01},
    ]
    out = await rr.rerank("q", chunks, top_n=2)

    assert out, "reranker returned no chunks"
    for c in out:
        assert c.get("reranker_used") == rr.mode, (
            f"chunk missing reranker_used provenance tag: {c}"
        )
