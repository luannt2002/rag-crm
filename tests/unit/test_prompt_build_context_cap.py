"""Test prompt_build context-chars cap (Chroma 2025 "Context Rot" guard).

The generate node enforces ``DEFAULT_GENERATE_CONTEXT_CHARS_CAP`` via a
prompt_build step. Chunks beyond the cap are dropped tail-first; metadata
records ``context_chunks_dropped`` + ``context_chars_dropped`` for audit.

Hallucination risk = ZERO: only drops chunks already lowest in graded order
(post-CRAG, post-LITM-reorder if enabled). Bot owner per-bot override via
``pipeline_config.generate_context_chars_cap``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _Ctx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict = {}

    def set_metadata(self, **kw) -> None:
        self.metadata.update(kw)

    def add_tokens(self, **_kw) -> None:
        return None

    def record(self, **_kw) -> None:
        return None


class _Tracker:
    def __init__(self) -> None:
        self.steps: list[_Ctx] = []

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _Ctx(name)
        self.steps.append(ctx)
        yield ctx

    def by_name(self, n: str) -> list[_Ctx]:
        return [s for s in self.steps if s.name == n]


class _InvLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _Guard:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _Embedder:
    async def embed(self, _texts, **_kw):
        return [[0.1] * 8]

    async def embed_batch(self, texts, **_kw):
        return [[0.1] * 8 for _ in texts]


class _BigChunkVectorStore:
    """Vector store returning N oversized chunks for the cap to truncate."""

    def __init__(self, n: int, chars_per_chunk: int) -> None:
        self.n = n
        self.chars_per_chunk = chars_per_chunk

    async def hybrid_search(
        self, *, query_text, query_embedding, record_bot_id, top_k, **_kw,
    ) -> list[dict]:
        out = []
        # Chunks ordered "top first" (highest priority) so the cap drops tail.
        for i in range(self.n):
            cid = f"big-{i}"
            txt = ("X" * self.chars_per_chunk) + f"#{i}"
            out.append(
                {
                    "chunk_id": cid,
                    "id": cid,
                    "text": txt,
                    "content": txt,
                    "score": 1.0 - (i * 0.01),  # decreasing
                    "document_name": "big.md",
                    "chunk_index": i,
                },
            )
        return out

    async def search(self, **_kw):  # pragma: no cover
        return []


def _resolver_llm():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại intent" in joined:
            return {
                "text": '{"query": "q", "intent": "factoid"}',
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
            "text": "Answer.",
            "prompt_tokens": 1, "completion_tokens": 1,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _state(*, ctx_cap: int | None, n_chunks: int, chars_per_chunk: int):
    cfg = {
        "multi_query_enabled": False,
        "multi_query_n_variants": 1,
        "multi_query_max_variants": 1,
        "multi_query_timeout_s": 5,
        "merge_condense_router": True,
        "decompose_enabled": False,
        "skip_rewrite_intents": ["factoid"],
        "embedding_model": "mock/model",
        "embedding_dimension": 8,
        "top_k": n_chunks,
        "reranker_enabled": False,
        "rag_rrf_k": 60,
        "lost_in_middle_reorder_enabled": False,
        "prompt_compression_enabled": False,
    }
    if ctx_cap is not None:
        cfg["generate_context_chars_cap"] = ctx_cap
    return {
        "tenant_id": uuid4(),
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "q",
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
        "pipeline_config": cfg,
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}


def _build_graph(tr, vs, resolver, llm):
    from ragbot.orchestration.query_graph import build_graph

    from tests.unit._state_lift_helper import register_active_tracker
    register_active_tracker(tr)

    return build_graph(
        invocation_logger=_InvLogger(),
        guardrail=_Guard(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_Embedder(),
    )


def test_prompt_build_drops_tail_chunks_when_over_cap():
    """3 chunks × 4000 chars > cap=5000 → keep 1, drop 2."""
    tr = _Tracker()
    resolver, llm = _resolver_llm()
    vs = _BigChunkVectorStore(n=3, chars_per_chunk=4000)
    graph = _build_graph(tr, vs, resolver, llm)
    asyncio.run(
        graph.ainvoke(
            _state(ctx_cap=5000, n_chunks=3, chars_per_chunk=4000),
            config={"recursion_limit": 30},
        ),
    )
    pb = tr.by_name("prompt_build")
    assert len(pb) == 1, "prompt_build must fire once"
    md = pb[0].metadata
    # Cap = 5000, first chunk = 4001 chars → kept (always keep ≥1);
    # second chunk + first > 5000 → dropped; third chunk also dropped.
    assert md["context_chunks_dropped"] == 2, md
    assert md["context_chars_dropped"] >= 8000, md
    assert md["context_cap"] == 5000, md
    assert md["context_chunks"] == 1, md


def test_prompt_build_under_cap_drops_nothing():
    """3 chunks × 100 chars = 300 < cap=5000 → keep all, drop 0."""
    tr = _Tracker()
    resolver, llm = _resolver_llm()
    vs = _BigChunkVectorStore(n=3, chars_per_chunk=100)
    graph = _build_graph(tr, vs, resolver, llm)
    asyncio.run(
        graph.ainvoke(
            _state(ctx_cap=5000, n_chunks=3, chars_per_chunk=100),
            config={"recursion_limit": 30},
        ),
    )
    pb = tr.by_name("prompt_build")
    md = pb[0].metadata
    assert md["context_chunks_dropped"] == 0, md
    assert md["context_chars_dropped"] == 0, md
    assert md["context_chunks"] == 3, md


def test_prompt_build_keeps_at_least_one_chunk_when_single_huge():
    """1 chunk of 50000 chars, cap=5000 → still keeps 1 (no zero-context refuse)."""
    tr = _Tracker()
    resolver, llm = _resolver_llm()
    vs = _BigChunkVectorStore(n=1, chars_per_chunk=50000)
    graph = _build_graph(tr, vs, resolver, llm)
    asyncio.run(
        graph.ainvoke(
            _state(ctx_cap=5000, n_chunks=1, chars_per_chunk=50000),
            config={"recursion_limit": 30},
        ),
    )
    pb = tr.by_name("prompt_build")
    md = pb[0].metadata
    # Always keep ≥1 — single huge chunk wins despite over-cap.
    assert md["context_chunks_dropped"] == 0, md
    assert md["context_chunks"] == 1, md


def test_prompt_build_per_bot_override_respected():
    """A per-bot override of generate_context_chars_cap takes precedence."""
    tr = _Tracker()
    resolver, llm = _resolver_llm()
    vs = _BigChunkVectorStore(n=3, chars_per_chunk=1500)
    graph = _build_graph(tr, vs, resolver, llm)
    # Custom low cap = 2000 → first chunk (1501c) kept, others dropped.
    asyncio.run(
        graph.ainvoke(
            _state(ctx_cap=2000, n_chunks=3, chars_per_chunk=1500),
            config={"recursion_limit": 30},
        ),
    )
    pb = tr.by_name("prompt_build")
    md = pb[0].metadata
    assert md["context_cap"] == 2000, md
    assert md["context_chunks_dropped"] == 2, md
