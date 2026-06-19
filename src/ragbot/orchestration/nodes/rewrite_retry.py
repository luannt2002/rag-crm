"""CRAG retry node — re-runs query rewrite and bumps the retry counter.

Extracted from ``query_graph.build_graph``. The ``rewrite`` node (itself a
builder closure over di_kwargs) is threaded in as a callable kwarg, bound via
``functools.partial`` in the graph builder.
"""
from __future__ import annotations

from typing import Any

from ragbot.orchestration.query_graph_helpers import _pcfg
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import DEFAULT_CRAG_MAX_GRADE_RETRIES


async def rewrite_retry(
    state: GraphState,
    *,
    rewrite: Any,
) -> dict:
    """CRAG retry path: rewrite query and increment retry counter."""
    async with state["step_tracker"].step("rewrite_retry") as rr_ctx:
        attempt = state.get("grade_retries", 0) + 1
        max_retries = int(
            _pcfg(state, "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES)
        )
        graded_count = len(state.get("graded_chunks") or [])
        triggered_by = (
            "grade_low" if graded_count == 0 else "grade_ambiguous"
        )
        original_query = (state.get("query") or "")
        result = await rewrite(state)
        result["grade_retries"] = attempt
        rewritten_query = result.get("rewritten_query") or ""
        n_chunks_after = len(state.get("retrieved_chunks") or [])
        rr_ctx.set_metadata(
            attempt=attempt,
            max_retries=max_retries,
            triggered_by=triggered_by,
            original_query_preview=str(original_query)[:80],
            rewritten_query_preview=str(rewritten_query)[:80],
            n_chunks_after=n_chunks_after,
        )
        return result
