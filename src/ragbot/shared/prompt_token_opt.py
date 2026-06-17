"""Prompt-token squeeze helpers (Phase B — B2).

Pure functions that reduce LLM input tokens at the prompt-build boundary,
on top of (and AFTER) the existing per-chunk content compressor
(`prompt_compression.compress_chunks`). The two are orthogonal:

* `prompt_compression` shrinks INSIDE each chunk's text.
* `prompt_token_opt` shrinks the SET of chunks + the history reach.

Operations (all individually toggle-able from `pipeline_config`):

1. **Min-score filter** — drop chunks whose grader score is below a
   per-bot floor. Retrieval already filters by absolute floor, but
   sometimes 5 chunks pass with marginal scores (0.05–0.15) and only
   add tokens without adding signal.
2. **Textual dedupe** — when multiple sources echo the same factoid
   (mirrored docs, FAQ duplicates), drop near-duplicates by character
   3-gram Jaccard. Distinct from semantic MMR which runs over embeddings
   at retrieve time; this is a cheap final pass after compression that
   catches dupes which survive embedding diversity (different doc IDs
   but ~same text).
3. **Factoid history skip** — factoid intent answers are self-contained;
   prior conversation rarely informs the next factoid. Skipping history
   for `intent="factoid"` typically saves 500–1500 input tokens.

All ops are GATED by `prompt_token_opt_enabled` (default False). When
disabled, helpers return their inputs unchanged so they are safe to
call unconditionally from the orchestrator.
"""

from __future__ import annotations

import re
from typing import Any

from ragbot.shared.constants import (
    DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
    DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
    DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
    INTENT_FACTOID,
)

# Character 3-grams are cheap to compute and language-agnostic — good
# fit for Vietnamese where word-segmentation is non-trivial.
_NGRAM_SIZE: int = 3
_TOKENIZE_RE = re.compile(r"\s+")


def _normalize_for_compare(text: str) -> str:
    """Lowercase + collapse whitespace for shape comparison only."""
    if not text:
        return ""
    return _TOKENIZE_RE.sub(" ", text.lower()).strip()


def _char_ngrams(text: str, n: int = _NGRAM_SIZE) -> set[str]:
    """Return set of overlapping character n-grams from normalized text.

    Short text shorter than `n` chars returns a single-element set so
    Jaccard between two short identical strings is still 1.0.
    """
    norm = _normalize_for_compare(text)
    if len(norm) < n:
        return {norm} if norm else set()
    return {norm[i : i + n] for i in range(len(norm) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity over two sets; 0.0 if both empty."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


def filter_min_score(
    chunks: list[dict[str, Any]],
    *,
    min_score: float = DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
    keep_at_least_one: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Drop chunks with score < min_score.

    Always keeps at least one chunk (the highest-scored) when
    `keep_at_least_one=True` to avoid zero-context refuse cascade.

    Returns (filtered_chunks, dropped_count).
    """
    if not chunks or min_score <= 0:
        return chunks, 0

    def _score_of(c: dict[str, Any]) -> float:
        try:
            return float(c.get("score", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    kept = [c for c in chunks if _score_of(c) >= min_score]
    dropped = len(chunks) - len(kept)

    if not kept and keep_at_least_one:
        # Surface the single best-scored chunk so generate node still has signal.
        best = max(chunks, key=_score_of)
        return [best], len(chunks) - 1

    return kept, dropped


def dedupe_chunks(
    chunks: list[dict[str, Any]],
    *,
    jaccard_threshold: float = DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
) -> tuple[list[dict[str, Any]], int]:
    """Drop near-duplicate chunks by character n-gram Jaccard.

    Preserves the FIRST occurrence (assumed higher-ranked since caller
    passes graded chunks in rank order). Threshold of 0.85 catches
    paraphrases / mirrored docs while preserving distinct sources that
    share a few sentences.

    Returns (deduped_chunks, dropped_count).
    """
    if not chunks or jaccard_threshold <= 0 or jaccard_threshold >= 1.0:
        # Degenerate thresholds: <=0 would drop everything, >=1 keeps all.
        return chunks, 0

    kept: list[dict[str, Any]] = []
    kept_ngrams: list[set[str]] = []
    dropped = 0

    for chunk in chunks:
        text = chunk.get("text") or chunk.get("content") or ""
        if not text:
            kept.append(chunk)
            kept_ngrams.append(set())
            continue

        ng = _char_ngrams(text)
        is_dup = False
        for prev_ng in kept_ngrams:
            if not prev_ng:
                continue
            if _jaccard(ng, prev_ng) >= jaccard_threshold:
                is_dup = True
                break

        if is_dup:
            dropped += 1
        else:
            kept.append(chunk)
            kept_ngrams.append(ng)

    return kept, dropped


def should_skip_history(
    intent: str | None,
    *,
    factoid_skip: bool = DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
) -> bool:
    """Decide whether the prompt-build step should drop history messages.

    Returns True only when intent is exactly `factoid` AND
    `factoid_skip` is enabled. Conservative: any non-factoid intent
    (multi_hop, comparison, list, definition, procedural, chitchat,
    out_of_scope) keeps history.
    """
    if not factoid_skip:
        return False
    return (intent or "") == INTENT_FACTOID


def apply_token_opt(
    chunks: list[dict[str, Any]],
    *,
    intent: str | None,
    enabled: bool,
    min_score: float = DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
    dedupe_threshold: float = DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
    factoid_skip_history: bool = DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
) -> tuple[list[dict[str, Any]], bool, dict[str, int]]:
    """Single-call facade for prompt-build to apply min-score + dedupe.

    Returns (squeezed_chunks, skip_history, metrics).

    When `enabled=False`, returns chunks unchanged, `skip_history=False`,
    and a metrics dict with all zero counters. Safe to call
    unconditionally — gating happens internally.
    """
    metrics = {"dropped_by_score": 0, "dropped_by_dedupe": 0}

    if not enabled:
        return chunks, False, metrics

    out = chunks
    out, metrics["dropped_by_score"] = filter_min_score(out, min_score=min_score)
    out, metrics["dropped_by_dedupe"] = dedupe_chunks(
        out, jaccard_threshold=dedupe_threshold,
    )
    skip_hist = should_skip_history(intent, factoid_skip=factoid_skip_history)

    return out, skip_hist, metrics


__all__ = [
    "apply_token_opt",
    "dedupe_chunks",
    "filter_min_score",
    "should_skip_history",
]
