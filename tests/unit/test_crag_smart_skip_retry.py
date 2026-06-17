"""CRAG smart-skip retry — Stream P1 (T2 perf) + S1 Pipeline-Opt (T1-Smartness).

When pass-1 top retrieval score clears ``crag_skip_retry_above_score``,
``_grade_route`` bypasses ``rewrite_retry`` even on mixed/ambiguous grading
output. Default raised from 0.0 → 0.7 by S1 (trace fa7983c2 wasted 10683ms
on a retry at top_score=0.91).
"""

from __future__ import annotations

from tests.unit._node_test_helpers import build_test_graph, make_state


def _grade_router(compiled):
    """Reach into the compiled graph and pull the ``_grade_route`` closure.

    LangGraph stores conditional-edge branches on the graph's
    ``builder.branches`` dict; for the ``grade`` node we want the path
    function (named ``_grade_route``). ``branch.path`` is a
    ``RunnableCallable`` — the underlying sync function lives at
    ``.func``.
    """
    branches = compiled.builder.branches.get("grade")
    assert branches, "grade node has no conditional branches"
    # Single branch — pull the only path callable.
    branch = next(iter(branches.values()))
    return branch.path.func


def test_crag_smart_skip_threshold_zero_preserves_retry_default():
    """Default threshold 0.0 → retry fires when retrieval_adequate is False."""
    compiled, *_ = build_test_graph()
    route = _grade_router(compiled)
    state = make_state(
        retrieval_adequate=False,
        grade_retries=0,
        reranked_chunks=[{"chunk_id": "c1", "score": 0.85, "content": "x"}],
        pipeline_config={
            "crag_skip_retry_above_score": 0.0,
            "max_grade_retries": 1,
        },
    )
    assert route(state) == "rewrite_retry"


def test_crag_smart_skip_high_score_bypasses_retry():
    """Top_score 0.85 >= threshold 0.5 → bypass to generate."""
    compiled, *_ = build_test_graph()
    route = _grade_router(compiled)
    state = make_state(
        retrieval_adequate=False,
        grade_retries=0,
        reranked_chunks=[
            {"chunk_id": "c1", "score": 0.85, "content": "x"},
            {"chunk_id": "c2", "score": 0.42, "content": "y"},
        ],
        pipeline_config={
            "crag_skip_retry_above_score": 0.5,
            "max_grade_retries": 1,
        },
    )
    assert route(state) == "generate"


def test_crag_smart_skip_low_score_still_retries():
    """Top_score 0.21 < threshold 0.5 → smart-skip declines, retry fires."""
    compiled, *_ = build_test_graph()
    route = _grade_router(compiled)
    state = make_state(
        retrieval_adequate=False,
        grade_retries=0,
        reranked_chunks=[
            {"chunk_id": "c1", "score": 0.21, "content": "x"},
            {"chunk_id": "c2", "score": 0.18, "content": "y"},
        ],
        pipeline_config={
            "crag_skip_retry_above_score": 0.5,
            "max_grade_retries": 1,
        },
    )
    assert route(state) == "rewrite_retry"


def test_crag_smart_skip_does_not_fire_on_second_pass():
    """When retries > 0 (already retried once), smart-skip is irrelevant — let normal cap take over."""
    compiled, *_ = build_test_graph()
    route = _grade_router(compiled)
    state = make_state(
        retrieval_adequate=False,
        grade_retries=1,  # already retried; cap reached
        reranked_chunks=[{"chunk_id": "c1", "score": 0.9, "content": "x"}],
        pipeline_config={
            "crag_skip_retry_above_score": 0.5,
            "max_grade_retries": 1,
        },
    )
    # retries >= max_retries → fall through to generate regardless of skip flag.
    assert route(state) == "generate"


def test_crag_smart_skip_adequate_path_unchanged():
    """retrieval_adequate=True → always generate (legacy invariant)."""
    compiled, *_ = build_test_graph()
    route = _grade_router(compiled)
    state = make_state(
        retrieval_adequate=True,
        grade_retries=0,
        reranked_chunks=[{"chunk_id": "c1", "score": 0.05, "content": "x"}],
        pipeline_config={
            "crag_skip_retry_above_score": 0.5,
            "max_grade_retries": 1,
        },
    )
    assert route(state) == "generate"


def test_crag_smart_skip_falls_back_to_graded_chunks_if_no_rerank_pool():
    """When reranked_chunks empty, the smart-skip inspects graded_chunks instead."""
    compiled, *_ = build_test_graph()
    route = _grade_router(compiled)
    state = make_state(
        retrieval_adequate=False,
        grade_retries=0,
        reranked_chunks=[],
        graded_chunks=[
            {"chunk_id": "c1", "score": 0.72, "content": "x", "relevance": "ambiguous"},
        ],
        pipeline_config={
            "crag_skip_retry_above_score": 0.5,
            "max_grade_retries": 1,
        },
    )
    assert route(state) == "generate"


def test_crag_constants_exported():
    """``DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE`` is the canonical default."""
    from ragbot.shared.constants import DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE

    # S1 Pipeline-Opt raised default 0.0 → 0.7. Production-tuned from
    # trace fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4 (top_score=0.91 wasted
    # 10683ms on retry). Set per-bot to 1.1 to disable.
    assert DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE == 0.7


def test_crag_plan_limit_schema_entry():
    """Bot owner can override via ``plan_limits.crag_skip_retry_above_score``."""
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA

    schema = PLAN_LIMIT_SCHEMA["crag_skip_retry_above_score"]
    assert schema["type"] == "float"
    assert schema["default"] == 0.7
    assert schema["min"] == 0.0
    # max=1.1 (disable-by-overshoot sentinel: any value > 1.0 disables
    # the gate so retry path stays available).
    assert schema["max"] == 1.1
