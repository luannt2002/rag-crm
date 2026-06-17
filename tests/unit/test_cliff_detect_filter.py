"""Unit tests for `_cliff_detect_filter` — Stream V Phase 3."""

from __future__ import annotations

from ragbot.orchestration.query_graph import _cliff_detect_filter


def _chunk(score: float, _id: str = "x") -> dict:
    return {"id": _id, "score": score, "content": "..."}


def test_huge_cliff_keeps_top_only():
    """Top score 0.567 vs second 0.082 → cliff at index 1 (gap > 0.5)."""
    chunks = [_chunk(0.567, "a"), _chunk(0.082, "b"), _chunk(0.080, "c")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"
    assert meta["triggered"] is True
    assert meta["cut_index"] == 1
    assert meta["max_gap_ratio"] > 0.5


def test_flat_distribution_keeps_all():
    """Scores 0.40, 0.39, 0.37, 0.35 — no cliff > 0.35 ratio → keep all."""
    chunks = [_chunk(0.40, "a"), _chunk(0.39, "b"), _chunk(0.37, "c"), _chunk(0.35, "d")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 4
    assert meta["triggered"] is False
    assert meta["reason"] == "no_cliff_kept_all"


def test_below_floor_dropped_safety_net_keeps_top_one():
    """All 3 below floor → safety net (default ON) keeps top-1.

    Empty-context safety net prevents the LLM from seeing a literally
    blank ``<documents>`` block. The system prompt's empty-context rule
    must drive refusal, not the filter's [] return.
    """
    chunks = [_chunk(0.030, "a"), _chunk(0.020, "b"), _chunk(0.010, "c")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"
    assert meta["safety_triggered"] is True
    assert meta["reason"] == "empty_context_safety_keep_top1"


def test_below_floor_dropped_no_safety_returns_empty():
    """Same input but force_min_keep=False → original [] return."""
    chunks = [_chunk(0.030, "a"), _chunk(0.020, "b"), _chunk(0.010, "c")]
    out, meta = _cliff_detect_filter(
        chunks,
        absolute_floor=0.05,
        gap_ratio=0.35,
        min_keep=1,
        force_min_keep=False,
    )
    assert out == []
    assert meta["safety_triggered"] is False


def test_safety_net_metadata_emitted_on_normal_path():
    """Normal cliff cut path also emits safety_triggered=False for observability."""
    chunks = [_chunk(0.567, "a"), _chunk(0.082, "b"), _chunk(0.080, "c")]
    _, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert "safety_triggered" in meta
    assert meta["safety_triggered"] is False


def test_min_keep_overrides_early_cliff():
    """Cliff at index 1 but min_keep=3 forces 3 chunks kept."""
    chunks = [_chunk(0.567, "a"), _chunk(0.082, "b"), _chunk(0.080, "c"), _chunk(0.030, "d")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=3)
    assert len(out) == 3  # min_keep enforced; 4th below floor anyway
    assert [c["id"] for c in out] == ["a", "b", "c"]


def test_partial_floor_drop_then_cliff_in_remainder():
    """0.50, 0.40, 0.30, 0.04 → floor cuts 0.04, then no cliff in {0.50, 0.40, 0.30}."""
    chunks = [_chunk(0.50, "a"), _chunk(0.40, "b"), _chunk(0.30, "c"), _chunk(0.04, "d")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 3
    assert [c["id"] for c in out] == ["a", "b", "c"]


def test_unsorted_input_gets_sorted_internally():
    """Input not pre-sorted — algorithm sorts descending by score first."""
    chunks = [_chunk(0.082, "b"), _chunk(0.567, "a"), _chunk(0.040, "noise")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"


def test_empty_input_returns_empty():
    out, meta = _cliff_detect_filter([], absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert out == []


def test_single_chunk_returns_unchanged():
    out, meta = _cliff_detect_filter([_chunk(0.5, "a")], absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 1
    assert meta["triggered"] is False
    assert meta["reason"] == "below_floor_or_single"


def test_zero_score_handled_safely():
    """Score = 0 must not divide-by-zero; chunk should drop via floor."""
    chunks = [_chunk(0.0, "a"), _chunk(0.5, "b")]
    out, meta = _cliff_detect_filter(chunks, absolute_floor=0.05, gap_ratio=0.35, min_keep=1)
    assert len(out) == 1
    assert out[0]["id"] == "b"
