"""Narrate-then-Embed application service.

Narrate-then-Embed for TABLE / FORMULA / IMAGE chunks.

Proof citation:
    - Anthropic Contextual Retrieval (Sep 2024,
      https://www.anthropic.com/news/contextual-retrieval) reports a
      **-49% retrieval failure rate** when chunks are augmented with
      LLM-generated context BEFORE embedding.
    - RAG-Anything (HKUDS) extends the same insight to non-prose blocks:
      LLM linearisation of tables before embedding improves QA accuracy
      on table-heavy corpora.
    - AdapChunk Layer 7 internal blueprint (PhD thesis, private).

What this service does:
    Given a block ``content`` + ``block_type``, it asks the configured
    ``NarrateServicePort`` to produce a 1-2 sentence natural-language
    description. The service stores BOTH the narrated text (for embedding)
    AND the raw block content (for downstream LLM consumption) so the
    retrieval index can match on the natural-language vector while the
    LLM still answers from the source of truth — HALLU=0 sacred.

What this service does NOT do:
    - It does NOT call any LLM directly — that is the adapter's job
      (``LLMNarrateGenerator`` in ``infrastructure/narrate/``).
    - It does NOT inject any text into the answer LLM prompt — narration
      is an INGEST-side enhancement; query-time generation is untouched.
    - It does NOT override LLM answers — see Quality Gate #10.

Feature flag: ``narrate_then_embed_enabled`` (default False). When OFF,
the DI container binds ``NullNarrateGenerator`` which is an identity
function — callers wire ``await narrate.narrate(c, t)`` unconditionally
and pay zero LLM cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from ragbot.application.ports.narrate_port import NarrateServicePort
from ragbot.shared.constants import (
    NARRATE_BLOCK_TYPES_DEFAULT,
    NARRATE_METADATA_KEY_BLOCK_TYPE,
    NARRATE_METADATA_KEY_NARRATED_TEXT,
    NARRATE_METADATA_KEY_RAW_CHUNK,
)
from ragbot.shared.types import BlockType

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NarrateResult:
    """Outcome of one narration call.

    @param text_for_embedding: the text the embedder will see — either
        the LLM-narrated description (when narration succeeded for a
        supported block type) or the raw ``content`` (default OFF,
        unsupported block type, or LLM failure — HALLU=0 fallback).
    @param raw_chunk: the original block content, preserved verbatim
        for downstream LLM consumption. Stored in chunk metadata under
        ``NARRATE_METADATA_KEY_RAW_CHUNK``.
    @param narrated_text: the LLM-narrated text (or the raw content when
        narration was bypassed / failed). Stored under
        ``NARRATE_METADATA_KEY_NARRATED_TEXT``. We persist it even when
        identical to ``raw_chunk`` so the retrieval layer has a single
        consistent key to read.
    @param block_type: the classifier label echoed back for metadata.
    @param narrated: True only when an actual LLM call produced new text;
        False on bypass / failure / unsupported block type. Used by
        callers / telemetry to distinguish "embed enriched" vs "embed raw".
    """

    text_for_embedding: str
    raw_chunk: str
    narrated_text: str
    block_type: BlockType
    narrated: bool


class NarrateService:
    """Application-level coordinator for Narrate-then-Embed.

    Wraps a ``NarrateServicePort`` strategy with the dual-content
    persistence policy: regardless of whether narration ran, the result
    carries BOTH the embedding text and the raw block content so the
    ingest pipeline can persist them together in ``metadata_json``.

    @param strategy: the injected Narrate port (Null or LLM).
    @param enabled: feature flag. When False (the platform default), the
        service short-circuits to identity behaviour and never invokes
        the strategy. The flag is also honoured at the DI container so
        callers can safely call ``narrate(...)`` unconditionally.
    @param eligible_block_types: which block_type labels trigger narration
        when ``enabled`` is True. Defaults to ``NARRATE_BLOCK_TYPES_DEFAULT``
        (``("TABLE", "FORMULA", "IMAGE")``) — prose-like blocks
        (HEADING / TEXT / CODE / LIST) embed fine raw.
    """

    def __init__(
        self,
        *,
        strategy: NarrateServicePort,
        enabled: bool,
        eligible_block_types: tuple[BlockType, ...] = NARRATE_BLOCK_TYPES_DEFAULT,
    ) -> None:
        self._strategy = strategy
        self._enabled = bool(enabled)
        self._eligible = tuple(eligible_block_types)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def narrate_chunk(
        self,
        content: str,
        block_type: BlockType,
    ) -> NarrateResult:
        """Linearise ``content`` of ``block_type`` for embedding.

        Always returns a ``NarrateResult`` with both ``text_for_embedding``
        and ``raw_chunk`` populated — the ingest pipeline can persist
        them together via :meth:`to_metadata`.

        Decision matrix:
            - feature flag OFF                         → raw, narrated=False
            - block_type not in eligible_block_types   → raw, narrated=False
            - empty / whitespace content               → raw, narrated=False
            - strategy raised / returned empty         → raw, narrated=False
            - strategy returned unchanged content      → raw, narrated=False
            - strategy returned new text               → narrated, narrated=True
        """
        raw = content or ""

        if not self._enabled:
            return NarrateResult(
                text_for_embedding=raw,
                raw_chunk=raw,
                narrated_text=raw,
                block_type=block_type,
                narrated=False,
            )

        if block_type not in self._eligible:
            return NarrateResult(
                text_for_embedding=raw,
                raw_chunk=raw,
                narrated_text=raw,
                block_type=block_type,
                narrated=False,
            )

        if not raw.strip():
            return NarrateResult(
                text_for_embedding=raw,
                raw_chunk=raw,
                narrated_text=raw,
                block_type=block_type,
                narrated=False,
            )

        narrated_text = await self._strategy.narrate(raw, block_type)
        # Strategy contract guarantees a string — but be defensive: if it
        # ever returns None or empty for a non-empty input we treat that
        # as "no enhancement" and fall back to raw (HALLU=0 — never embed
        # empty / fabricated text in place of real content).
        narrated_clean = (narrated_text or "").strip()
        if not narrated_clean or narrated_clean == raw.strip():
            return NarrateResult(
                text_for_embedding=raw,
                raw_chunk=raw,
                narrated_text=raw,
                block_type=block_type,
                narrated=False,
            )

        logger.debug(
            "narrate_service_enriched",
            block_type=block_type,
            raw_chars=len(raw),
            narrated_chars=len(narrated_clean),
            step_name=f"narrate_{block_type.lower()}",
            feature_flag="narrate_then_embed_enabled",
        )

        return NarrateResult(
            text_for_embedding=narrated_clean,
            raw_chunk=raw,
            narrated_text=narrated_clean,
            block_type=block_type,
            narrated=True,
        )

    @staticmethod
    def to_metadata(result: NarrateResult) -> dict[str, Any]:
        """Render a ``NarrateResult`` as a chunk-metadata dict.

        Keys live in ``shared.constants`` so downstream readers (retrieval,
        eval, admin debug tooling) share a single source of truth and
        callers cannot drift them by typo.
        """
        return {
            NARRATE_METADATA_KEY_RAW_CHUNK: result.raw_chunk,
            NARRATE_METADATA_KEY_NARRATED_TEXT: result.narrated_text,
            NARRATE_METADATA_KEY_BLOCK_TYPE: result.block_type,
        }


__all__ = ["NarrateResult", "NarrateService"]
