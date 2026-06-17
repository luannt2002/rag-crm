"""HyDE Port — contract for Hypothetical Document Embedding generators.

Phase-C C1 stream — Gao et al. 2022, "Precise Zero-Shot Dense Retrieval
without Relevance Labels" (https://arxiv.org/abs/2212.10496). Before
embedding a user query, ask an LLM to produce a SHORT hypothetical answer
to it; embed that hypothetical instead of the raw question. The resulting
vector lives closer to actual document text (declarative style) than the
question text does, lifting top-k recall on ambiguous queries.

Owner-opt-in: the platform exposes the Port + Registry but never enables
generation automatically. Bot owners flip ``bots.plan_limits.hyde_enabled``
(per-bot) or operators flip ``system_config.hyde_enabled`` (tenant-wide);
otherwise the default ``NullHyDEGenerator`` returns the raw query unchanged
so the retrieve hot path is unaffected.

Implementations:
    - ``NullHyDEGenerator`` — returns ``query`` verbatim (default OFF).
    - ``LLMHyDEGenerator`` — uses an injected ``LLMPort`` to draft a
      hypothetical answer.

Caller contract:
    generate(query) -> str

The Port deliberately does NOT carry tenant or trace identifiers — those
are bound at construction time by the implementation so the call site
inside ``_embed_query`` stays minimal.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class HyDEServicePort(Protocol):
    """Generate a hypothetical answer to ``query`` for retrieval embedding.

    The hypothetical is what the caller will embed in place of the raw
    query. Implementations MUST return the original ``query`` on any
    failure path (LLM error, empty completion, disabled flag, etc.) so
    the retrieve pipeline degrades gracefully — HyDE is an enhancement,
    never a hard dependency.
    """

    async def generate(self, query: str) -> str:
        """Return text to embed for ``query``.

        @param query: raw user query (already language-normalised by upstream).
        @return: hypothetical answer text, or the original query verbatim
            when the strategy is OFF / failed / produced empty output.
        """
        ...


__all__ = ["HyDEServicePort"]
