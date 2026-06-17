"""P28 — centralised CRAG / RRF / max-iterations constants.

Guards against silent drift of inline defaults that used to live in
query_graph.py and pgvector SQL. Block α (constants) lane: verifies
presence, types, and sanity ranges. Block β lane wires these into code.
"""

from __future__ import annotations

from ragbot.shared.constants import (
    DEFAULT_CRAG_FALLBACK_COUNT,
    DEFAULT_CRAG_MAX_GRADE_RETRIES,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_MIN_RELEVANT_COUNT,
    DEFAULT_CRAG_MIN_RELEVANT_FRACTION,
    DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS,
    DEFAULT_RRF_RANK_MISS_PENALTY,
)


def test_all_constants_defined() -> None:
    """Each P28 constant imports cleanly with correct type and canonical value."""
    assert isinstance(DEFAULT_CRAG_MIN_RELEVANT_COUNT, int)
    assert DEFAULT_CRAG_MIN_RELEVANT_COUNT == 1

    assert isinstance(DEFAULT_CRAG_MIN_RELEVANT_FRACTION, float)
    assert DEFAULT_CRAG_MIN_RELEVANT_FRACTION == 0.0

    assert isinstance(DEFAULT_CRAG_MIN_FALLBACK_SCORE, float)
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE == 0.3

    assert isinstance(DEFAULT_CRAG_FALLBACK_COUNT, int)
    assert DEFAULT_CRAG_FALLBACK_COUNT == 2

    assert isinstance(DEFAULT_CRAG_MAX_GRADE_RETRIES, int)
    assert DEFAULT_CRAG_MAX_GRADE_RETRIES == 1

    assert isinstance(DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS, int)
    assert DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS == 8

    assert isinstance(DEFAULT_RRF_RANK_MISS_PENALTY, int)
    assert DEFAULT_RRF_RANK_MISS_PENALTY == 1000


def test_fraction_in_0_1_range() -> None:
    """Fraction must be a valid probability (0 ≤ x ≤ 1)."""
    assert 0.0 <= DEFAULT_CRAG_MIN_RELEVANT_FRACTION <= 1.0


def test_max_iterations_positive() -> None:
    """Graph iteration ceiling must be a positive int — 0 would halt all work."""
    assert DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS > 0


def test_rrf_penalty_reasonable() -> None:
    """RRF rank-miss penalty must dominate real rank values but stay bounded."""
    assert DEFAULT_RRF_RANK_MISS_PENALTY > 100
    assert DEFAULT_RRF_RANK_MISS_PENALTY < 100_000
