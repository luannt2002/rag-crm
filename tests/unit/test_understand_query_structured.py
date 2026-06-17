"""understand_query node uses UnderstandOutput structured call.

Verifies (1) successful structured call sets condensed_query + intent on
state, (2) parse failure (call_with_schema returns None) falls back to
factoid intent + raw query unchanged.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.dto.llm_schemas import UnderstandOutput
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()

        def _record(**_rec_kw):
            ctx._recorded = True

        ctx.record = _record
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


def _resolver_and_llm():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(api_key="sk-x", base_url=None, code="mock", timeout_ms=30_000)
    cfg.params = MagicMock(temperature=0.0, max_tokens=128)
    cfg.pricing = None
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "default", "prompt_tokens": 1, "completion_tokens": 1,
        "cost_usd": 0.0, "finish_reason": "stop",
    })
    # Stub litellm module so structured_output_helper doesn't import real litellm.
    # The structured helper reads `_litellm_module` attribute on the router.
    llm._litellm_module = MagicMock()
    return resolver, llm


def _build_graph_with_understand_only(structured_return):
    """Build graph with patched _invoke_structured_llm_node returning given value."""
    from ragbot.orchestration import query_graph as qg

    resolver, llm = _resolver_and_llm()

    captured_state: dict = {}

    async def _fake_structured(state, *, purpose, messages, user_prompt, schema):  # noqa: ARG001
        captured_state["called_with_schema"] = schema
        captured_state["purpose"] = purpose
        ctx = MagicMock()
        ctx.record = lambda **_kw: None
        return structured_return, ctx

    graph = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )
    return graph, resolver, llm, captured_state


def test_understand_query_structured_success_sets_condensed_and_intent(monkeypatch):
    """Mock _call_with_schema → returns UnderstandOutput → state has comparison intent."""
    from ragbot.orchestration import query_graph as qg

    parsed = UnderstandOutput(
        condensed_query="So sánh A và B",
        intent="comparison",
    )

    async def _fake_call_with_schema(**_kw):
        # Only respond to the UnderstandOutput call so downstream nodes
        # (reflect → ReflectOutput) fall back to their None-paths instead
        # of receiving the wrong pydantic shape.
        if _kw.get("schema") is not UnderstandOutput:
            return None
        sink = _kw.get("usage_sink")
        if sink is not None:
            sink(10, 5, 0, parsed.model_dump_json(), "stop")
        return parsed

    monkeypatch.setattr(qg, "_call_with_schema", _fake_call_with_schema)

    resolver, llm = _resolver_and_llm()
    graph = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    initial = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "So sánh giá A với B đi",
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

    final = asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 25}))
    assert final.get("intent") == "comparison"
    # condensed_query differed from raw → original_query stashed
    assert final.get("query") == "So sánh A và B"
    assert final.get("original_query") == "So sánh giá A với B đi"


def test_understand_query_structured_failure_falls_back_to_factoid(monkeypatch):
    """call_with_schema returns None → state.intent='factoid', query unchanged."""
    from ragbot.orchestration import query_graph as qg

    async def _fake_call_with_schema(**_kw):
        return None  # provider failure / validation error

    monkeypatch.setattr(qg, "_call_with_schema", _fake_call_with_schema)

    resolver, llm = _resolver_and_llm()
    graph = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    initial = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 2,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "What is the warranty period?",
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

    final = asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 25}))
    assert final.get("intent") == "factoid"
    # Raw query preserved (no condensed override)
    assert final.get("query") == "What is the warranty period?"


def test_understand_query_uses_understand_output_schema(monkeypatch):
    """Schema arg passed into _call_with_schema must be UnderstandOutput."""
    from ragbot.orchestration import query_graph as qg

    captured: dict = {"schemas": []}

    async def _fake_call_with_schema(**kw):
        captured["schemas"].append(kw.get("schema"))
        return None

    monkeypatch.setattr(qg, "_call_with_schema", _fake_call_with_schema)

    resolver, llm = _resolver_and_llm()
    graph = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    initial = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 3,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "Q?",
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

    asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 25}))
    # understand_query is the first structured call; downstream nodes (generate)
    # may also call structured but with their own schema. Verify
    # UnderstandOutput appears in the captured list.
    assert UnderstandOutput in captured["schemas"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
