"""Regression test for sprint-1-G6 — GraphState parallel markers.

LangGraph reducer drops keys not declared on the TypedDict schema during
state merge. The ``cache_check_and_understand_parallel`` node sets
``_understand_skipped_by_parallel`` (query_graph.py:1726) so that the
downstream ``understand_query`` node short-circuits (query_graph.py:1365).
Without the declaration here, the marker silently disappears → understand
fires 2× per request (live evidence: 25 step rows vs 14 requests).
"""

from typing import get_type_hints


def test_graphstate_declares_understand_skipped_marker():
    """``_understand_skipped_by_parallel`` MUST be on the TypedDict schema."""
    from ragbot.orchestration.state import GraphState

    hints = get_type_hints(GraphState)
    assert "_understand_skipped_by_parallel" in hints, (
        "GraphState missing _understand_skipped_by_parallel — "
        "LangGraph will drop the marker, causing understand_query "
        "to fire 2× per request. See sprint-1-G6 plan."
    )
    assert hints["_understand_skipped_by_parallel"] is bool


def test_graphstate_declares_force_re_understand():
    """``force_re_understand`` is the CRAG-retry escape hatch and shares the
    same drop-on-merge risk; must be declared on GraphState."""
    from ragbot.orchestration.state import GraphState

    hints = get_type_hints(GraphState)
    assert "force_re_understand" in hints
    assert hints["force_re_understand"] is bool
