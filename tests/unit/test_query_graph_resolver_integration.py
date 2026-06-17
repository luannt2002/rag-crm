"""Integration-style test — wire resolver + llm mocks into build_graph."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


def _base_state(query: str = "San pham bao hanh may nam?") -> dict:
    return {
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
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}


def _make_resolver_and_llm(answer_text: str):
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/provider-model"
    cfg.provider = MagicMock(api_key="sk-xxx", base_url="http://x", code="mock")
    cfg.params = MagicMock(temperature=0.2, max_tokens=256)
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    llm = MagicMock()

    async def _complete(_cfg, messages, **_kw):
        # merged understand_query or legacy router -> return factoid to continue flow
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại intent" in joined:
            return {
                "text": '{"query": "test query", "intent": "factoid"}',
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        if "phan loai intent" in joined:
            return {
                "text": "factoid",
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            return {
                "text": "Chunk 1: relevant",
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        return {
            "text": answer_text,
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "cost_usd": 0.0001,
            "finish_reason": "stop",
        }

    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def test_graph_ainvoke_with_resolver_and_llm_not_crash():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_resolver_and_llm("Bao hanh 24 thang.")
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )
    final = asyncio.run(
        graph.ainvoke(_base_state(), config={"recursion_limit": 25}),
    )
    # Mindset clean: empty answer is valid contract when bot has no oos_template
    assert "answer" in final
    assert isinstance(final.get("citations", []), list)


def test_graph_citation_validator_filters_invalid_from_citations_list():
    """App-mindset: LLM answer text is verbatim — invalid markers stay visible.
    But the citations list MUST exclude out-of-set IDs (so downstream consumers
    do not surface fake source links). Replaces the prior "strip from answer"
    behavior which violated 'application không override LLM answer'.
    """
    from ragbot.orchestration.query_graph import build_graph

    allowed_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    bad_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    answer_with_bad = (
        f"Bao hanh 24 thang [chunk:{allowed_id}] va co ho tro [chunk:{bad_id}]."
    )
    resolver, llm = _make_resolver_and_llm(answer_with_bad)

    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )
    state = _base_state()
    state["graded_chunks"] = [
        {"chunk_id": allowed_id, "text": "24 thang", "score": 0.9},
    ]
    state["reranked_chunks"] = state["graded_chunks"]
    state["retrieved_chunks"] = state["graded_chunks"]

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 25}))
    answer = final.get("answer", "")
    citations = final.get("citations", [])
    # Answer text preserved as-is — bad marker remains visible.
    assert bad_id in answer
    assert allowed_id in answer
    # Citations list filtered: only allowed_id, never bad_id.
    cited_ids = [c["chunk_id"].lower() for c in citations]
    assert allowed_id in cited_ids
    assert bad_id not in cited_ids


def test_graph_with_none_resolver_raises_invariant_violation():
    """No mock fallback: missing resolver/llm must raise InvariantViolation."""
    from ragbot.orchestration.query_graph import build_graph
    from ragbot.shared.errors import InvariantViolation

    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=None,
        llm=None,
    )
    with pytest.raises((InvariantViolation, Exception)) as exc_info:
        asyncio.run(
            graph.ainvoke(_base_state(), config={"recursion_limit": 25}),
        )
    # langgraph may wrap; verify the underlying cause mentions runtime.
    msg = str(exc_info.value) + str(getattr(exc_info.value, "__cause__", "") or "")
    assert "LLM runtime not configured" in msg or "InvariantViolation" in msg


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
