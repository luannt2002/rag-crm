"""[T1-Smartness] Tests for stats-index + doc-summary routing in retrieve node.

Verifies that when intent is aggregation/comparison and a price-range filter
is parsed, the retrieve node uses stats_index_repo instead of vector retrieve.
Also verifies fallback behaviour and doc-summary routing.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.orchestration import query_graph as qg


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _RecordingStepCtx:
    def __init__(self) -> None:
        self.metadata: dict = {}
        self._calls: list[str] = []

    def set_metadata(self, **kwargs) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs) -> None:
        return None

    def record_llm(self, **_kw) -> None:
        pass

    def record(self, **_kwargs) -> None:
        return None


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.last_ctx: _RecordingStepCtx | None = None

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx()
        self.last_ctx = ctx
        yield ctx


def _make_llm_and_resolver():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock()
    cfg.provider.name = "mock-provider"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "answer", "prompt_tokens": 1, "completion_tokens": 1,
        "cost_usd": 0.0, "finish_reason": "stop",
    })
    return resolver, llm


def _base_state(tracker: _RecordingStepTracker, *, intent: str = "aggregation") -> dict:
    return {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "workspace_id": "default",
        "query": "dưới 2tr có bao nhiêu dịch vụ",
        "rewritten_query": None,
        "sub_queries": [],
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "intent": intent,
        "intent_confidence": 0.9,
        "pipeline_config": {},
        "step_tracker": tracker,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


def _build_compiled(*, stats_index_repo=None, doc_repo=None,
                    vector_store=None):
    resolver, llm = _make_llm_and_resolver()
    compiled = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        llm=llm,
        model_resolver=resolver,
        vector_store=vector_store,
        stats_index_repo=stats_index_repo,
        doc_repo=doc_repo,
    )
    return compiled


def _invoke_retrieve(compiled, state: dict) -> dict:
    pregel_node = compiled.nodes["retrieve"]
    runnable = pregel_node.bound
    return asyncio.run(runnable.ainvoke(state))


# ---------------------------------------------------------------------------
# 1. aggregation + range → stats_index path
# ---------------------------------------------------------------------------


def test_aggregation_with_range_routes_to_stats_index() -> None:
    """When intent=aggregation and query has a price range, retrieve uses
    stats_index_repo instead of vector retrieve."""
    cid_a, cid_b = str(uuid4()), str(uuid4())
    fake_entities = [
        {"entity_name": "Dịch vụ A", "price_primary": 1_500_000,
         "record_chunk_id": cid_a},
        {"entity_name": "Dịch vụ B", "price_primary": 900_000,
         "record_chunk_id": cid_b},
    ]
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)
    # 2026-05-28: stats_index only emits when it can link chunks (else falls
    # through to hybrid to avoid an empty retrieve → faith failure). Provide a
    # doc_repo so linked_chunks is non-empty and the stats path completes.
    doc_repo = MagicMock()
    doc_repo.find_chunks_by_ids = AsyncMock(
        return_value=[{"content": "c", "chunk_id": cid_a, "score": 1.0}]
    )
    doc_repo.fetch_summaries_by_bot = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    compiled = _build_compiled(stats_index_repo=stats_repo, doc_repo=doc_repo)

    result = _invoke_retrieve(compiled, state)

    # stats_index_repo.query_by_price_range was called
    stats_repo.query_by_price_range.assert_called_once()
    call_kwargs = stats_repo.query_by_price_range.call_args.kwargs
    # Multi-tenant: record_bot_id passed
    assert "record_bot_id" in call_kwargs
    # Correct range for "dưới 2tr"
    assert call_kwargs["price_max"] == 2_000_000
    assert call_kwargs["price_min"] is None

    # stats_entities returned in result
    assert result.get("stats_entities") == fake_entities
    assert result.get("retrieve_mode") == "stats_index"


def test_aggregation_with_range_metadata_set_on_step_ctx() -> None:
    """Step context metadata records source=stats_index + entity_count."""
    cid = str(uuid4())
    fake_entities = [
        {"entity_name": "X", "price_primary": 500_000, "record_chunk_id": cid},
    ]
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)
    # stats_index emits source=stats_index only when chunks link (2026-05-28).
    doc_repo = MagicMock()
    doc_repo.find_chunks_by_ids = AsyncMock(
        return_value=[{"content": "c", "chunk_id": cid, "score": 1.0}]
    )
    doc_repo.fetch_summaries_by_bot = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    compiled = _build_compiled(stats_index_repo=stats_repo, doc_repo=doc_repo)

    _invoke_retrieve(compiled, state)

    assert tracker.last_ctx is not None
    assert tracker.last_ctx.metadata.get("source") == "stats_index"
    assert tracker.last_ctx.metadata.get("entity_count") == 1


# ---------------------------------------------------------------------------
# 2. aggregation + no range → fallback to vector retrieve
# ---------------------------------------------------------------------------


def test_aggregation_no_range_falls_back_to_vector() -> None:
    """When intent=aggregation but query has no price range, do NOT call
    stats_index_repo; vector retrieve runs normally (returns [] on null store)."""
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    # Override query to one with no range signal
    state["query"] = "các dịch vụ phổ biến nhất"

    compiled = _build_compiled(stats_index_repo=stats_repo)
    result = _invoke_retrieve(compiled, state)

    # stats_index_repo NOT called because no range was parsed
    stats_repo.query_by_price_range.assert_not_called()
    assert result.get("retrieve_mode") != "stats_index"


# ---------------------------------------------------------------------------
# 3. Stats route triggers on parser confidence, NOT on intent label.
#
# This was the original Wave M4 contract — "only intent=aggregation/comparison
# triggers stats route". The contract was lifted 2026-05-26 (post-F-wave) so
# that range queries which heuristic-classify as factoid/None ("dưới 1 triệu
# có dịch vụ gì", "dịch vụ nào dưới 800 nghìn") still take the fast SQL path
# instead of the 15-20s vector + multi-query + LLM detour. The trigger is now
# (range_filter is not None AND confidence >= threshold), which is intent-
# independent and domain-neutral.
# ---------------------------------------------------------------------------


def test_factoid_intent_routes_to_stats_when_range_parsed() -> None:
    """factoid intent + parseable range → MUST route to stats (post-lift).

    Pre-lift this test asserted ``assert_not_called`` because the gate was
    ``intent in (AGGREGATION, COMPARISON)``. Post-lift, parser confidence is
    the sole gate so factoid + clear range routes to stats for the latency
    win without any HALLU regression (stats returns deterministic entities).
    """
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="factoid")

    compiled = _build_compiled(stats_index_repo=stats_repo)
    _invoke_retrieve(compiled, state)

    stats_repo.query_by_price_range.assert_called_once()


def test_greeting_intent_routes_to_stats_when_range_parsed() -> None:
    """greeting + range signal → MUST route to stats (post-lift).

    "xin chào dưới 2tr" has a clean numeric filter; the parser flags it with
    confidence 0.85. The architecture treats the filter as authoritative —
    if the user typed a price band, the SQL path is the answer regardless of
    what the heuristic intent classifier thought of the greeting suffix.
    """
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="greeting")
    state["query"] = "xin chào dưới 2tr"  # parser still extracts price_max=2_000_000

    compiled = _build_compiled(stats_index_repo=stats_repo)
    _invoke_retrieve(compiled, state)

    stats_repo.query_by_price_range.assert_called_once()


# ---------------------------------------------------------------------------
# 4. stats_index_repo is None → no routing
# ---------------------------------------------------------------------------


def test_no_stats_repo_falls_back_to_vector() -> None:
    """When stats_index_repo is None, the pipeline runs normal vector retrieve."""
    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    # vector_store=None → retrieve returns [] immediately without calling stats
    compiled = _build_compiled(stats_index_repo=None)
    result = _invoke_retrieve(compiled, state)

    assert result.get("retrieve_mode") != "stats_index"
    assert result.get("stats_entities") is None


# ---------------------------------------------------------------------------
# 5. stats_index returns empty list → fallback to vector
# ---------------------------------------------------------------------------


def test_stats_index_empty_falls_back_to_vector() -> None:
    """When stats_index_repo returns [], the node does NOT return early;
    vector retrieve runs next (returns [] because vector_store=None)."""
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    compiled = _build_compiled(stats_index_repo=stats_repo)
    result = _invoke_retrieve(compiled, state)

    stats_repo.query_by_price_range.assert_called_once()
    assert result.get("retrieve_mode") != "stats_index"
    assert result.get("stats_entities") is None


# ---------------------------------------------------------------------------
# 6. stats_index failure → graceful degrade to vector
# ---------------------------------------------------------------------------


def test_stats_index_repo_exception_falls_back_to_vector() -> None:
    """If stats_index_repo raises, retrieve must not propagate the error;
    it logs and falls through to vector retrieve."""
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(side_effect=RuntimeError("DB down"))

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    compiled = _build_compiled(stats_index_repo=stats_repo)

    # Should not raise
    result = _invoke_retrieve(compiled, state)
    assert result.get("retrieve_mode") != "stats_index"


# ---------------------------------------------------------------------------
# 7. linked chunks are fetched when record_chunk_id present
# ---------------------------------------------------------------------------


def test_stats_index_chunks_linked_to_evidence() -> None:
    """When entities have record_chunk_id, find_chunks_by_ids is called on
    doc_repo and the result is placed in retrieved_chunks."""
    chunk_uuid = str(uuid4())
    fake_entities = [
        {"entity_name": "Dịch vụ A", "price_primary": 800_000,
         "record_chunk_id": chunk_uuid},
    ]
    fake_chunks = [{"content": "chunk text", "chunk_id": chunk_uuid, "score": 1.0}]

    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)

    doc_repo = MagicMock()
    doc_repo.find_chunks_by_ids = AsyncMock(return_value=fake_chunks)
    # fetch_summaries_by_bot must NOT be called (query has no summary signal)
    doc_repo.fetch_summaries_by_bot = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    compiled = _build_compiled(stats_index_repo=stats_repo, doc_repo=doc_repo)

    result = _invoke_retrieve(compiled, state)

    # The stats route now PREPENDS a synthetic chunk built from the filtered
    # entities (name + price) so the LLM answers from the clean aggregation
    # result, then appends the evidence-linked chunks. graded_chunks is seeded
    # too so the stats→generate route can skip rerank/grade.
    retrieved = result.get("retrieved_chunks")
    assert retrieved[0]["source"] == "stats_index"
    assert "Dịch vụ A" in retrieved[0]["content"]
    assert fake_chunks[0] in retrieved
    assert result.get("graded_chunks") == retrieved
    doc_repo.find_chunks_by_ids.assert_called_once()


# ---------------------------------------------------------------------------
# 8. doc-summary routing
# ---------------------------------------------------------------------------


def test_summary_query_routes_to_doc_summary() -> None:
    """A query containing 'tóm tắt' routes to doc_repo.fetch_summaries_by_bot
    and returns synthetic chunks — no vector retrieve call."""
    fake_docs = [
        {"id": str(uuid4()), "document_name": "Doc A",
         "summary_json": {"summary": "Tóm tắt nội dung Doc A"}},
    ]
    doc_repo = MagicMock()
    doc_repo.fetch_summaries_by_bot = AsyncMock(return_value=fake_docs)

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="factoid")
    state["query"] = "tóm tắt nội dung tài liệu"

    compiled = _build_compiled(doc_repo=doc_repo)
    result = _invoke_retrieve(compiled, state)

    doc_repo.fetch_summaries_by_bot.assert_called_once()
    assert result.get("retrieve_mode") == "doc_summary"
    chunks = result.get("retrieved_chunks") or []
    assert len(chunks) == 1
    assert chunks[0]["content"] == "Tóm tắt nội dung Doc A"
    assert chunks[0]["source"] == "doc_summary"


def test_non_summary_query_does_not_call_fetch_summaries() -> None:
    """A regular factoid query must not trigger doc-summary route."""
    doc_repo = MagicMock()
    doc_repo.fetch_summaries_by_bot = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="factoid")
    state["query"] = "giá dịch vụ X là bao nhiêu"

    compiled = _build_compiled(doc_repo=doc_repo)
    _invoke_retrieve(compiled, state)

    doc_repo.fetch_summaries_by_bot.assert_not_called()


# ---------------------------------------------------------------------------
# 9. comparison intent also routes to stats_index
# ---------------------------------------------------------------------------


def test_comparison_intent_with_range_routes_to_stats_index() -> None:
    """intent=comparison triggers the same stats-index path as aggregation."""
    cid = str(uuid4())
    fake_entities = [
        {"entity_name": "Svc X", "price_primary": 1_200_000, "record_chunk_id": cid},
    ]
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)
    # stats_index emits only when chunks link (2026-05-28); supply doc_repo.
    doc_repo = MagicMock()
    doc_repo.find_chunks_by_ids = AsyncMock(
        return_value=[{"content": "c", "chunk_id": cid, "score": 1.0}]
    )
    doc_repo.fetch_summaries_by_bot = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="comparison")

    compiled = _build_compiled(stats_index_repo=stats_repo, doc_repo=doc_repo)
    result = _invoke_retrieve(compiled, state)

    stats_repo.query_by_price_range.assert_called_once()
    assert result.get("stats_entities") == fake_entities


# ---------------------------------------------------------------------------
# 10. tenant isolation: record_bot_id always passed to stats_index_repo
# ---------------------------------------------------------------------------


def test_stats_index_receives_record_bot_id_for_tenant_isolation() -> None:
    """The stats_index_repo call MUST include record_bot_id so results are
    scoped to the requesting bot — prevents cross-tenant data leak."""
    bot_uuid = uuid4()
    fake_entities = [{"entity_name": "X", "price_primary": 100_000, "record_chunk_id": None}]
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)

    tracker = _RecordingStepTracker()
    state = _base_state(tracker, intent="aggregation")
    state["record_bot_id"] = bot_uuid

    compiled = _build_compiled(stats_index_repo=stats_repo)
    _invoke_retrieve(compiled, state)

    call_kwargs = stats_repo.query_by_price_range.call_args.kwargs
    assert call_kwargs["record_bot_id"] == bot_uuid
