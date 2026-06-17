"""Pure helpers for speculative parallel retrieve (Phase B Stream B1).

The retrieval pipeline today runs sequentially after ``understand_query``:

    cache_check_and_understand_parallel
        → understand_query (LLM rewrite, ~1.5-2s)
        → rewrite_and_mq_parallel (LLM rewrite, ~0.5-1s)
        → retrieve (embed + hybrid_search, ~0.5-1s)

When the rewritten query is close enough to the raw user input (cosine
similarity above ``speculative_similarity_threshold``), the rewrite step
adds nothing and the user pays its latency for nothing. The speculative
strategy fires ``embed(raw_query)`` + ``hybrid_search`` *in parallel* with
the understand+rewrite chain. On overlap, the speculative chunks become
the retrieved set; on miss, they are discarded and ``retrieve`` runs
normally against the rewritten query.

This module hosts only pure functions so they can be exercised in
isolation. The orchestration wiring lives in
``ragbot.orchestration.query_graph`` (closure capture of the DI handles
makes node bodies inseparable from build-time, but this policy layer is
free of DI and trivially testable).
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Compute cosine similarity between two equal-length numeric vectors.

    Returns 0.0 on any degenerate input (mismatched length, empty vector,
    or a zero-norm vector). Never raises — callers wire this into hot-path
    decisions and a single bad embed must not break the pipeline.
    """
    if not v1 or not v2:
        return 0.0
    if len(v1) != len(v2):
        return 0.0
    dot = 0.0
    n1 = 0.0
    n2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        n1 += a * a
        n2 += b * b
    if n1 <= 0.0 or n2 <= 0.0:
        return 0.0
    denom = math.sqrt(n1) * math.sqrt(n2)
    if denom <= 0.0:
        return 0.0
    return dot / denom


def decide_keep_speculative(
    raw_embed: Sequence[float] | None,
    rewritten_embed: Sequence[float] | None,
    threshold: float,
) -> bool:
    """Return ``True`` when speculative chunks should be reused.

    Decision policy: cosine_similarity(raw, rewritten) >= threshold. When
    either embedding is missing or the threshold is non-positive (the
    bot owner disabled the gate), refuse to keep speculative results so
    the safer normal retrieve path takes over.
    """
    if raw_embed is None or rewritten_embed is None:
        return False
    if threshold <= 0.0:
        return False
    sim = cosine_similarity(raw_embed, rewritten_embed)
    return sim >= threshold


__all__ = ["cosine_similarity", "decide_keep_speculative"]
