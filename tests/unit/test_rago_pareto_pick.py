"""Unit tests — Stream D Phase 4 (rago_pareto_pick)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rago_pareto_pick import (  # noqa: E402
    SweepRow,
    compute_frontier,
    is_dominated,
    pick_cost_priority,
    pick_latency_priority,
    pick_quality_priority,
)


def _row(
    cid: int,
    pass_rate: float,
    p95: float,
    cost: float,
    *,
    hallu: int = 0,
    err: int = 0,
) -> SweepRow:
    return SweepRow(
        config_id=cid,
        knob_values={},
        n_turns=30,
        pass_rate=pass_rate,
        p95_ms=p95,
        cost_per_turn=cost,
        hallu_count=hallu,
        error_count=err,
    )


def test_dominated_strict_improvement_in_one_axis() -> None:
    a = _row(0, 0.80, 1000.0, 0.001)
    b = _row(1, 0.85, 900.0, 0.0008)
    assert is_dominated(a, b)
    assert not is_dominated(b, a)


def test_not_dominated_when_axes_trade_off() -> None:
    """High pass + high p95 vs low pass + low p95 — neither dominates."""
    a = _row(0, 0.95, 2000.0, 0.001)
    b = _row(1, 0.70, 500.0, 0.001)
    assert not is_dominated(a, b)
    assert not is_dominated(b, a)


def test_not_dominated_against_self() -> None:
    a = _row(0, 0.80, 1000.0, 0.001)
    assert not is_dominated(a, a)


def test_compute_frontier_drops_dominated() -> None:
    rows = [
        _row(0, 0.80, 1000.0, 0.001),  # dominated by 1
        _row(1, 0.85, 900.0, 0.0008),  # frontier
        _row(2, 0.70, 500.0, 0.001),  # frontier (low p95)
        _row(3, 0.95, 2000.0, 0.0005),  # frontier (high pass + low cost)
    ]
    frontier = compute_frontier(rows)
    cids = {r.config_id for r in frontier}
    assert 0 not in cids
    assert cids == {1, 2, 3}


def test_compute_frontier_all_pareto_optimal() -> None:
    """Three-way trade-off configs should all stay on frontier."""
    rows = [
        _row(0, 0.95, 2000.0, 0.001),  # high quality
        _row(1, 0.70, 500.0, 0.0015),  # low latency
        _row(2, 0.80, 1500.0, 0.0005),  # low cost
    ]
    frontier = compute_frontier(rows)
    assert {r.config_id for r in frontier} == {0, 1, 2}


def test_compute_frontier_empty_input() -> None:
    assert compute_frontier([]) == []


def test_pick_latency_priority_picks_lowest_p95_above_quality_floor() -> None:
    rows = [
        _row(0, 0.90, 800.0, 0.001),  # quality floor candidate
        _row(1, 0.92, 1500.0, 0.001),  # best quality, slow
        _row(2, 0.50, 300.0, 0.001),  # fast but quality crashed
    ]
    frontier = compute_frontier(rows)
    pick = pick_latency_priority(frontier)
    # Best quality is 0.92; floor = 0.90. Among {0.90, 0.92} pick lowest p95.
    assert pick is not None
    assert pick.config_id == 0


def test_pick_cost_priority_minimises_cost_above_quality_and_latency_floor() -> None:
    rows = [
        _row(0, 0.90, 1000.0, 0.0010),
        _row(1, 0.91, 1050.0, 0.0005),  # best cost, near-best quality
        _row(2, 0.91, 5000.0, 0.0001),  # cheapest but slow → may fail headroom
    ]
    frontier = compute_frontier(rows)
    pick = pick_cost_priority(frontier)
    assert pick is not None
    # Both 0 and 1 within quality floor; 1 is cheaper.
    assert pick.config_id == 1


def test_pick_quality_priority_picks_highest_pass_in_resource_envelope() -> None:
    """Resource envelope: p95 ≤ best_p95×1.5, cost ≤ best_cost×1.5.
    Best p95=500 → ceiling 750. Best cost=0.0005 → ceiling 0.00075.
    Row 0 (p95=600) within both ceilings + highest pass → pick row 0."""
    rows = [
        _row(0, 0.95, 600.0, 0.00060),  # high quality, within both ceilings
        _row(1, 0.85, 500.0, 0.00050),  # cheaper + faster (defines best p95+cost)
    ]
    frontier = compute_frontier(rows)
    pick = pick_quality_priority(frontier)
    assert pick is not None
    assert pick.config_id == 0


def test_pick_quality_priority_falls_back_to_max_pass_when_no_envelope_eligible() -> None:
    """When no config fits resource envelope, picker falls back to global
    max-pass on the frontier (ensures non-None when frontier non-empty)."""
    rows = [
        _row(0, 0.95, 5000.0, 0.005),  # huge p95 + cost
        _row(1, 0.85, 500.0, 0.0005),  # tight envelope-defining
    ]
    frontier = compute_frontier(rows)
    pick = pick_quality_priority(frontier)
    assert pick is not None
    # 0 has highest pass_rate but breaches both ceilings; fallback = 1.
    assert pick.config_id == 1


def test_pick_returns_none_for_empty_frontier() -> None:
    assert pick_latency_priority([]) is None
    assert pick_cost_priority([]) is None
    assert pick_quality_priority([]) is None


def test_compute_frontier_excludes_strictly_dominated_chain() -> None:
    """Chain a < b < c — frontier should keep only c."""
    a = _row(0, 0.50, 2000.0, 0.002)
    b = _row(1, 0.70, 1500.0, 0.0015)
    c = _row(2, 0.90, 1000.0, 0.001)
    frontier = compute_frontier([a, b, c])
    assert {r.config_id for r in frontier} == {2}
