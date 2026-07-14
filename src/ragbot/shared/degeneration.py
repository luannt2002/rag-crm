"""Deterministic degeneration / repetition detector (QA #8).

Model-independent, domain-neutral: the classic failure where a generation
collapses into repeating the same token / phrase / sentence many times
(bug#8 — "công ty bảo hiểm xã hội…" repeated hundreds of times). Pure
string/arithmetic — no LLM, no DB, no I/O, no vocabulary. Short answers are
never judged (a brief answer that repeats a word is not a degenerate loop).

Gating signals (shape-only, OR-combined; each is deep in loop territory for
real prose, so the false-positive rate is near-zero):
  * distinct_word_ratio  — unique / total words. A loop drives this toward 0.
  * distinct_trigram_ratio — unique / total 3-grams. Catches a phrase loop of
                           otherwise-distinct words (single-word frequency stays
                           moderate but the 3-gram set collapses).

``top_token_ratio`` (the single most frequent token's share) is still reported
for observability but does NOT gate: its recall on real degeneration was zero
while a legitimate feature matrix can push one real word past the old cutoff.
Tokens are content-only — markdown scaffolding (`|`, `---`, `*`) is stripped so
a table is not mistaken for a repeated-token loop.

This module NEVER modifies the answer — blocking is a separate owner-gated step
in ``guard_output`` (default observe), the same governed path the
numeric-fidelity / empty-answer guards use (sacred #10 safe).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ragbot.shared.constants import (
    DEFAULT_DEGENERATION_DISTINCT_TRIGRAM_RATIO_MAX,
    DEFAULT_DEGENERATION_DISTINCT_WORD_RATIO_MAX,
    DEFAULT_DEGENERATION_MIN_WORDS,
)

# 3-gram window; a phrase loop collapses the distinct-3-gram set.
_TRIGRAM_N = 3

# Markdown scaffolding — `|` cell borders, `---` rules, `*`/`#`/`>`/`~`/`=`
# emphasis & headings. Counted as words they masquerade as a repeated token and
# trip the ratios on a legitimate table/list. Discarded before the shape maths.
_STRUCTURAL_PUNCT = "|`*#>~_=[]"


def _tokens(answer: str) -> list[str]:
    """Split *answer* into content tokens, dropping markdown structural marks.

    Strips leading/trailing structural punctuation and keeps only tokens that
    still carry an alphanumeric character, so `|`, `---` and `**` never count
    as words (a naive ``.split()`` lets them dominate a real table's ratios).
    """
    out: list[str] = []
    for raw in (answer or "").split():
        tok = raw.strip(_STRUCTURAL_PUNCT)
        if any(ch.isalnum() for ch in tok):
            out.append(tok)
    return out


def _not_degenerate(n_words: int) -> dict[str, Any]:
    return {
        "is_degenerate": False,
        "n_words": n_words,
        "distinct_word_ratio": 1.0,
        "top_token_ratio": 0.0,
        "distinct_trigram_ratio": 1.0,
    }


def classify_answer_degeneration(answer: str) -> dict[str, Any]:
    """Return a degeneration verdict for *answer* (never modifies it).

    Keys: ``is_degenerate`` (bool), ``n_words`` (int), and the three shape
    ratios. Answers shorter than ``DEFAULT_DEGENERATION_MIN_WORDS`` words are
    reported non-degenerate with neutral ratios.
    """
    words = _tokens(answer)
    n = len(words)
    if n < DEFAULT_DEGENERATION_MIN_WORDS:
        return _not_degenerate(n)

    lowered = [w.lower() for w in words]
    distinct_word_ratio = len(set(lowered)) / n
    top_count = Counter(lowered).most_common(1)[0][1]
    top_token_ratio = top_count / n

    if n >= _TRIGRAM_N:
        trigrams = [tuple(lowered[i : i + _TRIGRAM_N]) for i in range(n - _TRIGRAM_N + 1)]
        distinct_trigram_ratio = len(set(trigrams)) / len(trigrams)
    else:
        distinct_trigram_ratio = 1.0

    # top_token_ratio is REPORTED (logged by guard_output) but no longer gates:
    # its recall on real degeneration was 0 (bug#8 sat at 0.167), while a
    # legitimate feature matrix can push one real word past 0.40. The word- and
    # trigram-distinctness signals carry the detection.
    is_degenerate = (
        distinct_word_ratio <= DEFAULT_DEGENERATION_DISTINCT_WORD_RATIO_MAX
        or distinct_trigram_ratio <= DEFAULT_DEGENERATION_DISTINCT_TRIGRAM_RATIO_MAX
    )
    return {
        "is_degenerate": is_degenerate,
        "n_words": n,
        "distinct_word_ratio": round(distinct_word_ratio, 4),
        "top_token_ratio": round(top_token_ratio, 4),
        "distinct_trigram_ratio": round(distinct_trigram_ratio, 4),
    }
