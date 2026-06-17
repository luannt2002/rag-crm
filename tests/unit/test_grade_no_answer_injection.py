"""Verify grade + _grade_route never inject app-level refusal text.

CLAUDE.md "Application MINDSET" forbids the application from overriding
the LLM answer with bot.oos_answer_template. Three sites in query_graph
used to short-circuit grade with a template assignment; this test
ensures they no longer do, and that exhausted CRAG retries flow through
``generate`` so the bot owner's system_prompt composes the response.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# ---- Shared fakes (mirror existing query_graph tests) -------------------
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_: None
        yield ctx


class _FakeStepTracker:
    def __init__(self) -> None:
        self.steps: list[str] = []

    @asynccontextmanager
    async def step(self, name, **_kw):
        self.steps.append(name)
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


_GENERATED_ANSWER = "LLM-composed response per persona"


def _make_resolver_llm(grade_text: str = "Chunk 1: irrelevant\nChunk 2: irrelevant"):
    """Build resolver+llm; grade returns ``grade_text``, generate returns marker."""
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(api_key="sk-x", base_url="http://x", code="mock")
    cfg.params = MagicMock(temperature=0.2, max_tokens=256)
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại intent" in joined or "phan loai intent" in joined:
            return {
                "text": '{"query": "test", "intent": "factoid"}',
                "prompt_tokens": 2, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            return {
                "text": grade_text,
                "prompt_tokens": 3, "completion_tokens": 2,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        return {
            "text": _GENERATED_ANSWER,
            "prompt_tokens": 5, "completion_tokens": 5,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(query: str, **overrides):
    state = {
        "tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "channel_type": "api",
        "query": query,
        "rewritten_query": None,
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "intent": "factoid",
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}
    state.update(overrides)
    return state


# Bot owner's template — must NEVER appear in the final answer when no
# safety hard-stop triggered. If it leaks through, the application is
# injecting it (the bug we just fixed).
_OOS_TEMPLATE = "TEMPLATE_LITERAL_SHOULD_NOT_LEAK_TO_USER"


# ---- Test 1: grade with empty reranked_chunks → no answer injection ----


def test_grade_empty_input_does_not_inject_answer():
    """Empty reranked_chunks must NOT cause grade to set state['answer']."""
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_resolver_llm()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    state = _base_state(
        "chao shop",
        pipeline_config={
            "oos_answer_template": _OOS_TEMPLATE,
            "skip_rewrite_intents": ["factoid"],
            "max_grade_retries": 0,
            # F1a refuse short-circuit DISABLED: this test asserts on
            # the LLM-driven generate output to prove no app-side
            # template injection at the grade layer. F1a coverage lives
            # in test_refuse_short_circuit_chunks_zero.py.
            "refuse_short_circuit_enabled": False,
        },
    )
    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Bot owner's template must NOT have leaked — the only path that
    # could leak it was the deleted application override in grade.
    assert _OOS_TEMPLATE not in final.get("answer", "")
    # Generate ran (route honored "generate" after retries) and produced
    # the mock LLM response, proving no app short-circuit to persist.
    assert final.get("answer") == _GENERATED_ANSWER
    assert final.get("answer_type") == "answered"


# ---- Test 2: grade all-irrelevant + below-fallback-score → no inject ----


def test_grade_all_irrelevant_below_fallback_score_does_not_inject_answer(monkeypatch):
    """All-irrelevant + low scores must route to generate, not template-inject."""
    from ragbot.application.dto.llm_schemas import GradeOutput, UnderstandOutput
    from ragbot.orchestration import query_graph as qg

    resolver, llm = _make_resolver_llm()

    # Force structured-output success so grade enters the all_irrelevant +
    # below-fallback-score branch (the formerly app-injecting site).
    async def _fake_schema(**kw):
        schema = kw.get("schema")
        if schema is GradeOutput:
            return GradeOutput(grade="no", reason="")
        if schema is UnderstandOutput:
            return UnderstandOutput(condensed_query="x", intent="factoid")
        return None

    monkeypatch.setattr(qg, "_call_with_schema", _fake_schema)

    # 2 chunks pre-seeded with sub-fallback scores so the OOS path triggers.
    chunks = [
        {"chunk_id": str(uuid4()), "text": "low A", "score": 0.02, "content": "low A"},
        {"chunk_id": str(uuid4()), "text": "low B", "score": 0.02, "content": "low B"},
    ]

    graph = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    state = _base_state(
        "Cau hoi khong lien quan",
        retrieved_chunks=chunks,
        reranked_chunks=chunks,
        pipeline_config={
            "oos_answer_template": _OOS_TEMPLATE,
            "max_grade_retries": 0,
            "crag_min_fallback_score": 0.3,
            "skip_rewrite_intents": ["factoid"],
            "structured_output_enabled": True,
            "grade_use_structured_output": True,
            "reranker_enabled": False,
            # F1a refuse short-circuit DISABLED: see comment in test 1
            # above — this test asserts on the LLM-driven generate
            # output, F1a has its own dedicated test file.
            "refuse_short_circuit_enabled": False,
        },
    )
    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 40}))

    # Application must not have substituted the template into the answer.
    assert _OOS_TEMPLATE not in final.get("answer", "")
    # The LLM (generate node) ran instead — proving _grade_route returned
    # "generate" not "persist" after retries exhausted.
    assert final.get("answer") == _GENERATED_ANSWER
    assert final.get("answer_type") == "answered"


# ---- Test 3: _grade_route exhausted → returns "generate", not "persist" -


def test_grade_route_exhausted_routes_to_generate():
    """When retries are spent and retrieval inadequate, route to generate."""
    from ragbot.orchestration import query_graph as qg
    from ragbot.shared.constants import DEFAULT_CRAG_MAX_GRADE_RETRIES

    # _grade_route is an inner closure inside build_graph. Build a graph
    # to expose it via the compiled object's branch map. The compiled
    # langgraph keeps the routing function inside `branches`.
    resolver, llm = _make_resolver_llm()
    compiled = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    grade_branches = compiled.builder.branches.get("grade") or {}
    assert grade_branches, "grade node has no conditional branches"
    # BranchSpec.path wraps the route callable; .func unwraps it.
    branch = next(iter(grade_branches.values()))
    route_fn = branch.path.func
    # Branch ends must list only the active routes (no orphan "persist").
    assert set(branch.ends.keys()) == {"rewrite_retry", "generate"}

    exhausted_state = {
        "retrieval_adequate": False,
        "grade_retries": DEFAULT_CRAG_MAX_GRADE_RETRIES + 1,
        "pipeline_config": {"max_grade_retries": DEFAULT_CRAG_MAX_GRADE_RETRIES},
    }
    assert route_fn(exhausted_state) == "generate"

    # Sanity: while retries remain, still rewrite_retry.
    pending_state = {
        "retrieval_adequate": False,
        "grade_retries": 0,
        "pipeline_config": {"max_grade_retries": DEFAULT_CRAG_MAX_GRADE_RETRIES},
    }
    assert route_fn(pending_state) == "rewrite_retry"

    # Sanity: adequate retrieval routes straight to generate.
    ok_state = {"retrieval_adequate": True, "grade_retries": 0, "pipeline_config": {}}
    assert route_fn(ok_state) == "generate"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
