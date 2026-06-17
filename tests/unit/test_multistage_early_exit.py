"""Multi-stage chain early-exit semantics.

The wrapper in ``query_graph.retrieve`` walks the configured stages and
stops on the first stage whose result contains a chunk with score >=
``DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD``. We re-implement the same
walker here against in-memory stages to assert the contract without
spinning up the full graph.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from ragbot.shared.constants import (
    DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD,
)


class _StubStage:
    """Stage returning a fixed list — used to script the chain."""

    def __init__(self, name: str, payload: list[dict[str, Any]]) -> None:
        self._name = name
        self._payload = payload
        self.called = False

    @property
    def stage_name(self) -> str:
        return self._name

    async def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float],
        record_bot_id,
        top_k: int,
        prior_stage_result: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.called = True
        return list(self._payload)


async def _run_chain(stages: list[_StubStage], threshold: float) -> list[dict]:
    """Tiny re-implementation of the wrapper's loop — mirrors query_graph."""
    chunks: list[dict] = []
    for stage in stages:
        out = await stage.retrieve(
            query="q",
            query_embedding=[],
            record_bot_id=uuid4(),
            top_k=10,
            prior_stage_result=chunks,
        )
        if out:
            existing_ids = {str(c.get("chunk_id") or "") for c in chunks}
            for c in out:
                cid = str(c.get("chunk_id") or "")
                if cid and cid not in existing_ids:
                    chunks.append(c)
                    existing_ids.add(cid)
        top = max((float(c.get("score", 0) or 0) for c in chunks), default=0.0)
        if chunks and top >= threshold:
            break
    return chunks


@pytest.mark.asyncio
async def test_chain_early_exits_on_first_high_score_stage() -> None:
    s1 = _StubStage("hybrid_stage1", [
        {"chunk_id": "a", "score": 0.9, "content": "alpha"},
    ])
    s2 = _StubStage("bm25_only_stage2", [
        {"chunk_id": "b", "score": 0.6, "content": "beta"},
    ])
    s3 = _StubStage("keyword_stage3", [
        {"chunk_id": "c", "score": 0.5, "content": "gamma"},
    ])
    out = await _run_chain([s1, s2, s3], DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD)
    assert s1.called is True
    assert s2.called is False
    assert s3.called is False
    assert len(out) == 1
    assert out[0]["chunk_id"] == "a"


@pytest.mark.asyncio
async def test_chain_walks_all_stages_when_below_threshold() -> None:
    # Each stage returns score < 0.35 threshold.
    s1 = _StubStage("hybrid_stage1", [
        {"chunk_id": "a", "score": 0.10, "content": "alpha"},
    ])
    s2 = _StubStage("bm25_only_stage2", [
        {"chunk_id": "b", "score": 0.20, "content": "beta"},
    ])
    s3 = _StubStage("keyword_stage3", [
        {"chunk_id": "c", "score": 0.25, "content": "gamma"},
    ])
    out = await _run_chain([s1, s2, s3], DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD)
    assert s1.called and s2.called and s3.called
    # All three accumulated.
    ids = {c["chunk_id"] for c in out}
    assert ids == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_chain_skips_zero_chunk_stage_continues() -> None:
    s1 = _StubStage("hybrid_stage1", [])  # zero chunks
    s2 = _StubStage("bm25_only_stage2", [
        {"chunk_id": "b", "score": 0.9, "content": "beta"},
    ])
    s3 = _StubStage("keyword_stage3", [
        {"chunk_id": "c", "score": 0.5, "content": "gamma"},
    ])
    out = await _run_chain([s1, s2, s3], DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD)
    assert s1.called
    assert s2.called
    # s2 already crossed threshold; s3 must NOT run.
    assert s3.called is False
    assert len(out) == 1
    assert out[0]["chunk_id"] == "b"


@pytest.mark.asyncio
async def test_chain_dedupes_chunks_across_stages() -> None:
    s1 = _StubStage("hybrid_stage1", [
        {"chunk_id": "shared", "score": 0.20, "content": "x"},
    ])
    s2 = _StubStage("bm25_only_stage2", [
        # Same chunk_id — must NOT be added twice.
        {"chunk_id": "shared", "score": 0.22, "content": "x"},
        {"chunk_id": "fresh", "score": 0.25, "content": "y"},
    ])
    out = await _run_chain([s1, s2], DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD)
    chunk_ids = [c["chunk_id"] for c in out]
    assert chunk_ids.count("shared") == 1
    assert "fresh" in chunk_ids


@pytest.mark.asyncio
async def test_chain_empty_when_all_stages_empty() -> None:
    s1 = _StubStage("hybrid_stage1", [])
    s2 = _StubStage("bm25_only_stage2", [])
    out = await _run_chain([s1, s2], DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD)
    assert out == []
    assert s1.called and s2.called
