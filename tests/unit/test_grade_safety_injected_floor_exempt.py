"""E-3: CRAG absolute floor must not drop safety-injected chunks.

The rerank node re-injects top-of-retrieval chunks the reranker dropped
(``_safety_injected=True``). When the min-score/cliff stage emptied the
surviving pool, those chunks keep their raw RRF score (~0.01). In rerank
score-mode the CRAG fallback applies an absolute floor (``crag_min_fallback_score``
≈ 0.3) that is provenance-blind, so it would drop the very chunk the safety-net
re-added — defeating the safety-net. The floor must exempt ``_safety_injected``
chunks.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from ragbot.application.dto.llm_schemas import ChunkGradeItem, GradeBatchOutput
from ragbot.shared.constants import DEFAULT_CRAG_MIN_FALLBACK_SCORE


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        ctx.record_llm = lambda **_a: None
        yield ctx


def _pcfg(state, key, default):
    return state.get("pipeline_config", {}).get(key, default)


class _Lang:
    prompt_grader = "grade prompt"


def _lang(_state):
    return _Lang()


async def _audit(*_a, **_kw):
    return None


def _so_usage(_ctx):
    return {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}


def _make_invoke_all_irrelevant(chunk_ids: list[str]):
    """Stub structured-LLM node: grade every chunk 'no' (irrelevant)."""

    async def _invoke(_state, *, purpose, messages, user_prompt, schema):
        parsed = GradeBatchOutput(
            grades=[ChunkGradeItem(chunk_id=cid, grade="no") for cid in chunk_ids]
        )
        ctx = MagicMock()
        ctx.model_id = "mock/model"
        return parsed, ctx

    return _invoke


def _run_grade(state):
    from ragbot.orchestration.nodes.grade import grade

    return asyncio.run(
        grade(
            state,
            llm=MagicMock(),
            model_resolver=MagicMock(),
            _audit=_audit,
            _invoke_structured_llm_node=_make_invoke_all_irrelevant(
                [str(c["chunk_id"]) for c in state["reranked_chunks"]]
            ),
            _so_usage=_so_usage,
            _pcfg=_pcfg,
            _lang=_lang,
        )
    )


def _base_state(chunks):
    return {
        "query": "câu hỏi giá dịch vụ",
        "rewritten_query": None,
        "reranked_chunks": chunks,
        "intent": "factoid",
        "rerank_score_mode": "rerank",
        "step_tracker": _FakeStepTracker(),
        "pipeline_config": {
            # Force the batched-structured grade path.
            "structured_output_enabled": True,
            "grade_use_structured_output": True,
            "grade_use_batch": True,
            # Disable the high-score skip so the all-irrelevant path runs.
            "crag_skip_retry_above_score": 0.0,
            "grade_timeout_s": 0.0,
            "crag_min_fallback_score": DEFAULT_CRAG_MIN_FALLBACK_SCORE,
        },
    }


def test_safety_injected_chunk_survives_absolute_floor_when_all_irrelevant():
    """All-irrelevant + a safety chunk @0.01 → the safety chunk reaches graded."""
    chunks = [
        {"chunk_id": "c1", "text": "a", "content": "a", "score": 0.02},
        {"chunk_id": "c2", "text": "b", "content": "b", "score": 0.01,
         "_safety_injected": True},
    ]
    out = _run_grade(_base_state(chunks))
    graded_ids = {c["chunk_id"] for c in out["graded_chunks"]}
    assert "c2" in graded_ids, (
        f"safety-injected chunk dropped by absolute floor: graded={graded_ids}"
    )
    assert out["retrieval_adequate"] is True


def test_non_safety_chunk_below_floor_still_dropped():
    """Regression guard: a plain sub-floor chunk (no marker) is still dropped."""
    chunks = [
        {"chunk_id": "c1", "text": "a", "content": "a", "score": 0.01},
    ]
    out = _run_grade(_base_state(chunks))
    graded_ids = {c["chunk_id"] for c in out["graded_chunks"]}
    assert "c1" not in graded_ids, (
        "plain RRF-noise chunk must still be dropped by the absolute floor"
    )
