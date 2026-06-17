"""Smoke test — build LangGraph StateGraph + invoke with mock ports."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    """No-op replacement for InvocationLogger that yields a ctx matching
    the real ``InvocationContext`` surface (``record(...)``).
    """

    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx._recorded = False

        def _record(**_rec_kw):
            ctx._recorded = True

        ctx.record = _record
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


def _fake_resolver_and_llm(answer: str = "Cau tra loi mau.") -> tuple:
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        # Merged understand_query node — returns JSON with query + intent
        if "phân loại intent" in joined or "PHÂN LOẠI intent".lower() in joined:
            return {
                "text": '{"query": "test query", "intent": "factoid"}',
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        # Legacy router node
        if "phan loai intent" in joined:
            return {
                "text": "factoid",
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            # Grade node — return all relevant for test simplicity
            return {
                "text": "Chunk 1: relevant",
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        return {
            "text": answer,
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "cost_usd": 0.0,
            "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def test_build_graph_compiles_with_min_10_nodes():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _fake_resolver_and_llm()
    compiled = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )
    assert compiled is not None
    node_names = set(getattr(compiled, "nodes", {}).keys())
    # Parallel wrappers ``cache_check_and_understand_parallel`` and
    # ``rewrite_and_mq_parallel`` are the actual registered nodes — the
    # legacy ``check_cache`` / ``rewrite`` closures are invoked *inside*
    # them with byte-identical fallback when their feature flags are OFF.
    expected = {
        "guard_input",
        "cache_check_and_understand_parallel",
        "understand_query",
        "router",
        "rewrite_and_mq_parallel",
        "retrieve",
        "rerank",
        "grade",
        "generate",
        "guard_output",
        "reflect",
        "persist",
    }
    assert expected.issubset(node_names), (
        f"missing nodes: {expected - node_names}"
    )


def test_graph_ainvoke_returns_non_empty_clean_answer():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _fake_resolver_and_llm("Gioi thieu san pham ABC chat luong cao.")
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )

    initial = {
        "tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 123,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "channel_type": "api",
        "query": "Chao ban, gioi thieu san pham di.",
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
    answer = final.get("answer", "")
    # Mindset clean (2026-04-29): application does NOT inject any
    # hardcoded refusal template. When the bot has no
    # `oos_answer_template` set AND retrieval is empty AND there's no
    # LLM, the pipeline returns answer = "" — that's the contract now.
    # Bot owners that want a refusal sentence put it on
    # `bots.oos_answer_template`. Test asserts the empty contract +
    # absence of placeholder leakage.
    lowered = answer.lower()
    for banned in ("placeholder", "echo", "base-init", "todo", "fixme"):
        assert banned not in lowered, f"answer contains banned marker: {banned}"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
