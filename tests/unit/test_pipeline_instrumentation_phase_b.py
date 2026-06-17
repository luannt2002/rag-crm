"""Phase B pipeline instrumentation step coverage.

Verifies the new ``step_tracker.step("<name>")`` wrappers added in
Phase B for hallucination triage + cache phase split. Steps under test
(per ``reports/MEGA_PIPELINE_INSTRUMENTATION_PLAN_20260430.md`` §B.7,
§B.8, §B.14):

- ``grounding_check`` — fires inside ``guard_output`` when
  ``grounding_check_enabled=True`` AND the judge LLM callable is invoked.
- ``hash_lookup_cache`` — fires inside ``PgSemanticCache._find_similar_impl``
  when caller passes ``step_tracker`` AND ``query_text`` is non-empty.
- ``semantic_cache_check`` — fires inside the same path AFTER the exact-hash
  miss, before/around the pgvector cosine SQL.

T2 / observability — zero impact on LLM prompt content, zero new LLM calls
added. Cache split is internal; same SQL queries fire in identical order.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
# Recording fakes (reused across tests)                                        #
# --------------------------------------------------------------------------- #
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _RecordingStepCtx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict = {}

    def set_metadata(self, **kwargs) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs) -> None:
        return None

    def record(self, **_kwargs) -> None:
        return None

    def record_llm(self, **_kwargs) -> None:
        return None


class _RecordingStepTracker:
    """Captures every step name + metadata snapshot at exit."""

    def __init__(self) -> None:
        self.steps: list[_RecordingStepCtx] = []

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx(name)
        self.steps.append(ctx)
        yield ctx

    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def by_name(self, name: str) -> list[_RecordingStepCtx]:
        return [s for s in self.steps if s.name == name]


# --------------------------------------------------------------------------- #
# 1. grounding_check — guard_output node integration test                     #
# --------------------------------------------------------------------------- #


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    """Minimal output-guardrail fake that invokes the supplied
    ``llm_complete_fn`` exactly once when ``grounding_check_enabled``.
    Mirrors what the real check_output does for the LLM path so the
    wrap inside _grounding_llm fires.
    """

    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(
        self,
        _answer: str,
        *,
        grounding_check_enabled: bool = False,
        llm_complete_fn=None,
        **_kw,
    ) -> list:
        if grounding_check_enabled and llm_complete_fn is not None:
            # Fire the callable exactly once with a small message list so
            # the wrap inside _grounding_llm produces a step row.
            await llm_complete_fn([
                {"role": "system", "content": "judge"},
                {"role": "user", "content": "claim"},
            ])
        return []


class _RecordingVectorStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id,
        top_k: int,
        **_kw,
    ) -> list[dict]:
        self.calls.append({"query_text": query_text, "top_k": top_k})
        cid = f"chunk-{len(self.calls)}"
        return [
            {
                "chunk_id": cid,
                "id": cid,
                "text": f"hit for {query_text[:30]}",
                "content": f"hit for {query_text[:30]}",
                "score": 0.5,
                "document_name": "doc",
                "chunk_index": len(self.calls),
            },
        ]

    async def search(self, **_kw):  # pragma: no cover
        return []


class _FakeEmbedder:
    async def embed(self, texts, **_kw):
        if isinstance(texts, list):
            return [[0.1] * 8 for _ in texts]
        return [[0.1] * 8]

    async def embed_batch(self, texts, **_kw):
        return [[0.1] * 8 for _ in texts]


def _resolver_llm():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/judge-model"
    cfg.model_name = "mock/judge-model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw):
        purpose = kw.get("purpose", "")
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if purpose == "multi_query":
            return {
                "text": '["alt"]',
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        if "phân loại intent" in joined:
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


def _base_state(*, grounding_enabled: bool):
    return {
        "tenant_id": uuid4(),
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": "câu hỏi mẫu",
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
            "multi_query_enabled": False,
            "multi_query_n_variants": 1,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            "merge_condense_router": True,
            "decompose_enabled": False,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": "mock/model",
            "embedding_dimension": 8,
            "top_k": 10,
            "reranker_enabled": False,
            "rag_rrf_k": 60,
            "lost_in_middle_reorder_enabled": False,
            "grounding_check_enabled": grounding_enabled,
            "grounding_check_threshold": 0.3,
        },
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}


def _build_graph(tracker, vs, resolver, llm):
    from ragbot.orchestration.query_graph import build_graph

    from tests.unit._state_lift_helper import register_active_tracker
    register_active_tracker(tracker)

    return build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )


def test_grounding_check_step_fires_when_enabled():
    """grounding_check step row must be emitted with threshold + model metadata
    when ``grounding_check_enabled=True`` AND guard_output invokes the judge."""
    tracker = _RecordingStepTracker()
    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = _build_graph(tracker, vs, resolver, llm)
    state = _base_state(grounding_enabled=True)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    gc = tracker.by_name("grounding_check")
    assert len(gc) == 1, f"expected 1 grounding_check step, got {len(gc)}"
    md = gc[0].metadata
    assert md.get("threshold") == 0.3, md
    assert md.get("model"), md  # non-empty model id resolved
    assert md.get("messages", 0) >= 1, md
    assert "finish_reason" in md, md  # captured (may be empty string)


def test_grounding_check_step_skipped_when_disabled():
    """grounding_check MUST NOT fire when ``grounding_check_enabled=False``."""
    tracker = _RecordingStepTracker()
    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = _build_graph(tracker, vs, resolver, llm)
    state = _base_state(grounding_enabled=False)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert tracker.by_name("grounding_check") == [], (
        "grounding_check MUST NOT fire when feature is OFF"
    )


# --------------------------------------------------------------------------- #
# 2. hash_lookup_cache + semantic_cache_check — direct cache layer test       #
# --------------------------------------------------------------------------- #


class _StubResult:
    """sqlalchemy.Result-shaped fake returning the configured row."""

    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _StubSession:
    """Async session fake: serves pre-canned rows in order of execute() calls.

    The semantic cache fires the exact-hash SELECT first, then the cosine
    SELECT. Tests configure both rows independently to drive behaviour.
    """

    def __init__(self, rows: list[dict | None]) -> None:
        self._rows = list(rows)

    async def execute(self, *_args, **_kw):
        row = self._rows.pop(0) if self._rows else None
        return _StubResult(row)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None


def _make_cache(rows: list[dict | None]):
    """Build a PgSemanticCache wired to a stub session_factory."""
    from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache

    def _factory():
        return _StubSession(rows)

    return PgSemanticCache(_factory)


def test_hash_lookup_cache_step_emits_with_hit_metadata():
    """When exact-hash hits, hash_lookup_cache step records hit=True
    and the cosine path is short-circuited (NO semantic_cache_check row)."""
    tracker = _RecordingStepTracker()
    cache = _make_cache(
        rows=[
            {
                "answer": "cached",
                "citations": [],
                "model_name": "mock",
                "cached_at_ts": 0,
                "metadata_json": None,  # prod SELECT includes it (2026-05-27 chunks snapshot)
            },
            None,  # cosine row never consumed
        ],
    )

    result = asyncio.run(
        cache.find_similar_with_text(
            query_embedding=[0.1] * 8,
            query_text="bao lâu",
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            bot_version="bv1",
            corpus_version="latest",
            threshold=0.97,
            step_tracker=tracker,
        )
    )

    assert result is not None and result.answer == "cached"
    h = tracker.by_name("hash_lookup_cache")
    assert len(h) == 1, tracker.names()
    assert h[0].metadata.get("hit") is True, h[0].metadata
    assert h[0].metadata.get("source") == "exact_hash", h[0].metadata
    # Cosine SQL skipped on exact-hash hit → no semantic_cache_check row.
    assert tracker.by_name("semantic_cache_check") == [], tracker.names()


def test_semantic_cache_check_step_emits_on_hash_miss_then_cosine_hit():
    """When exact-hash misses and cosine hits, BOTH rows fire in order:
    hash_lookup_cache (hit=False) THEN semantic_cache_check (hit=True)."""
    tracker = _RecordingStepTracker()
    cache = _make_cache(
        rows=[
            None,  # hash miss
            {
                "answer": "cosine cached",
                "citations": [],
                "model_name": "mock",
                "cached_at_ts": 0,
                "score": 0.99,
                "metadata_json": None,  # prod SELECT includes it (2026-05-27 chunks snapshot)
            },
        ],
    )

    result = asyncio.run(
        cache.find_similar_with_text(
            query_embedding=[0.1] * 8,
            query_text="bao lâu",
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            bot_version="bv1",
            corpus_version="latest",
            threshold=0.97,
            step_tracker=tracker,
        )
    )

    assert result is not None and result.answer == "cosine cached"
    names = tracker.names()
    assert names == ["hash_lookup_cache", "semantic_cache_check"], names
    h, s = tracker.by_name("hash_lookup_cache")[0], tracker.by_name("semantic_cache_check")[0]
    assert h.metadata.get("hit") is False, h.metadata
    assert s.metadata.get("hit") is True, s.metadata
    assert s.metadata.get("threshold") == 0.97, s.metadata
    assert s.metadata.get("score", 0.0) > 0.0, s.metadata


def test_cache_steps_no_op_when_step_tracker_omitted():
    """Backward-compat: callers that don't pass step_tracker get zero rows
    (the wrap is purely opt-in; no behavioural change for legacy callers)."""
    tracker = _RecordingStepTracker()  # never wired into the cache
    cache = _make_cache(
        rows=[None, None],  # full miss
    )

    result = asyncio.run(
        cache.find_similar_with_text(
            query_embedding=[0.1] * 8,
            query_text="bao lâu",
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            bot_version="bv1",
            corpus_version="latest",
            threshold=0.97,
            # NO step_tracker kwarg
        )
    )

    assert result is None
    assert tracker.names() == [], "tracker MUST stay empty when not passed in"


def test_cache_steps_record_full_miss():
    """Both step rows still fire on a full miss, recording hit=False each."""
    tracker = _RecordingStepTracker()
    cache = _make_cache(rows=[None, None])

    result = asyncio.run(
        cache.find_similar_with_text(
            query_embedding=[0.1] * 8,
            query_text="bao lâu",
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            bot_version="bv1",
            corpus_version="latest",
            threshold=0.97,
            step_tracker=tracker,
        )
    )

    assert result is None
    names = tracker.names()
    assert names == ["hash_lookup_cache", "semantic_cache_check"], names
    for ctx in tracker.steps:
        assert ctx.metadata.get("hit") is False, (ctx.name, ctx.metadata)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
