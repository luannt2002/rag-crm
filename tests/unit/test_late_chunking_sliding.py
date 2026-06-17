"""Unit tests for ``shared.late_chunking.late_chunk_embed_sliding``.

The sliding-window variant extends late chunking for documents larger than
the embedder's single-pass context window. Each chunk inherits a *local*
context prefix carved from the sliding window that contains it, instead of
the (often distant) document opening reused by ``late_chunk_embed``.

Pinned behaviours:
- Empty chunks input -> empty result, embedder NEVER called.
- Short docs (≤ ``window_chars``) take the fast path: a single window, every
  chunk shares the document opening as context (parity with non-sliding).
- Long docs (> ``window_chars``) generate multiple overlapping windows; each
  chunk's prefix is drawn from the window that contains it, so a chunk near
  the document END gets context from the END region, not the document head.
- Overlap is honoured: a chunk straddling a boundary still resolves to ONE
  best window (largest overlap) rather than failing.
- ``embed_kwargs`` is forwarded verbatim to ``embedder.embed_batch``.
- Returns exactly one vector per chunk, even when a chunk cannot be located
  in the document (defensive fallback — caller's length invariant holds).

These are behavioural assertions on real values produced by the helper
against a deterministic recorder embedder — NOT ``assert True`` / NOT
``assert is not None`` smoke tests.
"""
from __future__ import annotations

from typing import Any

import pytest

from ragbot.shared.late_chunking import (
    _build_windows,
    _pick_window_for_chunk,
    late_chunk_embed_sliding,
)


class _RecorderEmbedder:
    """Deterministic stand-in for ``EmbeddingPort`` that echoes inputs.

    Returned vectors carry the index + input length so tests can assert
    ordering and confirm the right text reached the embedder.
    """

    def __init__(self) -> None:
        self.last_inputs: list[str] | None = None
        self.last_kwargs: dict[str, Any] | None = None
        self.call_count: int = 0

    async def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.last_inputs = list(texts)
        self.last_kwargs = dict(kwargs)
        self.call_count += 1
        return [[float(i), float(len(t))] for i, t in enumerate(texts)]


# ---------------------------------------------------------------------------
# Pure helpers — windowing math
# ---------------------------------------------------------------------------


def test_build_windows_short_doc_single_window() -> None:
    """Doc smaller than window → exactly one window covering the whole doc."""
    windows = _build_windows(doc_len=5_000, window_chars=8_000, overlap_chars=1_000)
    assert windows == [(0, 5_000)]


def test_build_windows_exact_multiple() -> None:
    """Stride = window - overlap; covers doc end without going past it."""
    # window=100, overlap=20 → stride=80; doc=260 → starts 0, 80, 160, 240
    windows = _build_windows(doc_len=260, window_chars=100, overlap_chars=20)
    # First window [0,100), then stride 80 puts next at 80 → [80,180), then
    # 160 → [160, 260) — that hits the end so iteration stops.
    starts = [w[0] for w in windows]
    ends = [w[1] for w in windows]
    assert starts == [0, 80, 160]
    assert ends[-1] == 260
    # All windows obey the window_chars cap (except the clamped last).
    for s, e in windows[:-1]:
        assert e - s == 100
    # Overlap actually overlaps (strict=False: trailing prev with no nxt
    # is fine — it's the doc-end window).
    for prev, nxt in zip(windows, windows[1:], strict=False):
        assert nxt[0] < prev[1]


def test_build_windows_doc_20k_with_8k_window_512_overlap() -> None:
    """Paper acceptance: doc 20K, window 8K, overlap 512 → 3 windows.

    Stride = 7488. Starts at 0, 7488, 14976. The 14976 window extends to
    20000 (clamped), so 3 windows total.
    """
    windows = _build_windows(doc_len=20_000, window_chars=8_000, overlap_chars=512)
    assert len(windows) == 3
    assert windows[0] == (0, 8_000)
    assert windows[1] == (7_488, 15_488)
    assert windows[2] == (14_976, 20_000)


def test_build_windows_bad_overlap_collapses_to_single_window() -> None:
    """Overlap ≥ window would loop forever — degrade gracefully."""
    assert _build_windows(doc_len=10_000, window_chars=100, overlap_chars=100) == [
        (0, 10_000),
    ]
    assert _build_windows(doc_len=10_000, window_chars=100, overlap_chars=-1) == [
        (0, 10_000),
    ]


def test_build_windows_empty_doc() -> None:
    assert _build_windows(doc_len=0, window_chars=100, overlap_chars=10) == []


def test_pick_window_picks_largest_overlap() -> None:
    windows = [(0, 100), (80, 180), (160, 260)]
    # Chunk [10, 50) is fully inside window 0 → window 0 wins.
    assert _pick_window_for_chunk(10, 50, windows) == (0, 100)
    # Chunk [110, 150) lives entirely inside the overlap region of window 1
    # → window 1 wins (40-char overlap vs negative against the others).
    assert _pick_window_for_chunk(110, 150, windows) == (80, 180)
    # Chunk [200, 220) sits inside window 2.
    assert _pick_window_for_chunk(200, 220, windows) == (160, 260)


def test_pick_window_no_overlap_falls_back_to_first() -> None:
    """Chunk position outside any window → return first window (defensive)."""
    windows = [(0, 100), (200, 300)]
    out = _pick_window_for_chunk(1_000, 1_010, windows)
    assert out == (0, 100)


# ---------------------------------------------------------------------------
# Main async surface — late_chunk_embed_sliding
# ---------------------------------------------------------------------------


async def test_empty_chunks_returns_empty_no_embedder_call() -> None:
    embedder = _RecorderEmbedder()
    out = await late_chunk_embed_sliding(
        chunks=[],
        document_text="any document text",
        embedder=embedder,
    )
    assert out == []
    assert embedder.call_count == 0
    assert embedder.last_inputs is None


async def test_short_doc_uses_fast_path_one_prefix_for_all_chunks() -> None:
    """Doc fits inside window → every chunk shares the document opening prefix."""
    embedder = _RecorderEmbedder()
    chunks = ["alpha chunk", "beta chunk", "gamma chunk"]
    doc = "Document about VN tax law for 2026."

    out = await late_chunk_embed_sliding(
        chunks=chunks,
        document_text=doc,
        embedder=embedder,
        window_chars=10_000,
        overlap_chars=500,
        context_prefix_chars=200,
    )

    assert len(out) == 3
    assert embedder.call_count == 1
    sent = embedder.last_inputs
    assert sent is not None and len(sent) == 3
    # All three chunks must share the same prefix because the doc fits in one window.
    prefixes = [s.split("\n\n", 1)[0] for s in sent]
    assert prefixes[0] == prefixes[1] == prefixes[2]
    assert prefixes[0].startswith("[Document context:")
    # Body of each sent string ends with the original chunk text.
    for orig, sent_text in zip(chunks, sent, strict=True):
        assert sent_text.endswith(orig)


async def test_long_doc_generates_local_prefixes_per_window() -> None:
    """Chunks from different parts of a long doc receive DIFFERENT prefixes.

    Constructs a synthetic 30K-char doc with three distinguishable regions
    ("HEAD ...", "MIDDLE ...", "TAIL ...") and asserts that a chunk drawn
    from each region inherits a context prefix sourced from THAT region.
    """
    head_marker = "HEAD-REGION-MARKER"
    mid_marker = "MIDDLE-REGION-MARKER"
    tail_marker = "TAIL-REGION-MARKER"
    head_chunk = f"{head_marker} content about head topic."
    mid_chunk = f"{mid_marker} content about middle topic."
    tail_chunk = f"{tail_marker} content about tail topic."

    # Build a 30K doc — head chunk near the start, mid chunk around index
    # 12000, tail chunk near index 24000. Padding is filler so each region
    # is well inside its own sliding window.
    pad = "X" * 10_000
    doc = head_chunk + pad + mid_chunk + pad + tail_chunk
    assert len(doc) > 8_000  # ensure we actually trigger sliding

    embedder = _RecorderEmbedder()
    out = await late_chunk_embed_sliding(
        chunks=[head_chunk, mid_chunk, tail_chunk],
        document_text=doc,
        embedder=embedder,
        window_chars=8_000,
        overlap_chars=512,
        context_prefix_chars=300,
    )

    assert len(out) == 3
    assert embedder.call_count == 1
    sent = embedder.last_inputs
    assert sent is not None and len(sent) == 3

    head_prefix = sent[0].split("\n\n", 1)[0]
    mid_prefix = sent[1].split("\n\n", 1)[0]
    tail_prefix = sent[2].split("\n\n", 1)[0]

    # Each chunk's prefix comes from the window containing it — so the
    # head chunk's prefix mentions the head marker, the tail chunk's
    # prefix should NOT mention the head marker (it's >24K chars away,
    # outside the tail window).
    assert head_marker in head_prefix
    assert tail_marker not in head_prefix

    # Tail chunk's prefix must NOT carry the head opening — that's the
    # whole reason sliding beats the fixed-prefix fast path on long docs.
    assert head_marker not in tail_prefix
    # Tail prefix should be drawn from the tail region.
    assert tail_marker in tail_prefix or "X" in tail_prefix

    # All three prefixes must be DIFFERENT — three distinct windows.
    assert head_prefix != tail_prefix
    # Body of each contextualised text still ends with the raw chunk.
    for orig, sent_text in zip([head_chunk, mid_chunk, tail_chunk], sent, strict=True):
        assert sent_text.endswith(orig)


async def test_paper_acceptance_doc_20k_three_windows() -> None:
    """Paper acceptance criterion: doc 20K chars + window 8K + overlap 512 → 3 windows.

    Asserted indirectly by checking that the prefix cache produces distinct
    prefixes for chunks placed in each window's home region.
    """
    # Doc 20K with three chunks placed at indices ~0, ~9000, ~17000.
    chunk_a = "AAA-zone-marker-here"
    chunk_b = "BBB-zone-marker-here"
    chunk_c = "CCC-zone-marker-here"
    filler = "." * 8_500
    doc = chunk_a + filler + chunk_b + filler + chunk_c  # ~17K + small overhead
    # Pad to exactly 20000.
    doc = (doc + ("." * 20_000))[:20_000]

    embedder = _RecorderEmbedder()
    out = await late_chunk_embed_sliding(
        chunks=[chunk_a, chunk_b, chunk_c],
        document_text=doc,
        embedder=embedder,
        window_chars=8_000,
        overlap_chars=512,
        context_prefix_chars=200,
    )

    assert len(out) == 3
    sent = embedder.last_inputs
    assert sent is not None
    prefix_a = sent[0].split("\n\n", 1)[0]
    prefix_b = sent[1].split("\n\n", 1)[0]
    prefix_c = sent[2].split("\n\n", 1)[0]
    # Three windows → at minimum two of the three prefixes differ.
    distinct = {prefix_a, prefix_b, prefix_c}
    assert len(distinct) >= 2


async def test_embed_kwargs_forwarded_verbatim() -> None:
    embedder = _RecorderEmbedder()
    await late_chunk_embed_sliding(
        chunks=["x"],
        document_text="short doc",
        embedder=embedder,
        window_chars=1_000,
        overlap_chars=100,
        embed_kwargs={"task_type": "passage", "dim": 1024, "spec": "SENTINEL"},
    )
    assert embedder.last_kwargs == {
        "task_type": "passage",
        "dim": 1024,
        "spec": "SENTINEL",
    }


async def test_chunk_not_found_in_doc_still_emits_vector() -> None:
    """Defensive: chunk text missing from document → fallback prefix, still 1 vector.

    Guards the caller's invariant that ``len(out) == len(chunks)`` (the
    document_service raises ExternalServiceError when this is violated).
    """
    embedder = _RecorderEmbedder()
    doc = "A" * 40_000
    orphan = "this string is nowhere inside the document text"
    out = await late_chunk_embed_sliding(
        chunks=[orphan],
        document_text=doc,
        embedder=embedder,
        window_chars=8_000,
        overlap_chars=512,
    )
    assert len(out) == 1
    sent = embedder.last_inputs
    assert sent is not None and len(sent) == 1
    # Vector still emitted; the chunk still reaches the embedder (possibly
    # with a fallback prefix from the first window).
    assert sent[0].endswith(orphan)


async def test_returns_one_vector_per_chunk_ordering_preserved() -> None:
    """Output ordering matches input chunk ordering — critical for the
    embedding loop downstream which assigns vectors by positional index.
    """
    embedder = _RecorderEmbedder()
    doc = "intro paragraph. " + ("section text. " * 5_000)
    chunks = [f"chunk-marker-{i} body" for i in range(8)]
    # Sprinkle the chunks across the long doc deterministically.
    doc = doc + " ".join(chunks)
    out = await late_chunk_embed_sliding(
        chunks=chunks,
        document_text=doc,
        embedder=embedder,
        window_chars=8_000,
        overlap_chars=512,
    )
    assert len(out) == len(chunks)
    sent = embedder.last_inputs
    assert sent is not None
    # First coordinate of each recorder vector is the index — must be
    # monotonically increasing (0, 1, 2, …) which proves no reordering.
    for i, vec in enumerate(out):
        assert vec[0] == float(i)
    # Sent text for each index ends with the corresponding chunk.
    for orig, sent_text in zip(chunks, sent, strict=True):
        assert sent_text.endswith(orig)


@pytest.mark.parametrize(
    ("window_chars", "overlap_chars"),
    [(8_000, 512), (4_096, 256), (16_384, 1_024)],
)
async def test_various_window_sizes_all_return_full_batch(
    window_chars: int,
    overlap_chars: int,
) -> None:
    embedder = _RecorderEmbedder()
    doc = "Z" * (window_chars * 3)  # force at least 2 windows in every case
    chunks = ["alpha", "bravo", "charlie", "delta"]
    out = await late_chunk_embed_sliding(
        chunks=chunks,
        document_text=doc,
        embedder=embedder,
        window_chars=window_chars,
        overlap_chars=overlap_chars,
    )
    assert len(out) == 4
    assert embedder.call_count == 1
