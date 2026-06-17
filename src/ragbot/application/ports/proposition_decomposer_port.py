"""Proposition Decomposer Port — contract for atomic decomposition strategies.

Reference: Chen et al. EMNLP 2024, "Dense X Retrieval: What Retrieval
Granularity Should We Use?" (https://arxiv.org/abs/2312.06648).
Decompose a paragraph / chunk into atomic, self-contained propositions
before embedding. Each proposition is one factual claim with pronouns
and coreferents replaced by their full entity names so the sentence
reads correctly in isolation. The paper reports +55% relative EM over
Contriever on factoid QA when retrieval embeds propositions instead of
paragraph or sentence chunks.

Owner-opt-in: the platform exposes the Port + Registry but never enables
decomposition automatically. Operators flip
``system_config.proposition_llm_decomp_enabled`` (tenant-wide) AND
``system_config.proposition_use_llm`` to opt in; otherwise the default
``NullPropositionDecomposer`` returns ``[text]`` unchanged so the ingest
hot path is unaffected and zero LLM cost is paid.

Implementations:
    - ``NullPropositionDecomposer`` — returns ``[text]`` verbatim
      (default OFF).
    - ``LLMPropositionDecomposer`` — uses an injected ``LLMPort`` to
      ask Chen et al.'s decomposition prompt.

Caller contract:
    decompose(text) -> list[str]

The Port deliberately does NOT carry tenant or trace identifiers — those
are bound at construction time by the implementation so the call site
inside ``_chunk_proposition`` stays minimal.

Graceful-degradation contract (HALLU=0 sacred): on ANY failure path
(LLM error, empty completion, malformed output) implementations MUST
return ``[text]`` (i.e. the original paragraph as a single chunk) so
downstream embedding never receives fabricated propositions.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PropositionDecomposerPort(Protocol):
    """Decompose ``text`` into atomic self-contained propositions.

    The propositions returned are what the caller will embed in place of
    the raw paragraph. Implementations MUST return ``[text]`` on any
    failure path (LLM error, empty completion, disabled flag, etc.) so
    the ingest pipeline degrades gracefully — proposition decomposition
    is an enhancement, never a hard dependency.
    """

    async def decompose(self, text: str) -> list[str]:
        """Return atomic propositions for ``text``.

        @param text: source paragraph / chunk (already cleaned of
            structural-path prefix by the upstream chunker).
        @return: list of self-contained proposition strings; or
            ``[text]`` (single-element list) when the strategy is OFF /
            failed / produced empty output. Never returns an empty list
            — callers can rely on the result containing at least one
            element so the ingest pipeline never silently drops a chunk.
        """
        ...


__all__ = ["PropositionDecomposerPort"]
