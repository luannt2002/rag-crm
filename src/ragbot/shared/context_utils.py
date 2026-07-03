"""Context-building utilities for the RAG generate node.

Helpers here are pure (no I/O, no DB) and operate on already-ranked chunk
lists. They are imported by `orchestration.query_graph.generate()`.
"""
from __future__ import annotations

from typing import Any, List


def reorder_for_lost_in_middle(chunks: List[Any]) -> List[Any]:
    """Interleave ranked chunks so the strongest items bracket the prompt.

    Mitigates the "lost in the middle" effect (Liu et al., 2023). Caller
    passes a list sorted in **descending relevance** (highest score first);
    we walk the input alternating between the front and back of the output
    so position 0 holds the top chunk, position N-1 holds the second-best,
    and the weakest items land in the middle.

    For ranked input ``[c0, c1, c2, c3, c4, c5]`` (c0 strongest), the output
    is ``[c0, c2, c4, c5, c3, c1]`` — c0 at 0, c1 at -1, c4/c5 buried mid.

    Lists with len <= 2 are returned unchanged (no middle to lose).
    """
    n = len(chunks)
    if n <= 2:
        return list(chunks)
    out: list[Any] = [None] * n
    left, right = 0, n - 1
    for i, ch in enumerate(chunks):
        if i % 2 == 0:
            out[left] = ch
            left += 1
        else:
            out[right] = ch
            right -= 1
    return out


def apply_context_char_cap(
    chunks: List[Any], cap: int,
) -> tuple[List[Any], int, int]:
    """Keep chunks IN THE GIVEN ORDER until their cumulative text length would
    exceed ``cap`` characters; always keep at least one (never zero-context).

    Returns ``(kept, n_dropped, dropped_chars)``.

    ORDER CONTRACT (B1): this MUST run on a score-DESCENDING list, BEFORE
    ``reorder_for_lost_in_middle``. The cap keeps a prefix and drops the tail,
    so on score order it discards the LOWEST-relevance chunks. Running it AFTER
    the LITM reorder discards the reordered tail — which holds the SECOND-best
    chunk (``reorder([c0..c5]) -> [c0,c2,c4,c5,c3,c1]``, c1 at the end) — keeping
    a weak middle chunk instead. So: cap first, reorder the survivors last.
    """
    running = 0
    kept: list[Any] = []
    n_dropped = 0
    dropped_chars = 0
    for c in chunks:
        text = c.get("text") or c.get("content") or ""
        if running + len(text) <= cap or not kept:
            kept.append(c)
            running += len(text)
        else:
            n_dropped += 1
            dropped_chars += len(text)
    return kept, n_dropped, dropped_chars
