"""Cache-hit response shape: model preserved + cache_status routing.

Validates the post-audit (24-step pipeline) contract:
- ``model_used`` carries the original cached model name (never magic "cache_hit").
- ``cache_status="hit"`` routes the persist-skip + cache_route conditional.
- ``tokens.cached`` forwards upstream prompt+completion if the cached payload
  exposes them (``getattr`` defaults to 0 when fields absent — current schema).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ragbot.application.ports.cache_port import CachedResponse
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


def _base_state(query: str = "chao shop") -> dict:
    return {
        "tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "channel_type": "messenger",
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


def _build_graph(*, semantic_cache, embedder):
    from ragbot.orchestration.query_graph import build_graph

    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(api_key="sk-x", base_url="http://x", code="mock")
    cfg.params = MagicMock(temperature=0.2, max_tokens=256)
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"text": "ignored", "prompt_tokens": 0,
                                           "completion_tokens": 0, "cost_usd": 0.0,
                                           "finish_reason": "stop"})
    return build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        semantic_cache=semantic_cache,
        embedder=embedder,
    )


def test_cache_hit_preserves_model_name() -> None:
    """Cache hit: state.model_used == cached.model_name (no 'cache_hit' magic)."""
    cached = CachedResponse(
        answer="Chao anh/chi a! Em rat vui khi anh/chi ghe tham shop.",
        citations=[{"chunk_id": "x"}],
        model_name="openai/gpt-4.1-mini",
        cached_at_ts=1700000000,
    )
    fake_cache = MagicMock()
    fake_cache.find_similar_with_text = AsyncMock(return_value=cached)
    fake_cache.store = AsyncMock()
    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(semantic_cache=fake_cache, embedder=fake_embedder)
    final = asyncio.run(graph.ainvoke(_base_state(), config={"recursion_limit": 25}))

    assert final.get("answer") == cached.answer
    assert final.get("answer_type") == "cache_hit"
    assert final.get("cache_status") == "hit"
    assert final.get("model_used") == "openai/gpt-4.1-mini"
    # Citations are forwarded as a list copy.
    assert final.get("citations") == [{"chunk_id": "x"}]
    # tokens.cached defaults to 0 because CachedResponse has no token fields yet.
    assert final.get("tokens", {}).get("cached", -1) == 0


def test_cache_hit_empty_model_name_does_not_inject_magic() -> None:
    """When cached.model_name is empty, model_used falls back to '' (not 'cache_hit')."""
    cached = CachedResponse(
        answer="Hello.",
        citations=[],
        model_name="",
        cached_at_ts=1700000000,
    )
    fake_cache = MagicMock()
    fake_cache.find_similar_with_text = AsyncMock(return_value=cached)
    fake_cache.store = AsyncMock()
    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(semantic_cache=fake_cache, embedder=fake_embedder)
    final = asyncio.run(graph.ainvoke(_base_state(), config={"recursion_limit": 25}))

    assert final.get("model_used") == ""
    assert final.get("cache_status") == "hit"


def test_cache_hit_forwards_upstream_tokens_when_attrs_exposed() -> None:
    """If a cache adapter ever attaches prompt_tokens/completion_tokens, tokens.cached aggregates them."""
    # Use a duck-typed mock that exposes the future fields — the production
    # CachedResponse dataclass does not yet expose them, but the read path
    # must already aggregate via getattr defensively.
    cached = MagicMock()
    cached.answer = "Hi."
    cached.citations = []
    cached.model_name = "anthropic/claude-3"
    cached.prompt_tokens = 12
    cached.completion_tokens = 30

    fake_cache = MagicMock()
    fake_cache.find_similar_with_text = AsyncMock(return_value=cached)
    fake_cache.store = AsyncMock()
    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(semantic_cache=fake_cache, embedder=fake_embedder)
    final = asyncio.run(graph.ainvoke(_base_state(), config={"recursion_limit": 25}))

    assert final.get("tokens", {}).get("cached") == 42
    assert final.get("model_used") == "anthropic/claude-3"
