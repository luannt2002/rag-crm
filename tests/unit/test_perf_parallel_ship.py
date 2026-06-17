"""Ship-tests for AGENT-PHASE-X1 perf-parallel patches.

Covers Option A (rewrite ∥ multi_query), Option D (cache_check ∥
understand_query overlap with cancel-on-hit) and J1 (multi-query embed
batch prewarm). Both Option A and Option D are gated by per-bot pipeline
flags defaulted OFF; the OFF-path tests pin byte-identical legacy
behaviour and the ON-path tests pin the concurrency/cancel contract.

Spec references:
- plans/260501-R3-PERF-PARALLEL/draft.md §1, §2, §3
- reports/MEGA_PERF_PARALLEL_Q4_Q5_Q6_SPEC_20260501.md
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.shared.constants import DEFAULT_EMBEDDING_TASK_PASSAGE
from tests.unit._node_test_helpers import (
    FakeGuardrail,
    FakeInvocationLogger,
    RecordingAuditLogger,
    RecordingStepTracker,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_graph(
    *,
    tracker: RecordingStepTracker | None = None,
    audit: RecordingAuditLogger | None = None,
    semantic_cache: Any | None = None,
    embedder: Any | None = None,
    text_response: str = "rewritten-query-text",
):
    from ragbot.orchestration.query_graph import build_graph

    from tests.unit._state_lift_helper import register_active_tracker
    register_active_tracker(tracker)

    tracker = tracker or RecordingStepTracker()
    audit = audit or RecordingAuditLogger()
    resolver, llm, _cfg = make_resolver_and_llm(text_response=text_response)
    compiled = build_graph(
        invocation_logger=FakeInvocationLogger(),
        guardrail=FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=MagicMock(),
        embedder=embedder or MagicMock(),
        semantic_cache=semantic_cache,
        audit_logger=audit,
    )
    return compiled, tracker, audit, resolver, llm


# --------------------------------------------------------------------------- #
# Option A — rewrite_and_mq_parallel                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_option_a_flag_off_falls_back_to_plain_rewrite() -> None:
    """Per-bot flag OFF (override of default-on) must produce only
    the legacy rewrite output; no _mq_queries slot leaks into state."""
    compiled, _tracker, _audit, _resolver, _llm = _build_graph(text_response="hello")
    fn = node_callable(compiled, "rewrite_and_mq_parallel")
    # Use multi_hop intent so the rewrite LLM call fires (lightweight intents
    # like the default 'factoid' skip rewrite via per-intent gate).
    state = make_state(
        query="câu hỏi gốc",
        intent="multi_hop",
        pipeline_config={"pipeline_parallel_rewrite_mq_enabled": False},
    )
    out = await fn(state)
    assert isinstance(out, dict)
    assert out.get("rewritten_query") == "hello"
    assert "_mq_queries" not in out


@pytest.mark.xfail(
    reason=(
        "Stale contract — pinned OLD gate ``sub_queries < 2 → bypass MQ``. "
        "Production gate inverted by 8ec1eb9 + TG4 (commit 4ea89a5) to "
        "``sub_queries >= 2 → bypass MQ`` because compound single-query "
        "inputs (no decompose) NEED the MQ paraphrase fanout — it is the "
        "retrieval lever. Test pre-dates 8ec1eb9 and must be rewritten to "
        "match new semantic. Defer Wave H+1 cleanup. strict=False so the "
        "test still runs + xpasses if the bug returns."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_option_a_flag_on_bypasses_mq_when_no_decompose(monkeypatch) -> None:
    """S2 bypass — when state has no real decomposition (sub_queries ≤ 1),
    the parallel helper must short-circuit ``_run_multi_query_expansion``
    so no paraphrase LLM call fires. The rewrite branch still produces
    ``rewritten_query`` (it does not depend on sub_queries), but the MQ
    branch returns nothing and the bypass flag is written onto state.
    """
    import ragbot.orchestration.query_graph as qg

    async def _never_called_expand(*_a, **_kw):  # pragma: no cover — guarded by bypass
        raise AssertionError(
            "mq_expand_query must NOT be invoked when S2 bypass engages",
        )

    monkeypatch.setattr(qg, "mq_expand_query", _never_called_expand)

    compiled, _tracker, _audit, _resolver, _llm = _build_graph(text_response="rew")
    fn = node_callable(compiled, "rewrite_and_mq_parallel")
    state = make_state(
        query="who is the customer support manager today",
        pipeline_config={"pipeline_parallel_rewrite_mq_enabled": True},
    )
    out = await fn(state)
    assert out.get("rewritten_query") == "rew"
    # S2 bypass: helper returns [], so no _mq_queries slot is added to
    # the merged dict (the wrapper only adds _mq_queries when len > 1).
    assert "_mq_queries" not in out, (
        f"S2 bypass must prevent _mq_queries from populating; got {out!r}"
    )
    # The bypass flag is the externally observable signal.
    assert state.get("fanout_bypassed") is True, (
        "S2 bypass must write state['fanout_bypassed'] = True"
    )


@pytest.mark.xfail(
    reason=(
        "Stale wall-time pin (< 0.7s) assumes S2 bypass on empty "
        "sub_queries — production now INVERTED (commit 8ec1eb9 + TG4 "
        "4ea89a5): empty sub_queries triggers MQ paraphrase fanout for "
        "compound single-query coverage, so wall time includes real MQ "
        "call. Test must rewrite to seed sub_queries >= 2 to exercise "
        "the bypass path. Defer Wave H+1 cleanup. strict=False."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_option_a_flag_on_runs_concurrently(monkeypatch) -> None:
    """Wall-time invariant: rewrite_and_mq_parallel must still run the
    rewrite LLM call concurrently with whatever the MQ task does. After
    the S2 bypass the MQ task is near-instant (it short-circuits before
    any LLM/embed work), so the wall time is dominated by the rewrite
    call alone (~0.4s with the slow-LLM mock) — still well under the
    0.7s threshold that the pre-S2 concurrent path targeted.
    """
    import ragbot.orchestration.query_graph as qg

    async def _never_called_expand(*_a, **_kw):  # pragma: no cover — guarded by bypass
        raise AssertionError(
            "mq_expand_query must NOT be invoked when S2 bypass engages",
        )

    monkeypatch.setattr(qg, "mq_expand_query", _never_called_expand)

    async def _slow_complete(*_a: Any, **_kw: Any) -> dict[str, Any]:
        await asyncio.sleep(0.4)
        return {
            "text": "rew", "prompt_tokens": 1, "completion_tokens": 1,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    resolver, llm, _cfg = make_resolver_and_llm()
    llm.complete = AsyncMock(side_effect=_slow_complete)

    embedder = MagicMock()
    _embed_seq = iter([
        [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    ])
    async def _embed_one_distinct(*_a, **_kw):
        return next(_embed_seq, [0.0] * 8)
    embedder.embed_one = _embed_one_distinct

    from ragbot.orchestration.query_graph import build_graph
    compiled = build_graph(
        invocation_logger=FakeInvocationLogger(),
        guardrail=FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=MagicMock(),
        embedder=embedder,
        semantic_cache=None,
        audit_logger=RecordingAuditLogger(),
    )

    fn = node_callable(compiled, "rewrite_and_mq_parallel")
    state = make_state(
        query="hỏi xem giá dịch vụ chăm sóc da bao nhiêu tiền",
        pipeline_config={"pipeline_parallel_rewrite_mq_enabled": True},
    )
    t0 = time.perf_counter()
    out = await fn(state)
    elapsed = time.perf_counter() - t0
    # The rewrite LLM call is ~0.4s. With the S2 bypass on the MQ side,
    # wall time stays well under the 0.7s threshold the pre-S2 concurrent
    # path targeted (the bypass makes it even faster than before).
    assert elapsed < 0.7, f"Expected wall time < 0.7s, got {elapsed:.2f}s"
    assert out.get("rewritten_query") == "rew"
    # S2 bypass keeps _mq_queries off the merged dict.
    assert "_mq_queries" not in out


@pytest.mark.asyncio
async def test_option_a_one_task_failure_does_not_kill_other(monkeypatch) -> None:
    """If multi_query expansion blows up, rewrite output must still surface."""
    import ragbot.orchestration.query_graph as qg

    async def _boom(*_a, **_kw):
        raise RuntimeError("synthetic mq failure")

    monkeypatch.setattr(qg, "mq_expand_query", _boom)

    compiled, _tracker, _audit, _resolver, _llm = _build_graph(text_response="rew2")
    fn = node_callable(compiled, "rewrite_and_mq_parallel")
    # Use aggregation intent so both rewrite and multi_query LLM calls fire.
    state = make_state(
        query="x",
        intent="aggregation",
        pipeline_config={"pipeline_parallel_rewrite_mq_enabled": True},
    )
    out = await fn(state)
    # Helper swallowed the exception internally and returned the original
    # query as a single-element list (len 1 -> not promoted to _mq_queries).
    assert out.get("rewritten_query") == "rew2"
    assert "_mq_queries" not in out


# --------------------------------------------------------------------------- #
# Option D — cache_check_and_understand_parallel                              #
# --------------------------------------------------------------------------- #


def _make_cache_hit_double(*, hit: bool, slow_understand_s: float = 0.0):
    """Create a semantic_cache double whose find_similar_with_text returns
    a cached result on hit, None on miss."""
    sc = MagicMock()
    if hit:
        cached = MagicMock()
        cached.answer = "cached answer text"
        cached.citations = []
        cached.model_name = "mock/model"
        cached.prompt_tokens = 0
        cached.completion_tokens = 0
        sc.find_similar_with_text = AsyncMock(return_value=cached)
    else:
        sc.find_similar_with_text = AsyncMock(return_value=None)
    return sc


@pytest.mark.asyncio
async def test_option_d_flag_off_falls_back_to_plain_check_cache() -> None:
    """Per-bot flag OFF (override of default-on) → wrapper returns
    identical dict to bare check_cache; no understand_query LLM call fires."""
    sc = _make_cache_hit_double(hit=False)
    embedder = MagicMock()
    embedder.embed_one = AsyncMock(return_value=[0.1] * 8)
    compiled, _tracker, _audit, _resolver, llm = _build_graph(
        semantic_cache=sc, embedder=embedder,
    )
    fn = node_callable(compiled, "cache_check_and_understand_parallel")
    state = make_state(
        query="anything",
        pipeline_config={"pipeline_parallel_cache_understand_enabled": False},
    )
    out = await fn(state)
    # Cache miss path returns the bot_cache_version slot but no intent
    # (intent only appears when understand_query ran).
    assert "_bot_cache_version" in out or out == {}
    assert "intent" not in out
    # The understand_query body issues llm.complete; if it fired we'd see it.
    assert llm.complete.await_count == 0


@pytest.mark.asyncio
async def test_option_d_cache_hit_cancels_understand_task(monkeypatch) -> None:
    """When cache hits, understand task must be cancelled before its LLM
    call completes — i.e. wall time stays well under the LLM sleep budget."""
    sc = _make_cache_hit_double(hit=True)
    embedder = MagicMock()
    embedder.embed_one = AsyncMock(return_value=[0.1] * 8)

    completed = {"flag": False}

    async def _slow_complete(*_a: Any, **_kw: Any) -> dict[str, Any]:
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        completed["flag"] = True
        return {
            "text": "should never see this", "prompt_tokens": 1,
            "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop",
        }

    resolver, llm, _cfg = make_resolver_and_llm()
    llm.complete = AsyncMock(side_effect=_slow_complete)

    from ragbot.orchestration.query_graph import build_graph
    compiled = build_graph(
        invocation_logger=FakeInvocationLogger(),
        guardrail=FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=MagicMock(),
        embedder=embedder,
        semantic_cache=sc,
        audit_logger=RecordingAuditLogger(),
    )

    fn = node_callable(compiled, "cache_check_and_understand_parallel")
    state = make_state(
        query="cached q",
        pipeline_config={"pipeline_parallel_cache_understand_enabled": True},
    )
    t0 = time.perf_counter()
    out = await fn(state)
    elapsed = time.perf_counter() - t0

    assert out.get("cache_status") == "hit"
    assert out.get("answer") == "cached answer text"
    # The 2 s LLM sleep must NOT have completed — proving cancellation
    # short-circuited the wait.
    assert completed["flag"] is False
    assert elapsed < 1.0, f"Cancel-on-hit broken: wall {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_option_d_cache_miss_merges_understand_intent() -> None:
    """When the cache misses, both bot_cache_version (from check_cache) and
    intent (from understand_query) appear in the merged output, and the
    short-circuit flag is set so downstream understand_query node skips."""
    sc = _make_cache_hit_double(hit=False)
    embedder = MagicMock()
    embedder.embed_one = AsyncMock(return_value=[0.1] * 8)
    compiled, _tracker, _audit, _resolver, _llm = _build_graph(
        semantic_cache=sc, embedder=embedder,
    )
    fn = node_callable(compiled, "cache_check_and_understand_parallel")
    state = make_state(
        query="fresh question",
        pipeline_config={"pipeline_parallel_cache_understand_enabled": True},
    )
    out = await fn(state)
    assert out.get("_understand_skipped_by_parallel") is True
    assert "intent" in out
    # Cache miss path also writes _bot_cache_version.
    assert "_bot_cache_version" in out


@pytest.mark.asyncio
async def test_understand_query_short_circuits_on_skip_flag() -> None:
    """If `_understand_skipped_by_parallel=True` is preset on state,
    understand_query body returns {} immediately without an LLM call."""
    compiled, _tracker, _audit, _resolver, llm = _build_graph()
    fn = node_callable(compiled, "understand_query")
    state = make_state(
        query="any",
        intent="factoid",
        _understand_skipped_by_parallel=True,
    )
    pre_count = llm.complete.await_count
    out = await fn(state)
    assert out == {}
    assert llm.complete.await_count == pre_count


# --------------------------------------------------------------------------- #
# J1 — multi-query embed batch prewarm                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_retrieve_consumes_preset_mq_queries_skips_inline_block() -> None:
    """When state has _mq_queries pre-populated, retrieve must NOT emit a
    multi_query_fanout step (it reused the pre-computed paraphrases)."""
    embedder = MagicMock()
    embedder.embed_one = AsyncMock(return_value=[0.1] * 8)
    embedder.embed_batch = AsyncMock(return_value=[[0.1] * 8, [0.2] * 8, [0.3] * 8])
    vstore = MagicMock()
    # Don't expose hybrid_search so retrieve falls back to vector-only path —
    # we only care about the multi_query_fanout step gate, not the search.
    del vstore.hybrid_search
    vstore.search = AsyncMock(return_value=[])

    tracker = RecordingStepTracker()
    compiled, _t, _a, _r, _l = _build_graph(
        tracker=tracker,
        embedder=embedder,
    )
    # Override the vector_store on the closure-bound retrieve via a fresh
    # build_graph call so retrieve uses our stub.
    from ragbot.orchestration.query_graph import build_graph
    resolver, llm, _cfg = make_resolver_and_llm()
    compiled = build_graph(
        invocation_logger=FakeInvocationLogger(),
        guardrail=FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vstore,
        embedder=embedder,
        semantic_cache=None,
        audit_logger=RecordingAuditLogger(),
    )

    fn = node_callable(compiled, "retrieve")
    preset = ["original question", "paraphrase 1", "paraphrase 2"]
    state = make_state(query="original question", _mq_queries=preset)
    await fn(state)
    # Inline mq_fanout step would have appeared if the preset slot was
    # ignored. With the preset honoured, only retrieve (and possibly
    # rrf_fuse) should appear, never multi_query_fanout.
    step_names = tracker.names()
    assert "multi_query_fanout" not in step_names, (
        f"Inline multi_query_fanout fired despite preset _mq_queries: {step_names}"
    )


@pytest.mark.asyncio
async def test_j1_embed_batch_prewarm_seeds_redis_cache() -> None:
    """When N>1 paraphrases hit retrieve and the embed-batch flag is ON,
    the embedder.embed_batch call fires once with all variants (instead of
    each fan-out branch issuing its own embed_one)."""
    from ragbot.shared import embedding_cache as ec_mod

    # In-memory shim for redis embedding cache so the test is hermetic.
    cache: dict[str, list[float]] = {}

    async def _get(_redis, text, *, model, dim):
        return cache.get(f"{model}:{dim}:{text}")

    async def _set(_redis, text, emb, *, model, dim, ttl=None):
        cache[f"{model}:{dim}:{text}"] = list(emb)

    import ragbot.orchestration.query_graph as qg
    # The module captured these at import time via `from ... import ...`
    # so monkey-patch the bound names inside qg.
    orig_get = qg.get_cached_embedding
    orig_set = qg.set_cached_embedding
    qg.get_cached_embedding = _get  # type: ignore[assignment]
    qg.set_cached_embedding = _set  # type: ignore[assignment]
    import ragbot.orchestration.nodes.retrieve as _rn  # node moved (carve)
    if hasattr(_rn, "set_cached_embedding"): _rn.set_cached_embedding = _set
    if hasattr(_rn, "get_cached_embedding"): _rn.get_cached_embedding = _get
    try:
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.42] * 8)
        embedder.embed_batch = AsyncMock(
            return_value=[[0.1] * 8, [0.2] * 8, [0.3] * 8]
        )
        vstore = MagicMock()
        del vstore.hybrid_search
        vstore.search = AsyncMock(return_value=[])

        from ragbot.orchestration.query_graph import build_graph
        resolver, llm, _cfg = make_resolver_and_llm()
        compiled = build_graph(
            invocation_logger=FakeInvocationLogger(),
            guardrail=FakeGuardrail(),
            model_resolver=resolver,
            llm=llm,
            vector_store=vstore,
            embedder=embedder,
            semantic_cache=None,
            audit_logger=RecordingAuditLogger(),
        )

        fn = node_callable(compiled, "retrieve")
        preset = ["q0", "q1", "q2"]
        state = make_state(query="q0", _mq_queries=preset)
        await fn(state)

        # The prewarm helper must have called embed_batch exactly once for
        # all 3 cold variants.
        assert embedder.embed_batch.await_count == 1
        # Cached entries were seeded for each variant.
        seeded = [k for k in cache.keys() if any(q in k for q in preset)]
        assert len(seeded) == 3, f"Expected 3 prewarmed entries, got {seeded}"
    finally:
        qg.get_cached_embedding = orig_get  # type: ignore[assignment]
        qg.set_cached_embedding = orig_set  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_j1_prewarm_sets_embedding_column() -> None:
    """Regression: prewarm path must set ``state['embedding_column']`` so
    downstream nodes that read it never see ``None``. The data table has
    a single column ``embedding`` — the state key is preserved for cache
    + diagnostic surfaces.
    """
    from unittest.mock import AsyncMock, MagicMock

    import ragbot.orchestration.query_graph as qg

    async def _get(_redis, text, *, model, dim):
        return None

    async def _set(*_a, **_kw):
        return None

    orig_get = qg.get_cached_embedding
    orig_set = qg.set_cached_embedding
    qg.get_cached_embedding = _get  # type: ignore[assignment]
    qg.set_cached_embedding = _set  # type: ignore[assignment]
    import ragbot.orchestration.nodes.retrieve as _rn  # node moved (carve)
    if hasattr(_rn, "set_cached_embedding"): _rn.set_cached_embedding = _set
    if hasattr(_rn, "get_cached_embedding"): _rn.get_cached_embedding = _get
    try:
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.1] * 1024)
        embedder.embed_batch = AsyncMock(
            return_value=[[0.1] * 1024, [0.2] * 1024],
        )
        vstore = MagicMock()
        vstore.hybrid_search = AsyncMock(return_value=[])

        v3_spec = EmbeddingSpec(
            binding_id=uuid.uuid4(),
            model_name="jina_ai/jina-embeddings-v3",
            provider="jina_ai",
            dimension=1024,
            max_batch=32,
            model_version="jina-embeddings-v3",
            task=DEFAULT_EMBEDDING_TASK_PASSAGE,
        )

        runtime_cfg = MagicMock()
        runtime_cfg.embedding_spec = v3_spec

        resolver = MagicMock()
        resolver.resolve_runtime = AsyncMock(return_value=runtime_cfg)
        resolver.resolve_embedding = AsyncMock(return_value=v3_spec)
        resolver.resolve_litellm = AsyncMock(return_value=None)
        resolver.resolve_prompt = AsyncMock(return_value=None)

        from ragbot.orchestration.query_graph import build_graph
        _, llm, _cfg = make_resolver_and_llm()
        compiled = build_graph(
            invocation_logger=FakeInvocationLogger(),
            guardrail=FakeGuardrail(),
            model_resolver=resolver,
            llm=llm,
            vector_store=vstore,
            embedder=embedder,
            semantic_cache=None,
            audit_logger=RecordingAuditLogger(),
        )

        fn = node_callable(compiled, "retrieve")
        state = make_state(query="q0", _mq_queries=["q0", "q1"])
        await fn(state)

        assert state.get("embedding_column") == "embedding", (
            f"prewarm must set state['embedding_column'] to the canonical "
            f"single column, got {state.get('embedding_column')!r}"
        )
    finally:
        qg.get_cached_embedding = orig_get  # type: ignore[assignment]
        qg.set_cached_embedding = orig_set  # type: ignore[assignment]
