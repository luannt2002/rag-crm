"""Narrate Port — contract for Narrate-then-Embed strategies (TABLE/FORMULA/IMAGE).

Stream 2A — Inspired by Anthropic Contextual Retrieval (Sep 2024,
https://www.anthropic.com/news/contextual-retrieval), which reports a
**-49% retrieval failure rate** when a SHORT natural-language context
is prepended to each chunk BEFORE embedding. The same principle applies
to non-prose chunks: markdown tables, LaTeX formulas, and image OCR
output embed poorly because dense embedders expect declarative natural
language. The Narrate strategy asks an LLM (typically Anthropic Haiku
via the Batch API for 50% discount) to linearise the block into 1–2
natural-language sentences; the linearised text is what gets embedded,
while the ORIGINAL raw content stays in chunk metadata for downstream
LLM consumption (HALLU=0 — we never embed a substitute and then drop
the source of truth).

Owner-opt-in: the platform exposes the Port + Registry but never enables
narration automatically. Operators flip ``system_config.narrate_then_embed_enabled``
(tenant-wide); otherwise the default ``NullNarrateGenerator`` returns
the raw block content unchanged so ingest is unaffected.

Implementations:
    - ``NullNarrateGenerator`` — returns ``content`` verbatim (default OFF).
    - ``LLMNarrateGenerator`` — uses an injected ``LLMPort`` to draft a
      block-type-aware narration (TABLE → linearisation, FORMULA →
      description, IMAGE → caption fallback to existing OCR description).

Caller contract:
    narrate(content, block_type) -> str

The Port deliberately does NOT carry tenant or trace identifiers — those
are bound at construction time by the implementation so the call site
inside the ingest pipeline stays minimal.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ragbot.shared.constants import DEFAULT_LANGUAGE
from ragbot.shared.types import BlockType


@runtime_checkable
class NarrateServicePort(Protocol):
    """Linearise a non-prose chunk into natural language for embedding.

    Implementations MUST return the original ``content`` on any failure
    path (LLM error, empty completion, unsupported block_type, disabled
    flag) so the ingest pipeline degrades gracefully — narration is an
    enhancement, never a hard dependency. HALLU=0 sacred: when the LLM
    cannot narrate, the embedder still receives the source text rather
    than an empty / fabricated string.
    """

    async def narrate(
        self,
        content: str,
        block_type: BlockType,
        *,
        language: str = DEFAULT_LANGUAGE,
    ) -> str:
        """Return text to embed for ``content`` of ``block_type``.

        @param content: raw block content (markdown table / LaTeX formula /
            OCR description).
        @param block_type: classifier label from the parser
            (``"TABLE"`` / ``"FORMULA"`` / ``"IMAGE"`` / etc.).
        @param language: the document's language code (per-bot ``bots.language``
            threaded from ingest) so narration is produced in the source
            language instead of a hardcoded one.
        @return: narrated text, or the original ``content`` verbatim
            when the strategy is OFF / failed / block_type unsupported.
        """
        ...


__all__ = ["NarrateServicePort"]
