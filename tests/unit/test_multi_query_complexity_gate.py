"""Pin the Adaptive-RAG multi-query complexity auto-gate.

The gate lets simple single-fact queries skip the LLM paraphrase fanout
(faster + cheaper) while complex queries still expand. Two invariants:

1. The DEFAULT floor is 0.0 → gate is INERT out of the box (zero regression
   on existing retrieval behaviour). Auto-mode is opt-in per-bot via
   ``pipeline_config.multi_query_complexity_min`` after load-test calibration.
2. The complexity classifier (which the gate consults inline) separates
   trivial queries from list/multi-intent queries with a usable margin, so a
   calibrated floor in the 0.3–0.5 band suppresses only trivial fanout.
"""

from __future__ import annotations

from ragbot.shared.constants import DEFAULT_MULTI_QUERY_COMPLEXITY_MIN
from ragbot.orchestration.nodes.query_complexity import classify_query_complexity


def test_gate_default_is_inert_no_regression():
    # 0.0 floor → `score < 0.0` is never true → fanout never suppressed.
    assert DEFAULT_MULTI_QUERY_COMPLEXITY_MIN == 0.0


def test_trivial_query_scores_below_calibration_floor():
    # A bare existence check carries no complexity signal.
    _, score = classify_query_complexity("còn hàng không")
    assert score < 0.3, f"trivial query should score low, got {score}"


def test_list_query_scores_above_floor():
    # Multi-intent list/aggregation query must stay above any sane floor so
    # the gate never suppresses fanout where recall breadth matters.
    _, score = classify_query_complexity(
        "liệt kê tất cả dịch vụ về da và giá, và có loại nào cho da nhạy cảm không?"
    )
    assert score >= 0.5, f"complex list query should score high, got {score}"


def test_gate_decision_matches_floor_semantics():
    """Replicate the gate predicate to lock its direction (skip iff below floor)."""
    floor = 0.3

    def _would_skip(query: str) -> bool:
        _, score = classify_query_complexity(query)
        return floor > 0.0 and float(score) < floor

    assert _would_skip("còn hàng không") is True          # trivial → skip fanout
    assert _would_skip("liệt kê tất cả dịch vụ về da") is False  # complex → fanout runs
