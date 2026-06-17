"""Unit tests for ``shared.late_chunking``.

Practical Late Chunking: prepend a document context prefix to each chunk
before embedding so cloud-API embeddings still capture cross-chunk context.

Pinned behaviors:
- Empty chunks input -> empty result, no embedder call.
- Chunks get the context prefix when ``document_summary`` is non-empty.
- Truncates context to ``context_prefix_chars`` characters.
- ``None`` document_summary is treated as empty (no prefix injected).
- ``embed_kwargs`` is forwarded verbatim to ``embedder.embed_batch``.
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.shared.late_chunking import late_chunk_embed


class _RecorderEmbedder:
    """Minimal stand-in for ``EmbeddingPort`` that just echoes inputs."""

    def __init__(self) -> None:
        self.last_inputs: list[str] | None = None
        self.last_kwargs: dict[str, Any] | None = None

    async def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.last_inputs = list(texts)
        self.last_kwargs = dict(kwargs)
        # Return one tiny vector per input — value carries the index for
        # easy assertions.
        return [[float(i), float(len(t))] for i, t in enumerate(texts)]


async def test_empty_chunks_returns_empty_without_calling_embedder() -> None:
    embedder = _RecorderEmbedder()
    out = await late_chunk_embed([], "summary", embedder)
    assert out == []
    assert embedder.last_inputs is None  # embedder NOT called


async def test_prefix_prepended_when_summary_present() -> None:
    embedder = _RecorderEmbedder()
    chunks = ["chunk one", "chunk two"]
    summary = "Document about VN tax law."

    out = await late_chunk_embed(chunks, summary, embedder)

    assert len(out) == 2
    assert embedder.last_inputs is not None
    for orig, sent in zip(chunks, embedder.last_inputs, strict=True):
        assert sent.startswith("[Document context:")
        assert sent.endswith(orig)


async def test_prefix_truncated_to_context_prefix_chars() -> None:
    embedder = _RecorderEmbedder()
    long_summary = "A" * 400
    out = await late_chunk_embed(
        ["chunk"],
        long_summary,
        embedder,
        context_prefix_chars=50,
    )
    assert len(out) == 1
    sent = embedder.last_inputs[0]
    # Prefix payload chars cannot exceed the cap.
    body_inside = sent.split("[Document context: ", 1)[1].split("]", 1)[0]
    assert len(body_inside) <= 50


async def test_none_summary_treated_as_empty_no_prefix() -> None:
    embedder = _RecorderEmbedder()
    out = await late_chunk_embed(["raw chunk"], None, embedder)  # type: ignore[arg-type]
    assert len(out) == 1
    # No prefix means the chunk reaches the embedder untouched.
    assert embedder.last_inputs == ["raw chunk"]


async def test_blank_summary_no_prefix() -> None:
    embedder = _RecorderEmbedder()
    out = await late_chunk_embed(["raw chunk"], "    ", embedder)
    assert len(out) == 1
    assert embedder.last_inputs == ["raw chunk"]


async def test_embed_kwargs_forwarded_to_embedder() -> None:
    embedder = _RecorderEmbedder()
    await late_chunk_embed(
        ["x"],
        "ctx",
        embedder,
        embed_kwargs={"task_type": "passage", "dim": 1024},
    )
    assert embedder.last_kwargs == {"task_type": "passage", "dim": 1024}


@pytest.mark.parametrize("prefix_chars", [50, 200, 500])
async def test_returns_one_vector_per_chunk(prefix_chars: int) -> None:
    embedder = _RecorderEmbedder()
    chunks = [f"chunk-{i}" for i in range(5)]
    out = await late_chunk_embed(
        chunks,
        "doc summary text",
        embedder,
        context_prefix_chars=prefix_chars,
    )
    assert len(out) == len(chunks)
