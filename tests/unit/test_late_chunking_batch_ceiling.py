"""A-I4 — late_chunk_embed slices oversize chunk lists into batches."""

from __future__ import annotations

import math

import pytest

from ragbot.shared.late_chunking import late_chunk_embed


class _CountingEmbedder:
    """Records each embed_batch call's input size; returns 1 vector per text."""

    def __init__(self) -> None:
        self.call_sizes: list[int] = []

    async def embed_batch(self, texts: list[str], **_: object) -> list[list[float]]:
        self.call_sizes.append(len(texts))
        return [[float(len(t))] for t in texts]


@pytest.mark.asyncio
async def test_oversize_doc_embeds_in_ceil_n_over_batch_calls() -> None:
    n, batch, ceiling = 250, 100, 50
    chunks = [f"chunk-{i}" for i in range(n)]
    emb = _CountingEmbedder()

    out = await late_chunk_embed(
        chunks,
        document_summary="ctx",
        embedder=emb,
        max_chunks_single_await=ceiling,
        doc_batch_size=batch,
    )

    assert len(out) == n  # one vector per chunk, order preserved
    assert len(emb.call_sizes) == math.ceil(n / batch) == 3
    assert emb.call_sizes == [100, 100, 50]


@pytest.mark.asyncio
async def test_small_doc_stays_single_await() -> None:
    chunks = [f"c-{i}" for i in range(10)]
    emb = _CountingEmbedder()

    out = await late_chunk_embed(
        chunks,
        document_summary="ctx",
        embedder=emb,
        max_chunks_single_await=50,
        doc_batch_size=100,
    )

    assert len(out) == 10
    assert len(emb.call_sizes) == 1  # single whole-doc await below the ceiling


@pytest.mark.asyncio
async def test_slicing_preserves_order() -> None:
    chunks = [f"x{i}" for i in range(7)]
    emb = _CountingEmbedder()
    out = await late_chunk_embed(
        chunks,
        document_summary="",
        embedder=emb,
        max_chunks_single_await=2,
        doc_batch_size=3,
    )
    # Vector encodes len(text); with empty prefix the text == chunk, so the
    # returned order must match the input chunk order exactly.
    assert [v[0] for v in out] == [float(len(c)) for c in chunks]
    assert len(emb.call_sizes) == math.ceil(7 / 3) == 3
