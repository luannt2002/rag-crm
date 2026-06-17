"""Multi-vector embedding port — late-interaction (ColBERT-style) scaffold.

Contract for strategies that turn a SINGLE chunk into a LIST of dense vectors
keyed by a role label (e.g. heading / body / hypothetical-question). The
retrieval scoring layer aggregates per-role similarities into a single chunk
score using max-pool (or other aggregator) so the system supports late-
interaction retrieval without requiring every adapter to commit to a fixed
schema.

Default operator behaviour is NullMultiVectorEmbedder (single vector list,
backward compatible with the existing single-embedding retrieval path).

CLAUDE.md compliance:
* Port + Strategy + Registry + Null Object pattern (Strategy + DI mindset).
* Adding a provider = drop a file in
  ``ragbot.infrastructure.embedding`` + register in the multi-vector registry.
* No version-ref in the name — purpose-named.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class MultiVectorChunk:
    """A chunk decomposed into per-role dense vectors for late-interaction.

    Attributes
    ----------
    role:
        Opaque label identifying which view of the chunk this vector covers
        (domain-neutral; concrete strategies define the taxonomy — e.g.
        ``"sentence"`` for sentence-split, ``"heading"`` / ``"body"`` /
        ``"hypothesis"`` for the ColBERT-like enrichment path).
    vector:
        Dense float vector in the embedder's native dimension. Empty list
        permitted only for the null path (operator-disabled).
    """

    role: str
    vector: list[float]


@runtime_checkable
class MultiVectorEmbedPort(Protocol):
    """Late-interaction embedding strategy.

    Implementations turn a chunk's text into one or more
    :class:`MultiVectorChunk` entries. The single-vector default
    (``NullMultiVectorEmbedder``) returns a list of length 1 with role
    ``"chunk"`` so existing callers see no behaviour change until the
    feature flag is flipped per-bot.
    """

    async def embed_chunk(self, text: str) -> list[MultiVectorChunk]:
        """Decompose ``text`` into per-role vectors.

        Empty input MUST return an empty list (no roles emitted).
        Implementations MAY skip empty sub-spans (e.g. blank sentences)
        rather than emitting empty vectors.
        """
        ...

    async def embed_chunks(self, texts: list[str]) -> list[list[MultiVectorChunk]]:
        """Batched :meth:`embed_chunk` — one entry per input text."""
        ...

    @property
    def dimension(self) -> int:
        """Per-vector dimension (0 for the null path)."""
        ...

    @property
    def provider_name(self) -> str:
        """Observability identifier (e.g. ``"null"`` / ``"sentence_split"``)."""
        ...


__all__ = ["MultiVectorChunk", "MultiVectorEmbedPort"]
