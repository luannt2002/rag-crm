"""Sentence-similarity strategy port — boundary detection for semantic chunking.

The :func:`ragbot.shared.chunking._chunk_semantic` strategy splits text where
adjacent-sentence similarity drops below a threshold. Two scoring strategies
need to coexist:

* **Lexical** — :class:`~difflib.SequenceMatcher` + word Jaccard. Sync, zero
  network, default fallback. Behaviour-preserving baseline.
* **Embedding** — dense-vector cosine via :class:`EmbedderPort` with Redis-
  cached sentence vectors. Async, semantic-aware: addresses the case where
  Vietnamese paraphrase pairs score ~0.0 under lexical (no token overlap).

This port lets the chunking module take *either* strategy without `if
provider == ...` branches inside business logic (Strategy + DI mindset,
``CLAUDE.md``). Adapters live under
``ragbot/infrastructure/sentence_similarity/`` and are wired through
:func:`build_sentence_similarity` in that package's registry.

Proof citation
--------------
Inspired by LangChain ``SemanticChunker`` (langchain-experimental) and the
NVIDIA RAGAS chunking benchmark which reports embedding-cosine boundaries
lift page-level recall from ~0.51 (lexical) to ~0.65 (cosine) on narrative
documents. Lexical scoring remains the default to keep ingest deterministic
and offline-safe; operators opt in via
``system_config.embedding_semantic_chunk_enabled``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SentenceSimilarityPort(Protocol):
    """Strategy port: score similarity between two sentences in ``[0.0, 1.0]``.

    Implementations MUST:

    * Return ``0.0`` (not raise) when either side is empty / whitespace.
    * Clamp to ``[0.0, 1.0]`` — callers compare against a threshold and
      negative cosines on out-of-vocab text would invert the boundary test.
    * Be safe to call concurrently across asyncio tasks.

    Implementations MAY:

    * Be sync internally (``async def`` simply awaits and returns); the
      port is async-by-contract so embedding strategies don't need a
      separate signature.
    """

    async def similarity(self, s1: str, s2: str) -> float:
        """Return adjacent-sentence similarity in ``[0.0, 1.0]``.

        @param s1: left sentence
        @param s2: right sentence
        @return: similarity score; higher = topic continues; lower = boundary.
        """
        ...

    @property
    def provider_name(self) -> str:
        """Registry key for observability (``"lexical"`` | ``"embedding"`` | ``"null"``)."""
        ...

    def stats(self) -> dict[str, float | int]:
        """Per-call telemetry snapshot.

        Implementations report at minimum:

        * ``calls`` — total similarity() invocations since construction.
        * ``cache_hits`` — Redis hits for sentence embeddings (``0`` for
          lexical / null adapters).
        * ``cache_misses`` — Redis misses that triggered a fresh embed call.

        Returning a fresh dict (not a live reference) keeps the snapshot
        stable for structured-log emission.
        """
        ...


__all__ = ["SentenceSimilarityPort"]
