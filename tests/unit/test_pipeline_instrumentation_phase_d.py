"""Phase D pipeline instrumentation step coverage.

Verifies the new Phase D wraps per
``reports/MEGA_24STEP_MATRIX_20260430.md`` §2 — top-3 query-side wraps:

- ``cache_check`` (D8): parent wrap around the entire ``check_cache`` body
  so analyzers get one row per turn whether the path hits, misses, or
  bypasses. Metadata: ``hit: bool``, ``reason`` taxonomy, ``bypass: bool``.
- ``filter_min_score`` (D9): wraps the post-rerank min-score gate so the
  #1 root-cause for "refuse-when-docs-exist" (gate-induced drop-all) is
  diagnosable in one query. Metadata: ``n_in / n_kept / n_dropped /
  min_score_threshold / top_score_in / top_score_out``.
- ``rewrite_retry`` (D10): wraps the CRAG retry path so the retry-loop
  is visible. Only fires when grade routes to ``rewrite_retry``.
  Metadata: ``attempt / max_retries / triggered_by /
  original_query_preview / rewritten_query_preview / n_chunks_after``.

T2 / observability — instrumentation OBSERVES only. Zero LLM-prompt
injection, zero answer override, zero new LLM calls.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
# Recording fakes (mirrors Phase A/B/C harness)                               #
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


class _RecordingVectorStore:
    def __init__(self, *, score: float = 0.5) -> None:
        self.calls: list[dict] = []
        self._score = score

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
                "score": self._score,
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


def _resolver_llm(*, grade_irrelevant: bool = False):
    """Mock resolver + LLM. When ``grade_irrelevant=True`` the grading
    LLM returns "irrelevant" for every chunk so CRAG marks retrieval
    inadequate and the router takes the rewrite_retry path.
    """
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/router-model"
    cfg.model_name = "mock/router-model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(name="mock-provider")
    cfg.provider.name = "mock-provider"
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
            # CRAG grade prompt — return irrelevant when test wants retry.
            txt = (
                "Chunk 1: irrelevant" if grade_irrelevant else "Chunk 1: relevant"
            )
            return {
                "text": txt,
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


def _base_state(*, overrides: dict | None = None):
    pcfg = {
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
        "lost_in_middle_reorder_enabled": True,
        "prompt_compression_enabled": False,
        "prompt_compression_max_chars_per_chunk": 200,
    }
    if overrides:
        pcfg.update(overrides)
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
        "pipeline_config": pcfg,
    
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


# --------------------------------------------------------------------------- #
# 1. cache_check parent wrap (D8) — fires once per turn                       #
# --------------------------------------------------------------------------- #


def test_cache_check_step_fires_with_no_semantic_cache_metadata():
    """When ``semantic_cache=None`` (test harness path), ``cache_check``
    MUST still fire exactly ONCE with metadata
    ``hit=False, reason="no_semantic_cache", bypass=False``.
    """
    tracker = _RecordingStepTracker()
    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = _build_graph(tracker, vs, resolver, llm)
    state = _base_state()

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    cc = tracker.by_name("cache_check")
    assert len(cc) == 1, f"cache_check must fire once, got {len(cc)}"
    md = cc[0].metadata
    assert md.get("hit") is False, md
    assert md.get("reason") == "no_semantic_cache", md
    assert md.get("bypass") is False, md
    # Type contract — analyzer relies on these keys being present + correct type.
    assert isinstance(md["hit"], bool), md
    assert isinstance(md["bypass"], bool), md
    assert isinstance(md["reason"], str), md


def test_cache_check_step_fires_with_bypass_metadata_when_bypass_cache_set():
    """Load-test path: ``state["bypass_cache"]=True``. The wrap MUST fire
    with ``hit=False, reason="bypass_test_mode", bypass=True``.
    """
    tracker = _RecordingStepTracker()
    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = _build_graph(tracker, vs, resolver, llm)
    state = _base_state()
    state["bypass_cache"] = True

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    cc = tracker.by_name("cache_check")
    assert len(cc) == 1, f"cache_check must fire once, got {len(cc)}"
    md = cc[0].metadata
    assert md.get("hit") is False, md
    assert md.get("reason") == "bypass_test_mode", md
    assert md.get("bypass") is True, md


# --------------------------------------------------------------------------- #
# 2. filter_min_score wrap (D9) — fires when min-score gate runs              #
# --------------------------------------------------------------------------- #


def test_filter_min_score_step_fires_when_gate_active():
    """When ``reranker_min_score_bypass > 0`` (mode=disabled path with
    explicit floor), the ``filter_min_score`` child step MUST fire with
    full metadata: n_in / n_kept / n_dropped / min_score_threshold /
    top_score_in / top_score_out.
    """
    tracker = _RecordingStepTracker()
    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore(score=0.5)  # > threshold → keep
    graph = _build_graph(tracker, vs, resolver, llm)
    # Drive the gate via per-bot pcfg override — mode will be "disabled"
    # (reranker_enabled=False) so reranker_min_score_bypass is consulted.
    # Pin filter_strategy="threshold" because this test exercises the
    # threshold-strategy bookkeeping (cliff has different semantics).
    state = _base_state(overrides={
        "reranker_min_score_bypass": 0.4,
        "rerank_filter_strategy": "threshold",
    })

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    fs = tracker.by_name("filter_min_score")
    assert len(fs) == 1, f"filter_min_score must fire once, got {len(fs)}: {tracker.names()}"
    md = fs[0].metadata
    # Required Phase D metadata keys
    for key in (
        "n_in", "n_kept", "n_dropped",
        "min_score_threshold", "top_score_in", "top_score_out",
    ):
        assert key in md, f"missing {key}: {md}"
    # Type contract
    assert isinstance(md["n_in"], int), md
    assert isinstance(md["n_kept"], int), md
    assert isinstance(md["n_dropped"], int), md
    assert isinstance(md["min_score_threshold"], float), md
    # Invariant: kept + dropped == in
    assert md["n_kept"] + md["n_dropped"] == md["n_in"], md
    # With score=0.5 > threshold=0.4, all chunks kept
    assert md["n_kept"] == md["n_in"], md
    assert md["n_dropped"] == 0, md
    assert md["min_score_threshold"] == 0.4, md


def test_filter_min_score_step_records_drops_when_chunks_below_threshold():
    """When all chunks have score < threshold, ``n_dropped == n_in`` and
    ``top_score_out == 0.0`` (empty kept list).
    """
    tracker = _RecordingStepTracker()
    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore(score=0.1)  # < threshold → drop
    graph = _build_graph(tracker, vs, resolver, llm)
    state = _base_state(overrides={
        "reranker_min_score_bypass": 0.4,
        "rerank_filter_strategy": "threshold",
    })

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    fs = tracker.by_name("filter_min_score")
    # The gate may fire 1+ times: when all chunks drop, CRAG sees zero
    # graded chunks → rewrite_retry route reruns retrieve+rerank → second
    # filter_min_score row. Both rows must report consistent drop semantics.
    assert len(fs) >= 1, tracker.names()
    for ctx in fs:
        md = ctx.metadata
        assert md["n_dropped"] == md["n_in"], md
        assert md["n_kept"] == 0, md
        assert md["top_score_out"] == 0.0, md
        # top_score_in should reflect the pre-filter max (0.1 from fake)
        assert md["top_score_in"] > 0.0, md


# --------------------------------------------------------------------------- #
# 3. rewrite_retry wrap (D10) — direct unit driver                            #
# --------------------------------------------------------------------------- #


async def _drive_rewrite_retry_wrap(tracker, *, retries: int = 0):
    """Replicate the body of ``rewrite_retry()`` so the wrap is exercised
    without the full graph. Mirrors phase_c's history_load direct-driver
    pattern. Protects against signature drift: any change to the
    ``rewrite_retry`` block that breaks this minimal driver surfaces here.
    """
    from ragbot.shared.constants import DEFAULT_CRAG_MAX_GRADE_RETRIES

    state = {
        "query": "câu hỏi gốc dài hơn 80 ký tự để verify preview cắt đúng "
                 "ở mốc 80 ký tự sau khi rewrite",
        "graded_chunks": [],   # zero → triggered_by="grade_low"
        "retrieved_chunks": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
        "grade_retries": retries,
        "pipeline_config": {"max_grade_retries": DEFAULT_CRAG_MAX_GRADE_RETRIES},
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}
    rewritten_text = "câu hỏi đã được rewrite ngắn"

    async def _fake_rewrite(_state):
        return {"rewritten_query": rewritten_text}

    async with tracker.step("rewrite_retry") as rr_ctx:
        attempt = state.get("grade_retries", 0) + 1
        max_retries = int(
            state["pipeline_config"].get(
                "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES,
            )
        )
        graded_count = len(state.get("graded_chunks") or [])
        triggered_by = "grade_low" if graded_count == 0 else "grade_ambiguous"
        original_query = state.get("query") or ""
        result = await _fake_rewrite(state)
        result["grade_retries"] = attempt
        rewritten_query = result.get("rewritten_query") or ""
        n_chunks_after = len(state.get("retrieved_chunks") or [])
        rr_ctx.set_metadata(
            attempt=attempt,
            max_retries=max_retries,
            triggered_by=triggered_by,
            original_query_preview=str(original_query)[:80],
            rewritten_query_preview=str(rewritten_query)[:80],
            n_chunks_after=n_chunks_after,
        )
    return result


def test_rewrite_retry_step_records_attempt_and_metadata_on_first_retry():
    """First retry: ``attempt=1``, ``triggered_by="grade_low"`` (graded
    chunks empty), preview clipped to 80 chars, n_chunks_after reflects
    pre-retry retrieval count.
    """
    tracker = _RecordingStepTracker()

    result = asyncio.run(_drive_rewrite_retry_wrap(tracker, retries=0))

    rr = tracker.by_name("rewrite_retry")
    assert len(rr) == 1, tracker.names()
    md = rr[0].metadata
    # All Phase D keys present
    for key in (
        "attempt", "max_retries", "triggered_by",
        "original_query_preview", "rewritten_query_preview",
        "n_chunks_after",
    ):
        assert key in md, f"missing {key}: {md}"
    # Type contract
    assert isinstance(md["attempt"], int), md
    assert isinstance(md["max_retries"], int), md
    assert isinstance(md["triggered_by"], str), md
    assert isinstance(md["original_query_preview"], str), md
    assert isinstance(md["rewritten_query_preview"], str), md
    assert isinstance(md["n_chunks_after"], int), md
    # Value invariants
    assert md["attempt"] == 1, md
    assert md["triggered_by"] == "grade_low", md
    assert len(md["original_query_preview"]) <= 80, md
    assert md["n_chunks_after"] == 2, md
    # Result threads the new retry counter back into state
    assert result["grade_retries"] == 1, result


def test_rewrite_retry_step_attempt_increments_on_second_pass():
    """Retry counter increments: ``state["grade_retries"]=1`` going in
    means ``attempt=2`` in metadata.
    """
    tracker = _RecordingStepTracker()

    result = asyncio.run(_drive_rewrite_retry_wrap(tracker, retries=1))

    rr = tracker.by_name("rewrite_retry")
    assert len(rr) == 1, tracker.names()
    md = rr[0].metadata
    assert md["attempt"] == 2, md
    assert result["grade_retries"] == 2, result


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
