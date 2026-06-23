"""Narrate-then-Embed dispatch helpers (AdapChunk Layer 7).

Goal:
    Between chunking and embedding, route each chunk to the
    block-type-aware ``NarrateService`` so non-prose blocks (TABLE /
    FORMULA / IMAGE) get linearised into 1-2 natural-language sentences
    BEFORE the dense encoder sees them. Embed-target text is rewritten
    in place; the source-of-truth raw chunk is preserved in chunk
    metadata so citation + LLM answers still pull from the original
    block (HALLU=0 — never embed a substitute without keeping the
    original for downstream retrieval reconstruction).

Why a dedicated module:
    The helpers are pure (no DB, no LLM, no heavy imports) and are
    consumed by exactly one site — ``DocumentService.ingest()``. Living
    in their own file keeps that site's import surface small and lets
    the unit tests exercise the helpers WITHOUT pulling the full ingest
    module — important because ``document_service.py`` transitively
    imports infra packages whose own import drift can mask real
    regressions in the dispatch wiring.

Graceful degradation:
    - ``narrate_service is None`` → identity passthrough (the legacy
      pre-narrate ingest path); zero behaviour change for tenants that
      have not opted in.
    - Feature flag OFF inside the service → also passthrough on the
      embed side; metadata still emitted so admin can introspect the
      block_type distribution offline.

Proof citation:
    - Anthropic Contextual Retrieval (Sep 2024,
      https://www.anthropic.com/news/contextual-retrieval) reports a
      **-49% retrieval failure rate** when chunks are augmented with
      natural-language context BEFORE embedding.
    - RAG-Anything (HKUDS) extends the same insight to non-prose blocks:
      LLM linearisation of tables / formulas / images before embedding
      improves QA accuracy on heterogeneous corpora.
    - AdapChunk Layer 7 internal blueprint (PhD thesis, private).
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from ragbot.application.services.narrate_service import NarrateService
from ragbot.shared.chunking import _split_into_blocks_with_atomic
from ragbot.shared.constants import (
    DEFAULT_LANGUAGE,
    DEFAULT_NARRATE_MAX_CONCURRENCY,
    NARRATE_METADATA_KEY_BLOCK_TYPE,
    NARRATE_METADATA_KEY_NARRATED_TEXT,
    NARRATE_METADATA_KEY_RAW_CHUNK,
)
from ragbot.shared.types import BlockType


# Map ``_split_into_blocks_with_atomic`` lowercase labels to the uppercase
# ``BlockType`` literal used by ``NarrateService``. Kept module-level so
# the constant table travels with the helper and doesn't drift into a
# function body where a future refactor could re-introduce string literals.
_BLOCK_TYPE_LABEL_MAP: Final[dict[str, BlockType]] = {
    "table": "TABLE",
    "formula": "FORMULA",
    "image": "IMAGE",
    "code": "CODE",
    "text": "TEXT",
}


def classify_chunk_block_type(text: str) -> BlockType:
    """Return the dominant ``BlockType`` label for ``text``.

    Reuses ``_split_into_blocks_with_atomic`` (the same heuristic the
    chunker already uses to keep tables / formulas / images / code
    atomic) so classification and chunking agree on what counts as a
    non-prose block. The dominant block-type is the one whose total
    content occupies the most characters inside the chunk; if the
    chunk contains a mix (e.g. table preceded by a one-line caption)
    the bulkier block wins so the narrate strategy routes to the
    correct prompt.

    Empty / whitespace-only chunks classify as ``"TEXT"`` so the
    narrate service short-circuits without an LLM hop.

    @param text: raw chunk text.
    @return: uppercase ``BlockType`` literal — one of
        ``"TABLE"`` / ``"FORMULA"`` / ``"IMAGE"`` / ``"CODE"`` / ``"TEXT"``.
        ``"HEADING"`` / ``"LIST"`` are not produced because the underlying
        splitter doesn't classify those (they live inside ``text``);
        both are prose-friendly and the narrate service treats them as
        TEXT anyway, so collapsing them is loss-less for the embed path.
    """
    if not text or not text.strip():
        return "TEXT"

    blocks = _split_into_blocks_with_atomic(text)
    if not blocks:
        return "TEXT"

    # Dominant block: largest content by character count.
    dominant_label, _ = max(
        blocks, key=lambda btype_content: len(btype_content[1]),
    )
    return _BLOCK_TYPE_LABEL_MAP.get(dominant_label, "TEXT")


async def narrate_chunks_for_embed(
    texts: list[str],
    *,
    narrate_service: NarrateService | None,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[list[str], list[dict[str, Any] | None]]:
    """Pre-embed dispatch — route each chunk through ``NarrateService``.

    For each chunk we:
        1. Classify its dominant block-type via :func:`classify_chunk_block_type`.
        2. Call ``narrate_service.narrate_chunk(text, block_type)``.
        3. Take the strategy's ``text_for_embedding`` as the new embed-target.
        4. Return per-chunk metadata (raw_chunk / narrated_text / block_type)
           so the caller can persist it into ``document_chunks.metadata_json``.

    Graceful-degradation contract:
        - ``narrate_service is None`` → return ``texts`` verbatim + all-None
          metadata. The caller embeds exactly the same bytes it would have
          embedded before the E3 wire existed.
        - Feature flag OFF inside ``NarrateService`` → embed-target text
          stays identical to ``raw_chunk`` BUT metadata is still emitted
          so the persist path can record block_type for offline analysis
          (the ``narrated`` flag in ``NarrateResult`` distinguishes
          "actually enriched" vs "passthrough").

    HALLU=0: when narration fails the strategy returns the raw content
    (see ``LLMNarrateGenerator`` degrade-silent path), so the embed-target
    text always reflects real source content — we never substitute an
    empty / fabricated string.

    @param texts: chunk texts in the order they will be embedded.
    @param narrate_service: injected ``NarrateService`` or ``None``.
    @return: tuple ``(rewritten_texts, per_chunk_metadata)`` — both lists
        have the same length as ``texts``. ``rewritten_texts[i]`` is what
        the embedder should see; ``per_chunk_metadata[i]`` is ``None`` when
        no service was wired, otherwise a dict with the
        ``NARRATE_METADATA_KEY_*`` keys.
    """
    if narrate_service is None:
        return list(texts), [None] * len(texts)

    # Per-chunk narration is independent (no cross-chunk data dep) — fan out
    # with bounded concurrency instead of a serial ``for await`` loop. gather
    # preserves input order, so rewritten[i]/metadata[i] still align with
    # texts[i]. narrate_chunk degrades silently (returns raw content) on
    # failure, so no per-task exception guard is needed (HALLU=0 preserved).
    _sem = asyncio.Semaphore(DEFAULT_NARRATE_MAX_CONCURRENCY)

    async def _one(chunk_text: str) -> tuple[str, dict[str, Any] | None]:
        block_type = classify_chunk_block_type(chunk_text)
        async with _sem:
            result = await narrate_service.narrate_chunk(
                chunk_text, block_type, language=language,
            )
        return result.text_for_embedding, {
            NARRATE_METADATA_KEY_RAW_CHUNK: result.raw_chunk,
            NARRATE_METADATA_KEY_NARRATED_TEXT: result.narrated_text,
            NARRATE_METADATA_KEY_BLOCK_TYPE: result.block_type,
        }

    pairs = await asyncio.gather(*[_one(t) for t in texts])
    rewritten: list[str] = [p[0] for p in pairs]
    metadata: list[dict[str, Any] | None] = [p[1] for p in pairs]
    return rewritten, metadata


__all__ = [
    "classify_chunk_block_type",
    "narrate_chunks_for_embed",
]
