"""[T1-Smartness] ChunkContextEnricher — Anthropic CR storage-column path.

Anthropic Contextual Retrieval (Sep 2024 — reports -49% retrieval failure
when chunks are augmented with LLM-generated context before embedding).

This service produces ONE situated-context label per chunk for storage in
the dedicated ``document_chunks.chunk_context`` column (alembic 010l). The
column is consumed by the hybrid retrieval path (BM25 over ``content`` +
``chunk_context`` GIN-trgm index) so the LLM-derived context boosts recall
without touching the embedded text.

Why this service differs from ``contextual_chunk_enrichment``:

* The legacy module wraps the context inline into ``content`` so embedding
  picks it up; that couples context + chunk into one searchable string.
* This module returns the context string as a SEPARATE first-class value
  so the caller can persist it into the dedicated column and the hybrid
  retrieval path can score the two signals independently. The embedded
  text stays the raw chunk — no double-wrapping.

DI / Strategy pattern (CLAUDE.md sacred rule):

* The enricher consumes a ``ChunkContextProviderPort`` Protocol — any
  adapter implementing ``async def generate(doc, chunks) -> list[str]``
  satisfies the contract.
* The default platform adapter wraps the existing
  ``AnthropicHaikuBatchClientPort`` (``infrastructure/llm/anthropic_haiku_batch.py``)
  so we ride the 50% Batch API discount + prompt cache on the full
  document as a cached prefix.
* Unit tests inject a stub provider — NO real Anthropic API call.

Storage-only guarantee (Quality Gate #10):

The output of this service flows into the DB column ``chunk_context`` and
the hybrid retrieval BM25 path. The application NEVER prepends the
context to the LLM answer prompt — the bot owner's ``system_prompt`` is
the only LLM-side input the platform composes.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import structlog

from ragbot.shared.constants import (
    DEFAULT_CHUNK_CONTEXT_MAX_TOKENS,
    DEFAULT_CR_MAX_DOC_CHARS,
)

logger = structlog.get_logger(__name__)


# Hard storage cap matching ``alembic/versions/20260520_010l_chunk_context.py``
# VARCHAR(1024). Service truncates + warns rather than failing ingest — the
# context is a retrieval signal, not an answer-side input, so degradation is
# safe (HALLU=0 unaffected).
_CHUNK_CONTEXT_STORAGE_LIMIT_CHARS = 1024


@runtime_checkable
class ChunkContextProviderPort(Protocol):
    """Contract for the LLM provider that generates per-chunk context labels.

    Implementations wrap a concrete LLM client (e.g. Anthropic Haiku Batch
    with prompt-cached document prefix). The enricher depends only on this
    Protocol so swapping providers needs zero edits to the orchestration
    layer (CLAUDE.md Strategy + DI rule).

    @param doc_full_text: parent document text — implementations route this
        as a cached prefix to amortise input cost across the chunk batch.
    @param chunks: list of leaf-chunk strings to label.
    @param max_context_tokens: hard cap on per-chunk output budget.
    @return: list with same length as ``chunks``. Position i is the
        generated context for ``chunks[i]``; empty string signals the
        per-chunk request failed and the caller should fall back to NULL
        in the storage column (column is nullable by design).
    """

    async def generate(
        self,
        *,
        doc_full_text: str,
        chunks: Sequence[str],
        max_context_tokens: int,
    ) -> list[str]: ...


class NullChunkContextProvider:
    """Default OFF — returns empty contexts for every chunk.

    Used when no real provider is wired in DI (bootstrap default), or when
    the operator wants to keep the column NULL until they explicitly opt
    a bot in via ``plan_limits.cr_enhanced_enabled``. Records the call
    shape in structlog so ops can confirm wiring without paying for the
    paid API.
    """

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    async def generate(
        self,
        *,
        doc_full_text: str,
        chunks: Sequence[str],
        max_context_tokens: int,
    ) -> list[str]:
        logger.debug(
            "chunk_context_provider_null",
            n_chunks=len(chunks),
            doc_chars=len(doc_full_text),
            max_context_tokens=max_context_tokens,
        )
        return ["" for _ in chunks]


# Prompt template — single source of truth lives in this module so unit
# tests can re-import the canonical text. ``{doc}`` becomes the cached
# Anthropic prefix on the real provider; ``{chunk}`` is the per-item
# variable suffix. Domain-neutral: no brand / industry token.
CHUNK_CONTEXT_PROMPT_TEMPLATE: str = (
    "Document: {doc}\n\n"
    "Chunk: {chunk}\n\n"
    "Provide a short (max {max_tokens} tokens) context that situates "
    "this chunk within the document. Copy verbatim any document/regulation "
    "numbers, dates, percentages, monetary amounts, and proper nouns present "
    "in the chunk — do not paraphrase or omit them. Return ONLY the context, "
    "no preamble."
)


def _truncate_to_storage_cap(context: str) -> str:
    """Truncate to DB column limit + warn if we had to cut.

    Storage is bounded by ``alembic 010l`` VARCHAR(1024). Application-side
    truncation makes the failure-mode explicit (structlog warn) rather
    than letting Postgres reject the row.
    """
    if len(context) <= _CHUNK_CONTEXT_STORAGE_LIMIT_CHARS:
        return context
    logger.warning(
        "chunk_context_truncated",
        original_chars=len(context),
        limit_chars=_CHUNK_CONTEXT_STORAGE_LIMIT_CHARS,
    )
    return context[:_CHUNK_CONTEXT_STORAGE_LIMIT_CHARS]


class ChunkContextEnricher:
    """Generate situated-context labels for a batch of chunks.

    Single public entrypoint: ``async generate_contexts(doc, chunks)`` →
    ``list[str]`` aligned positionally with the input chunks. Empty
    string signals a per-chunk failure (caller persists NULL).

    Cost guard: docs longer than ``max_doc_chars`` are skipped wholesale
    (all-empty result + structlog event) so a runaway 1MB doc cannot
    burn the prompt-cache budget for an entire batch.
    """

    def __init__(
        self,
        provider: ChunkContextProviderPort | None = None,
        *,
        max_context_tokens: int = DEFAULT_CHUNK_CONTEXT_MAX_TOKENS,
        max_doc_chars: int = DEFAULT_CR_MAX_DOC_CHARS,
    ) -> None:
        """@param provider: LLM provider; defaults to ``NullChunkContextProvider``.
        @param max_context_tokens: hard cap on per-chunk output token budget;
            lifted from ``shared/constants.DEFAULT_CHUNK_CONTEXT_MAX_TOKENS``.
        @param max_doc_chars: cost-guard cap on parent document size; lifted
            from ``shared/constants.DEFAULT_CR_MAX_DOC_CHARS`` (shared with
            the inline-wrap CR path so both gates trigger at the same size).
        """
        if max_context_tokens <= 0:
            raise ValueError(
                f"max_context_tokens must be positive, got {max_context_tokens!r}",
            )
        if max_doc_chars <= 0:
            raise ValueError(
                f"max_doc_chars must be positive, got {max_doc_chars!r}",
            )
        self._provider: ChunkContextProviderPort = (
            provider if provider is not None else NullChunkContextProvider()
        )
        self._max_context_tokens = max_context_tokens
        self._max_doc_chars = max_doc_chars

    @property
    def provider_name(self) -> str:
        """Convenience accessor for structured logging / metrics."""
        get_name = getattr(self._provider, "get_provider_name", None)
        if callable(get_name):
            try:
                return str(get_name())
            except (TypeError, AttributeError):
                return type(self._provider).__name__
        return type(self._provider).__name__

    async def generate_contexts(
        self,
        doc_full_text: str,
        chunks: Sequence[str],
    ) -> list[str]:
        """Produce one context string per chunk.

        @param doc_full_text: parent document. Empty / whitespace-only →
            short-circuit with empty list aligned to ``chunks``.
        @param chunks: leaf chunk strings to label.
        @return: list of length ``len(chunks)``. Position i is the
            generated context for ``chunks[i]`` (empty string if the
            provider returned an empty value or skipped the chunk).

        Never raises. Provider failure is logged at WARN and an
        all-empty list is returned so ingest never blocks on CR
        enrichment (graceful degradation — CLAUDE.md sacred rule).
        """
        # Empty inputs short-circuit BEFORE we touch the provider; this
        # keeps Null-provider behaviour identical to real-provider on the
        # trivial-input paths.
        if not chunks:
            return []
        n_chunks = len(chunks)
        if not doc_full_text or not doc_full_text.strip():
            logger.debug(
                "chunk_context_skip_empty_doc",
                n_chunks=n_chunks,
            )
            return ["" for _ in chunks]

        # Cost guard — defer to ops to chunk the doc differently or raise
        # the cap via system_config when a single document exceeds the
        # platform-default size budget.
        if len(doc_full_text) > self._max_doc_chars:
            logger.info(
                "chunk_context_skip_doc_too_long",
                doc_chars=len(doc_full_text),
                max_doc_chars=self._max_doc_chars,
                n_chunks=n_chunks,
            )
            return ["" for _ in chunks]

        try:
            results = await self._provider.generate(
                doc_full_text=doc_full_text,
                chunks=chunks,
                max_context_tokens=self._max_context_tokens,
            )
        except Exception as exc:  # noqa: BLE001 — graceful degradation per CLAUDE.md
            # Top-level enricher boundary — log + degrade, never escalate.
            # ``error_type`` follows the CLAUDE.md broad-except contract.
            logger.warning(
                "chunk_context_provider_failed",
                provider=self.provider_name,
                error=str(exc),
                error_type=type(exc).__name__,
                n_chunks=n_chunks,
            )
            return ["" for _ in chunks]

        # Provider contract: same length as input. If a buggy adapter
        # returns a mis-sized list we pad / truncate to the input length
        # so the caller can iterate positionally without an IndexError
        # — degrade silent (Aux dependency does not crash ingest).
        if len(results) != n_chunks:
            logger.warning(
                "chunk_context_provider_length_mismatch",
                provider=self.provider_name,
                expected=n_chunks,
                actual=len(results),
            )
            aligned: list[str] = list(results[:n_chunks])
            while len(aligned) < n_chunks:
                aligned.append("")
            results = aligned

        # Apply storage-cap truncation and normalise types — provider
        # might return non-string values on a bad day; coerce to str so
        # the DB INSERT never trips a TypeError.
        normalised: list[str] = []
        n_non_empty = 0
        for raw in results:
            text = str(raw) if raw is not None else ""
            text = text.strip()
            if text:
                n_non_empty += 1
            normalised.append(_truncate_to_storage_cap(text))

        logger.info(
            "chunk_context_enriched",
            provider=self.provider_name,
            n_chunks=n_chunks,
            n_non_empty=n_non_empty,
            doc_chars=len(doc_full_text),
            max_context_tokens=self._max_context_tokens,
        )
        return normalised


def render_chunk_context_prompt(
    *,
    doc_full_text: str,
    chunk: str,
    max_tokens: int = DEFAULT_CHUNK_CONTEXT_MAX_TOKENS,
) -> str:
    """Render the canonical per-chunk prompt string.

    Exposed so provider adapters (Anthropic Haiku Batch, etc.) and unit
    tests share the same single-source-of-truth template — drift here
    would mean the prompt-cache hit signature changes silently between
    test and production paths.
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens!r}")
    return CHUNK_CONTEXT_PROMPT_TEMPLATE.format(
        doc=doc_full_text,
        chunk=chunk,
        max_tokens=max_tokens,
    )


__all__ = [
    "CHUNK_CONTEXT_PROMPT_TEMPLATE",
    "ChunkContextEnricher",
    "ChunkContextProviderPort",
    "NullChunkContextProvider",
    "render_chunk_context_prompt",
]
