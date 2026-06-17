"""Unit tests for `shared.context_utils.reorder_for_lost_in_middle`.

Liu et al., 2023 mitigation: highest-ranked chunks must bracket the LLM
context (position 0 AND -1) instead of being buried in the middle. The
reorder uses an even/odd interleave: i=0 -> front, i=1 -> back, etc.
"""
from __future__ import annotations

from ragbot.shared.context_utils import reorder_for_lost_in_middle


def _mk(name: str, score: float) -> dict:
    return {"id": name, "score": score}


def test_reorder_empty_list_unchanged() -> None:
    assert reorder_for_lost_in_middle([]) == []


def test_reorder_single_item_unchanged() -> None:
    chunks = [_mk("a", 0.9)]
    assert reorder_for_lost_in_middle(chunks) == chunks


def test_reorder_two_items_unchanged() -> None:
    chunks = [_mk("a", 0.9), _mk("b", 0.8)]
    # No middle to lose with len==2 -> identity.
    assert reorder_for_lost_in_middle(chunks) == chunks


def test_reorder_three_items_interleave_exact() -> None:
    # i=0 -> front[0]=c0; i=1 -> back[2]=c1; i=2 -> front[1]=c2.
    chunks = [_mk("c0", 0.9), _mk("c1", 0.8), _mk("c2", 0.7)]
    out = reorder_for_lost_in_middle(chunks)
    assert [c["id"] for c in out] == ["c0", "c2", "c1"]


def test_reorder_six_items_interleave_exact() -> None:
    # Liu et al. 2023 interleave: top at 0 + second-best at N-1, weak in middle.
    chunks = [
        _mk("c0", 0.95),
        _mk("c1", 0.90),
        _mk("c2", 0.80),
        _mk("c3", 0.70),
        _mk("c4", 0.60),
        _mk("c5", 0.50),
    ]
    out = reorder_for_lost_in_middle(chunks)
    assert [c["id"] for c in out] == ["c0", "c2", "c4", "c5", "c3", "c1"]
    # Cross-check the design contract:
    assert out[0]["id"] == "c0"   # strongest at start
    assert out[-1]["id"] == "c1"  # second-strongest at end
    # Weakest two land in the middle (indices 2 + 3 for n=6).
    assert {out[2]["id"], out[3]["id"]} == {"c4", "c5"}
    # No data lost.
    assert sorted(c["id"] for c in out) == sorted(c["id"] for c in chunks)


def test_reorder_odd_length_keeps_top_at_start_and_second_at_end() -> None:
    # 5 ranked desc -> [c0, c2, c4, c3, c1]
    chunks = [_mk(f"c{i}", 1.0 - i * 0.1) for i in range(5)]
    out = reorder_for_lost_in_middle(chunks)
    assert [c["id"] for c in out] == ["c0", "c2", "c4", "c3", "c1"]
    assert out[0]["id"] == "c0"
    assert out[-1]["id"] == "c1"
    # End slot beats the middle.
    assert out[-1]["score"] >= out[len(out) // 2]["score"]


def test_reorder_does_not_mutate_input() -> None:
    chunks = [_mk(f"c{i}", 1.0 - i * 0.1) for i in range(6)]
    snapshot = [dict(c) for c in chunks]
    _ = reorder_for_lost_in_middle(chunks)
    assert chunks == snapshot
