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
