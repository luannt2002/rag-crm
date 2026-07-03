"""B1 PROOF: the context char-cap must run on the score-DESCENDING order BEFORE
the lost-in-the-middle reorder — otherwise the cap drops a HIGH-relevance chunk
from the reordered tail and keeps a weak middle chunk instead.

This test drives the SAME pure helpers the generate node uses
(apply_context_char_cap + reorder_for_lost_in_middle) and demonstrates the bug
the fix removes: old order (reorder→cap) discards the 2nd-best chunk; new order
(cap→reorder) keeps the top-by-score survivors.
"""
from __future__ import annotations

from ragbot.shared.context_utils import (
    apply_context_char_cap,
    reorder_for_lost_in_middle,
)


def _chunk(cid: str, score: float, chars: int) -> dict:
    return {"chunk_id": cid, "score": score, "content": "x" * chars}


# 6 chunks, score-descending, 800 chars each = 4800 total.
def _score_desc() -> list[dict]:
    return [
        _chunk("c0", 0.95, 800),
        _chunk("c1", 0.90, 800),
        _chunk("c2", 0.80, 800),
        _chunk("c3", 0.70, 800),
        _chunk("c4", 0.60, 800),
        _chunk("c5", 0.50, 800),
    ]


_CAP = 2900  # keeps 3 chunks (3*800=2400 ≤ 2900; a 4th would be 3200 > 2900)


def _ids(chunks: list[dict]) -> set[str]:
    return {c["chunk_id"] for c in chunks}


def test_char_cap_on_score_order_keeps_top_by_score() -> None:
    """NEW order — cap the score-desc list: survivors are the top-3 by score."""
    kept, n_dropped, _ = apply_context_char_cap(_score_desc(), _CAP)
    assert _ids(kept) == {"c0", "c1", "c2"}, "cap on score order keeps top-3"
    assert n_dropped == 3


def test_new_order_keeps_second_best_that_old_order_dropped() -> None:
    """The headline B1 assertion.

    OLD (buggy) order = reorder THEN cap. reorder([c0..c5]) puts c1 (2nd best)
    at the TAIL; the cap drops from the tail → c1 is discarded and a weak chunk
    survives. NEW order = cap THEN reorder → c1 survives.
    """
    src = _score_desc()

    # OLD: reorder first, then cap the reordered list.
    reordered = reorder_for_lost_in_middle(src)  # [c0, c2, c4, c5, c3, c1]
    old_kept, _, _ = apply_context_char_cap(reordered, _CAP)
    old_ids = _ids(old_kept)

    # NEW: cap on score order first, then reorder the survivors.
    new_kept_pre, _, _ = apply_context_char_cap(src, _CAP)
    new_kept = reorder_for_lost_in_middle(new_kept_pre)
    new_ids = _ids(new_kept)

    # c1 (the 2nd-best chunk) is DROPPED by the old order but KEPT by the new.
    assert "c1" not in old_ids, "old order wrongly drops the 2nd-best chunk"
    assert "c1" in new_ids, "new order keeps the 2nd-best chunk"

    # And the old order wrongly KEEPS a weaker chunk that the new order drops.
    assert old_ids != new_ids
    # New order = strictly the top-3 by score (regardless of final position).
    assert new_ids == {"c0", "c1", "c2"}


def test_generate_node_uses_helper_before_reorder() -> None:
    """Guard the call order in the node source: cap helper is invoked, and the
    litm reorder happens after the cap (not before)."""
    import inspect

    from ragbot.orchestration.nodes import generate

    src = inspect.getsource(generate)
    cap_pos = src.find("apply_context_char_cap(")
    reorder_pos = src.find("reorder_for_lost_in_middle(graded)")
    assert cap_pos != -1 and reorder_pos != -1
    assert cap_pos < reorder_pos, "char-cap must precede the LITM reorder"


def test_char_cap_always_keeps_at_least_one() -> None:
    """A single oversized chunk must not zero-context the answer."""
    big = [_chunk("big", 0.9, 10_000)]
    kept, n_dropped, _ = apply_context_char_cap(big, _CAP)
    assert len(kept) == 1 and n_dropped == 0
