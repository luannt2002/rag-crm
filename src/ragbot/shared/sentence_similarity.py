"""Lexical sentence-similarity strategy + cosine helper.

Two responsibilities, intentionally co-located so callers that only need the
lexical default don't drag the infrastructure layer in:

1. :class:`LexicalSentenceSimilarity` — default strategy. Wraps the
   pre-existing :func:`SequenceMatcher` + word-Jaccard blend so the legacy
   behaviour of ``_chunk_semantic`` is preserved bit-for-bit when the
   embedding feature flag is OFF. This is the *Null Object* in the sense
   of the strategy registry: it never raises, never calls the network.
2. :func:`cosine_similarity` — pure-math helper used by the embedding
   adapter. Lives here (not in the adapter) so unit tests can pin the
   numerics without touching Redis / EmbedderPort.

Proof citation
--------------
Lexical scoring mirrors the historical ``_sentence_similarity`` blend in
:mod:`ragbot.shared.chunking`. The 60 / 40 weight is empirical — kept
identical so the feature-flag rollout is a true behaviour-preserving
fallback, not a silent retune. NVIDIA RAGAS chunking benchmark reports
embedding-cosine boundaries lift page-level recall on narrative docs.
"""

from __future__ import annotations

import math
from difflib import SequenceMatcher
from typing import Iterable


_SEQUENCE_MATCHER_WEIGHT: float = 0.6
_JACCARD_WEIGHT: float = 0.4


def _clamp_unit(value: float) -> float:
    """Clamp ``value`` to ``[0.0, 1.0]`` — callers threshold-compare it."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def lexical_similarity(s1: str, s2: str) -> float:
    """Blend SequenceMatcher ratio and word-Jaccard similarity.

    Behaviour-preserving reproduction of the historical
    ``_sentence_similarity`` helper. Exposed as a free function so the
    chunking module can call it without depending on the Strategy class
    for the sync fast-path.
    """
    if not s1 or not s2:
        return 0.0
    seq_score = SequenceMatcher(None, s1.lower(), s2.lower()).ratio()
    words1 = set(s1.lower().split())
    words2 = set(s2.lower().split())
    if not words1 or not words2:
        return _clamp_unit(seq_score)
    jaccard = len(words1 & words2) / len(words1 | words2)
    return _clamp_unit(_SEQUENCE_MATCHER_WEIGHT * seq_score + _JACCARD_WEIGHT * jaccard)


def cosine_similarity(v1: Iterable[float], v2: Iterable[float]) -> float:
    """Cosine similarity between two dense vectors, clamped to ``[0.0, 1.0]``.

    Negative cosines (orthogonal-then-anti-aligned embedders) collapse to
    ``0.0`` because the chunking boundary test reads "above threshold =
    same topic" — a negative score under that contract would mean
    "stronger boundary", which is misleading vs the lexical baseline.

    @raise ValueError: when the two vectors have different lengths.
    """
    a = list(v1)
    b = list(v2)
    if len(a) != len(b):
        raise ValueError(
            f"cosine_similarity: dimension mismatch ({len(a)} vs {len(b)})",
        )
    if not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return _clamp_unit(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


class LexicalSentenceSimilarity:
    """Default :class:`SentenceSimilarityPort` — sync lexical blend."""

    def __init__(self) -> None:
        self._calls = 0

    @staticmethod
    def get_provider_name() -> str:
        return "lexical"

    @property
    def provider_name(self) -> str:
        return self.get_provider_name()

    async def similarity(self, s1: str, s2: str) -> float:
        self._calls += 1
        return lexical_similarity(s1, s2)

    def stats(self) -> dict[str, float | int]:
        # Lexical never touches a cache — hit/miss counters fixed at 0 so
        # the structured-log schema is identical to the embedding adapter.
        return {
            "calls": self._calls,
            "cache_hits": 0,
            "cache_misses": 0,
        }


__all__ = [
    "LexicalSentenceSimilarity",
    "lexical_similarity",
    "cosine_similarity",
]
