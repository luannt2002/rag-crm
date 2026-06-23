"""Late Chunking — Jina-style context-aware embedding (practical approximation).

Since Ragbot embeds via cloud APIs (LiteLLM / OpenAI / Jina) with NO direct
access to token-level embeddings, we implement the practical adaptation:
prepend a document-level context prefix to each chunk before embedding so the
resulting vectors capture cross-chunk document context.

This module exposes two surfaces:

* ``late_chunk_embed`` — the original short-doc helper. Uses the document's
  first ``context_prefix_chars`` characters as a shared prefix for every
  chunk. Works well when the document fits comfortably inside the embedder's
  context window (Jina v3: ~8192 tokens / ~32768 chars).

* ``late_chunk_embed_sliding`` — the long-doc extension. Slides a window over
  the document with configurable overlap and gives each chunk a *local*
  context prefix drawn from the window that actually contains it, instead of
  forcing every chunk to share the same (often distant) document opening.

Proof citation:
* Jina AI — "Late Chunking" (Günther et al., 2024)
  Paper: https://arxiv.org/abs/2409.04701
  Benchmark: +24.47% average nDCG on BeIR vs naive per-chunk embedding.
* Ragbot adaptation: cloud-API embedders (OpenAI / Jina / Voyage) do not
  expose token-level embeddings, so we approximate the paper's "encode full
  doc once, pool per chunk" idea by injecting a *contextualised prefix*
  drawn from a sliding window that contains each chunk.

The character-based sliding boundaries are chosen because Ragbot does not
have a universal token decoder for every embedding provider; using characters
keeps the implementation provider-agnostic. ``DEFAULT_LATE_CHUNKING_WINDOW_
CHARS`` is sized to track Jina v3's 8192-token limit at the conservative
~4 chars/token ratio common for mixed VN/EN corpora.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

from ragbot.shared.constants import (
    DEFAULT_CHUNK_FINGERPRINT_CHARS,
    DEFAULT_EMBED_DOC_BATCH_SIZE,
    DEFAULT_LATE_CHUNKING_MAX_CHUNKS,
    DEFAULT_LATE_CHUNKING_OVERLAP_CHARS,
    DEFAULT_LATE_CHUNKING_WINDOW_CHARS,
)

if TYPE_CHECKING:
    from ragbot.application.ports.embedding_port import EmbeddingPort  # noqa: F401

logger = structlog.get_logger(__name__)


async def late_chunk_embed(
    chunks: list[str],
    document_summary: str,
    embedder: Any,
    *,
    context_prefix_chars: int = 200,
    embed_kwargs: dict[str, Any] | None = None,
    max_chunks_single_await: int = DEFAULT_LATE_CHUNKING_MAX_CHUNKS,
    doc_batch_size: int = DEFAULT_EMBED_DOC_BATCH_SIZE,
) -> list[list[float]]:
    """Embed chunks with document context prefix (practical late chunking).

    Prepends document summary to each chunk before embedding,
    so the embedding captures cross-chunk document context.

    When the chunk list exceeds ``max_chunks_single_await`` the contextualized
    list is embedded in ``doc_batch_size`` slices instead of one whole-doc
    ``embed_batch`` call, bounding the orchestrator-side memory peak for very
    large documents (e.g. a multi-table sheet → thousands of chunks). Order is
    preserved. ``max_chunks_single_await <= 0`` disables slicing.

    @param chunks: list of chunk texts to embed
    @param document_summary: full document text (first N chars used as prefix)
    @param embedder: EmbeddingPort-compatible embedder with embed_batch()
    @param context_prefix_chars: how many chars of document_summary to prepend
    @param embed_kwargs: additional kwargs passed to embedder.embed_batch()
    @param max_chunks_single_await: ceiling above which the batched path runs
    @param doc_batch_size: chunks per orchestrator-side ``embed_batch`` slice
    @return: list of embedding vectors, one per chunk
    """
    if not chunks:
        return []

    if document_summary is None:
        document_summary = ""

    # Build context prefix from document summary
    prefix = document_summary[:context_prefix_chars].strip()
    if prefix:
        prefix = f"[Document context: {prefix}]\n\n"

    # Prepend context to each chunk
    contextualized_chunks = [
        f"{prefix}{chunk}" if prefix else chunk
        for chunk in chunks
    ]

    kwargs = embed_kwargs or {}

    # Oversize doc → embed in doc_batch_size slices so the orchestrator never
    # holds the full contextualized list + all vectors in one await.
    if (
        max_chunks_single_await > 0
        and doc_batch_size > 0
        and len(contextualized_chunks) > max_chunks_single_await
    ):
        logger.info(
            "late_chunk_embed_batched",
            num_chunks=len(chunks),
            prefix_len=len(prefix),
            doc_batch_size=doc_batch_size,
        )
        vectors: list[list[float]] = []
        for start in range(0, len(contextualized_chunks), doc_batch_size):
            slice_ = contextualized_chunks[start:start + doc_batch_size]
            vectors.extend(await embedder.embed_batch(slice_, **kwargs))
        return vectors

    logger.debug(
        "late_chunk_embed",
        num_chunks=len(chunks),
        prefix_len=len(prefix),
        context_prefix_chars=context_prefix_chars,
    )

    return await embedder.embed_batch(contextualized_chunks, **kwargs)


def _build_windows(
    doc_len: int,
    window_chars: int,
    overlap_chars: int,
) -> list[tuple[int, int]]:
    """Compute sliding window [start, end) ranges over a document of ``doc_len`` chars.

    Stride = ``window_chars - overlap_chars``. Final window is clamped so it
    never extends beyond ``doc_len``. The function is pure / deterministic so
    the chunk → window mapping can be unit-tested without an embedder.
    """
    if doc_len <= 0 or window_chars <= 0:
        return []
    if overlap_chars < 0 or overlap_chars >= window_chars:
        # Bad config — degrade to a single window covering the whole doc rather
        # than infinite-loop or skip-everything. Caller logs the violation.
        return [(0, doc_len)]
    stride = window_chars - overlap_chars
    windows: list[tuple[int, int]] = []
    pos = 0
    while pos < doc_len:
        end = min(pos + window_chars, doc_len)
        windows.append((pos, end))
        if end >= doc_len:
            break
        pos += stride
    return windows


def _pick_window_for_chunk(
    chunk_start: int,
    chunk_end: int,
    windows: list[tuple[int, int]],
) -> tuple[int, int]:
    """Return the window with the largest overlap against [chunk_start, chunk_end).

    Falls back to the first window when no overlap is found (e.g. chunk could
    not be located in the document — defensive: still emits a vector instead
    of raising mid-batch).
    """
    if not windows:
        return (0, 0)
    best = windows[0]
    best_overlap = -1
    for w_start, w_end in windows:
        ov = min(chunk_end, w_end) - max(chunk_start, w_start)
        if ov > best_overlap:
            best_overlap = ov
            best = (w_start, w_end)
    return best


async def late_chunk_embed_sliding(
    chunks: list[str],
    document_text: str,
    embedder: Any,
    *,
    window_chars: int = DEFAULT_LATE_CHUNKING_WINDOW_CHARS,
    overlap_chars: int = DEFAULT_LATE_CHUNKING_OVERLAP_CHARS,
    context_prefix_chars: int = 200,
    embed_kwargs: dict[str, Any] | None = None,
) -> list[list[float]]:
    """Sliding-window variant of :func:`late_chunk_embed` for long documents.

    Strategy:
    1. Compute sliding windows ``[start, end)`` over ``document_text`` with
       ``window_chars`` size and ``overlap_chars`` overlap.
    2. For each chunk, locate it in the document (substring match on first
       ``DEFAULT_CHUNK_FINGERPRINT_CHARS`` characters) and pick the window
       with the largest overlap against the chunk position.
    3. Use the first ``context_prefix_chars`` of that local window as the
       chunk's context prefix.
    4. Embed the prefixed chunks in a single batched call.

    @param chunks: list of chunk texts to embed (order preserved on return)
    @param document_text: the full document text the chunks were derived from
    @param embedder: EmbeddingPort-compatible embedder with ``embed_batch``
    @param window_chars: sliding window size in characters
    @param overlap_chars: window overlap in characters (must be < window_chars)
    @param context_prefix_chars: prefix length carved from the local window
    @param embed_kwargs: forwarded verbatim to ``embedder.embed_batch``
    @return: one embedding vector per chunk, same order as ``chunks``

    Proof citation:
        Jina AI Late Chunking — https://arxiv.org/abs/2409.04701
        +24.47% nDCG on BeIR vs naive per-chunk embedding.
    """
    if not chunks:
        return []

    doc_text = document_text or ""
    doc_len = len(doc_text)

    t_start = time.perf_counter()

    # Short-doc fast path: full document fits in a single window, no sliding
    # benefit possible — fall back to the simple late chunking surface so we
    # don't pay for per-chunk window lookups.
    if doc_len <= window_chars:
        out = await late_chunk_embed(
            chunks=chunks,
            document_summary=doc_text,
            embedder=embedder,
            context_prefix_chars=context_prefix_chars,
            embed_kwargs=embed_kwargs,
        )
        logger.info(
            "late_chunking_sliding",
            step_name="late_chunking_sliding",
            feature_flag="late_chunking_sliding_enabled",
            doc_chars=doc_len,
            n_chunks=len(chunks),
            n_windows=1,
            mode="short_doc_fast_path",
            duration_ms=int((time.perf_counter() - t_start) * 1000),
        )
        return out

    windows = _build_windows(doc_len, window_chars, overlap_chars)

    # Cache the (prefix-text → trimmed prefix string) mapping so repeated
    # chunks that pick the same window don't recompute the slice + strip.
    window_prefix_cache: dict[tuple[int, int], str] = {}

    contextualized: list[str] = []
    # Sequential cursor: chunks usually arrive in document order, so we can
    # advance from the previous chunk's match position to keep substring
    # search O(doc_len) amortised across the batch instead of O(n_chunks ·
    # doc_len) which becomes pathological on 100K-char docs.
    cursor = 0
    fingerprint_len = DEFAULT_CHUNK_FINGERPRINT_CHARS
    for chunk in chunks:
        if not chunk:
            contextualized.append(chunk)
            continue
        probe = chunk[:fingerprint_len]
        idx = doc_text.find(probe, cursor)
        if idx < 0:
            # Out-of-order or fingerprint mismatch — scan from doc start once.
            idx = doc_text.find(probe)
        if idx >= 0:
            chunk_start = idx
            chunk_end = idx + len(chunk)
            cursor = idx  # next chunk likely starts at-or-after this one
        else:
            # Defensive fallback: pretend the chunk lives at the cursor so we
            # still emit one vector per chunk (caller's length invariant).
            chunk_start = cursor
            chunk_end = cursor + len(chunk)

        w = _pick_window_for_chunk(chunk_start, chunk_end, windows)
        if w not in window_prefix_cache:
            w_text = doc_text[w[0]:w[1]][:context_prefix_chars].strip()
            window_prefix_cache[w] = (
                f"[Document context: {w_text}]\n\n" if w_text else ""
            )
        prefix = window_prefix_cache[w]
        contextualized.append(f"{prefix}{chunk}" if prefix else chunk)

    kwargs = embed_kwargs or {}
    vectors = await embedder.embed_batch(contextualized, **kwargs)

    duration_ms = int((time.perf_counter() - t_start) * 1000)
    logger.info(
        "late_chunking_sliding",
        step_name="late_chunking_sliding",
        feature_flag="late_chunking_sliding_enabled",
        doc_chars=doc_len,
        n_chunks=len(chunks),
        n_windows=len(windows),
        window_chars=window_chars,
        overlap_chars=overlap_chars,
        mode="sliding",
        duration_ms=duration_ms,
    )
    return vectors


__all__ = [
    "_build_windows",
    "_pick_window_for_chunk",
    "late_chunk_embed",
    "late_chunk_embed_sliding",
]
