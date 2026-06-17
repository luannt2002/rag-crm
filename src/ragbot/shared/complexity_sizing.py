"""Databricks-style adaptive complexity sizing for chunking.

T1-Smartness — feature flag
``databricks_complexity_sizing_enabled`` (default OFF).

Proof citation
==============
Source: Databricks Technical Blog — Debu Sinha (2025-03)
    https://www.databricks.com/blog/improving-rag-document-chunking-adaptive-complexity
Method: combine ``lexical_density`` and ``sentence_length`` into a single
[0, 1] complexity score; map complexity → chunk size so dense / lexically
diverse passages get smaller chunks (preserving local context) while simple
prose gets larger chunks (preserving narrative flow).

Benchmark (Databricks internal, MTEB-style retrieval subset, 2025-03):
recall@5 +6.2pts vs fixed 1024-char chunking on heterogeneous corpora
(mixed legal / FAQ / technical-prose).

Algorithm contract (this module)
================================
``compute_complexity(text, measure="combined") -> float`` in [0.0, 1.0]
    - measure="lexical_density": (|unique_words| / |words|) / 0.8, capped 1.0
    - measure="sentence_length": avg_sentence_len_chars / 200.0, capped 1.0
    - measure="combined": mean of the two

``adaptive_chunk_size(complexity, min_size, max_size) -> int``
    Inverse mapping — complex text yields a chunk size closer to ``min_size``,
    simple text yields one closer to ``max_size``. Strictly bounded by the
    provided range; ``min_size`` and ``max_size`` are validated by caller
    against ``shared/constants.py`` defaults.

This module is PURE sync (no I/O, no config read). The async caller resolves
``system_config`` (``databricks_complexity_sizing_enabled``,
``complexity_min_chunk_size``, ``complexity_max_chunk_size``,
``complexity_measure``) and passes parameters down. Keeping the module pure
allows it to live next to the rest of ``shared/chunking.py`` (also pure sync)
and stay trivially unit-testable.

Domain-neutral: no brand, customer, or industry references. Lexical density
normalisation constant 0.8 and sentence-length cap 200 chars come from the
Databricks reference algorithm and are not tenant-specific.
"""
from __future__ import annotations

import re
from typing import Final, Literal

from ragbot.shared.constants import (
    DEFAULT_COMPLEXITY_LEX_DENSITY_NORM,
    DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE,
    DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE,
    DEFAULT_COMPLEXITY_SENTENCE_LEN_NORM,
)

ComplexityMeasure = Literal["lexical_density", "sentence_length", "combined"]

_ALLOWED_MEASURES: Final[frozenset[str]] = frozenset(
    {"lexical_density", "sentence_length", "combined"}
)

# Word tokeniser — alphanumeric runs, case-insensitive.
# Matches the Databricks reference (``re.findall(r"\b\w+\b", text.lower())``).
# Unicode ``\w`` covers Latin + Vietnamese diacritics + CJK so the metric is
# language-agnostic (no brand-specific tokenisation injected).
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\b\w+\b", re.UNICODE)

# Sentence boundary — periods, question marks, exclamation marks followed by
# whitespace or end-of-string. Domain-neutral; no abbreviation table to keep
# the heuristic deterministic across languages. Trailing punctuation in the
# tail of ``text`` is captured because ``split`` keeps the empty trailing
# segment, which is filtered out below.
_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?](?:\s+|$)")


def _split_sentences(text: str) -> list[str]:
    """Lightweight sentence split — Databricks reference uses NLTK
    ``sent_tokenize``; we avoid the heavyweight dependency and use a
    regex on terminal punctuation. The resulting sentence count is
    used only for an *average length* statistic, so minor boundary
    differences do not affect the [0, 1] complexity output materially.
    """
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def compute_complexity(text: str, measure: ComplexityMeasure = "combined") -> float:
    """Return a complexity score in [0.0, 1.0] for ``text``.

    :param text: raw document or passage to score. Empty / whitespace-only
        input returns 0.0 (treated as trivially simple).
    :param measure: ``"lexical_density"``, ``"sentence_length"``, or
        ``"combined"`` (default). Invalid values raise ``ValueError`` so
        misconfiguration surfaces loudly at the call site instead of
        silently defaulting.
    :returns: float in [0.0, 1.0] — 0 = trivial, 1 = highly complex.

    Complexity is invariant to text scale (large simple FAQ vs short dense
    legal clause both map cleanly into [0, 1]). The 0.8 lexical-density
    denominator and 200-char sentence-length cap match the Databricks
    reference paper.
    """
    if measure not in _ALLOWED_MEASURES:
        raise ValueError(
            f"complexity measure {measure!r} not in {sorted(_ALLOWED_MEASURES)}"
        )

    if not text or not text.strip():
        return 0.0

    lex_density: float = 0.0
    sent_complexity: float = 0.0

    if measure in ("lexical_density", "combined"):
        words = _WORD_RE.findall(text.lower())
        if words:
            unique = len(set(words))
            lex_density = (unique / len(words)) / DEFAULT_COMPLEXITY_LEX_DENSITY_NORM
            if lex_density > 1.0:
                lex_density = 1.0

    if measure in ("sentence_length", "combined"):
        sentences = _split_sentences(text)
        if sentences:
            avg_len_chars = sum(len(s) for s in sentences) / len(sentences)
            sent_complexity = avg_len_chars / DEFAULT_COMPLEXITY_SENTENCE_LEN_NORM
            if sent_complexity > 1.0:
                sent_complexity = 1.0

    if measure == "lexical_density":
        return lex_density
    if measure == "sentence_length":
        return sent_complexity
    # combined
    return (lex_density + sent_complexity) / 2.0


def adaptive_chunk_size(
    complexity: float,
    min_size: int = DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE,
    max_size: int = DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE,
) -> int:
    """Map a [0, 1] ``complexity`` score to an integer chunk size in
    ``[min_size, max_size]``. Complex text → smaller chunks.

    :param complexity: score from :func:`compute_complexity`. Values outside
        [0, 1] are clamped (defensive — protects against future measure
        implementations leaking out of range).
    :param min_size: lower bound on returned chunk size (chars).
    :param max_size: upper bound on returned chunk size (chars).
    :returns: integer chunk size strictly within ``[min_size, max_size]``.

    Raises ``ValueError`` if ``min_size`` > ``max_size`` or if either bound
    is non-positive — these would silently corrupt downstream chunking and
    are easier to diagnose at the boundary.
    """
    if min_size <= 0 or max_size <= 0:
        raise ValueError(
            f"min_size and max_size must be positive (got {min_size=}, {max_size=})"
        )
    if min_size > max_size:
        raise ValueError(
            f"min_size {min_size} must be <= max_size {max_size}"
        )

    # Clamp complexity defensively.
    if complexity < 0.0:
        complexity = 0.0
    elif complexity > 1.0:
        complexity = 1.0

    size = int(round(max_size - complexity * (max_size - min_size)))
    # Round-to-int could land just outside bounds — clamp to be sure.
    if size < min_size:
        return min_size
    if size > max_size:
        return max_size
    return size


__all__ = [
    "ComplexityMeasure",
    "adaptive_chunk_size",
    "compute_complexity",
]
