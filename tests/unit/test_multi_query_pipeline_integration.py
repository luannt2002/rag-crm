"""Pipeline-level test for multi-query expansion.

Wires a real ``build_graph(...)`` with a fake vector_store that records every
``hybrid_search`` call. With ``multi_query_enabled=True`` and N=3 paraphrases
the retrieve node MUST issue 3 hybrid_search calls in parallel and RRF-merge
them. With ``multi_query_enabled=False`` only 1 call must happen.

Math-lockdown / streaming compat: multi-query lives in the RETRIEVE stage —
nothing here touches generate(), guard_output, or _stream_sink, so default
graph wiring proves no regression.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #
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


class _RecordingVectorStore:
    """Captures each hybrid_search call so tests can assert call count."""

    def __init__(self):
        self.calls: list[dict] = []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id,
        top_k: int,
        **kw,
    ) -> list[dict]:
        self.calls.append({"query_text": query_text, "top_k": top_k})
        # Each branch returns its own chunk so RRF merge has work to do.
        cid = f"chunk-{len(self.calls)}"
        return [
            {"chunk_id": cid, "text": f"hit for {query_text[:30]}", "content": f"hit for {query_text[:30]}", "score": 0.5},
        ]

    async def search(self, **kw):  # pragma: no cover — fallback path
        return []


class _FakeEmbedder:
    """Embedder needed because retrieve calls _embed_query before hybrid_search.

    The graph's _embed_query helper calls ``embedder.embed([text])`` (batch)
    when only the simple ``.embed`` interface is present.
    """

    async def embed(self, texts, **_kw):
        # Accept either a list or a single string; always return list[list[float]].
        if isinstance(texts, list):
            return [[0.1] * 8 for _ in texts]
        return [[0.1] * 8]

    async def embed_batch(self, texts, **_kw):
        return [[0.1] * 8 for _ in texts]


def _resolver_llm(*, paraphrase_text: str = '["alt 1", "alt 2"]'):
    """Resolver + LLM mock. multi_query purpose returns paraphrase_text."""
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw):
        purpose = kw.get("purpose", "")
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if purpose == "multi_query":
            return {
                "text": paraphrase_text,
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        if "phân loại intent" in joined:
            # Echo back the user query so retrieve sees the original text.
            user_q = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"),
                "",
            )
            return {
                "text": '{"query": "' + user_q + '", "intent": "factoid"}',
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            return {
                "text": "Chunk 1: relevant",
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        return {
            "text": "Answer text.",
            "prompt_tokens": 1, "completion_tokens": 1,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(query: str, *, multi_query_enabled: bool, n_variants: int = 3):
    return {
        "tenant_id": uuid4(),
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": uuid4(),
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
        "pipeline_config": {
            "multi_query_enabled": multi_query_enabled,
            "multi_query_n_variants": n_variants,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            # Keep defaults to short-circuit OOS / decompose paths.
            "merge_condense_router": True,
            "decompose_enabled": False,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": "mock/model",
            "embedding_dimension": 8,
            "top_k": 10,
            "reranker_enabled": False,
            "rag_rrf_k": 60,
        },
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_multi_query_enabled_invokes_n_hybrid_searches():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm(paraphrase_text='["bao lâu", "thời hạn"]')
    vs = _RecordingVectorStore()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    state = _base_state("bảo hành", multi_query_enabled=True, n_variants=3)

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Expect 3 hybrid_search calls (1 original + 2 paraphrases).
    assert len(vs.calls) == 3, f"expected 3 parallel hybrid_search, got {len(vs.calls)}: {vs.calls}"
    queries = [c["query_text"] for c in vs.calls]
    # Original query (or its rewritten variant) must appear; understand_query
    # may inject a "Câu hỏi: ..." prefix → use substring match.
    assert any("bảo hành" in q for q in queries), queries
    assert any("bao lâu" in q for q in queries), queries
    assert any("thời hạn" in q for q in queries), queries
    # Pipeline still produced an answer (no streaming / generate regression).
    assert final.get("answer"), "pipeline must still produce an answer with multi-query on"


def test_multi_query_disabled_invokes_single_hybrid_search():
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    state = _base_state("bảo hành", multi_query_enabled=False, n_variants=3)

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert len(vs.calls) == 1, f"flag OFF must keep single hybrid_search, got {len(vs.calls)}"
    assert "bảo hành" in vs.calls[0]["query_text"], vs.calls[0]["query_text"]
    assert final.get("answer")


def test_multi_query_n1_keeps_single_search_even_when_enabled():
    """n_variants=1 must skip LLM expansion and run a single search."""
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    state = _base_state("bảo hành", multi_query_enabled=True, n_variants=1)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert len(vs.calls) == 1, "n=1 ⇒ short-circuit, single hybrid_search"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
