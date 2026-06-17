"""Integration-style tests for the full chat pipeline.

Tests verify that the LangGraph pipeline flows correctly end-to-end using
mocks — no real DB, LLM, or vector store required.  Each test builds a
graph via ``build_graph()`` and calls ``graph.ainvoke()`` with tailored
mock behaviour.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.infrastructure.guardrails.local_guardrail import (
    GuardrailBlocked,
    GuardrailHit,
)
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER

# ---------------------------------------------------------------------------
# Shared fakes (same pattern as test_query_graph_build.py)
# ---------------------------------------------------------------------------

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
    """Guardrail that never blocks."""

    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _BlockingGuardrail:
    """Guardrail that always blocks on input."""

    async def check_input(self, *_a, **_kw):
        hit = GuardrailHit(
            rule_id="too_short",
            severity="block",
            action="block",
            details={"response_message": "Yeu cau khong hop le"},
        )
        raise GuardrailBlocked([hit])

    async def check_output(self, *_a, **_kw):
        return []


def _base_state(query: str = "San pham bao hanh may nam?", **overrides) -> dict:
    state = {
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
    state.update(overrides)
    return state


def _make_llm_response(text: str, **kw) -> dict:
    return {
        "text": text,
        "prompt_tokens": kw.get("prompt_tokens", 5),
        "completion_tokens": kw.get("completion_tokens", 5),
        "cost_usd": kw.get("cost_usd", 0.0001),
        "finish_reason": kw.get("finish_reason", "stop"),
    }


def _make_resolver_and_llm(complete_side_effect):
    """Build resolver + llm mocks with a custom side_effect for llm.complete."""
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(api_key="sk-xxx", base_url="http://x", code="mock")
    cfg.params = MagicMock(temperature=0.2, max_tokens=256)
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=complete_side_effect)
    return resolver, llm


def _default_complete(_cfg, messages, **_kw) -> dict:
    """Default LLM mock: factoid intent, all chunks relevant, generic answer."""
    joined = " ".join(m.get("content", "") for m in messages).lower()
    if "phân loại intent" in joined:
        return _make_llm_response('{"query": "test query", "intent": "factoid"}')
    if "phan loai intent" in joined:
        return _make_llm_response("factoid")
    if "relevant" in joined and "irrelevant" in joined:
        return _make_llm_response("Chunk 1: relevant")
    return _make_llm_response("San pham bao hanh 24 thang.")


def _patch_understand_structured(monkeypatch, condensed: str, intent: str):
    """Stub _call_with_schema for understand_query — always returns the given UnderstandOutput.

    Skips usage_sink so the pipeline doesn't try to compute cost against the
    MagicMock pricing fields (would blow up in Decimal conversion).
    """
    from ragbot.application.dto.llm_schemas import UnderstandOutput
    from ragbot.orchestration import query_graph as qg

    parsed = UnderstandOutput(condensed_query=condensed, intent=intent)

    async def _fake(**kw):
        # Only handle UnderstandOutput; let other schemas pass through (return
        # None → caller's fallback path).
        if kw.get("schema") is UnderstandOutput:
            return parsed
        return None

    monkeypatch.setattr(qg, "_call_with_schema", _fake)


def _build_graph(guardrail=None, complete_fn=None, semantic_cache=None, **extra):
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_resolver_and_llm(complete_fn or _default_complete)
    return build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=guardrail or _FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        semantic_cache=semantic_cache,
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. test_full_pipeline_factoid_query
# ---------------------------------------------------------------------------
def test_full_pipeline_factoid_query():
    """Simple factoid query goes through the full pipeline and returns an answer."""
    graph = _build_graph()
    final = asyncio.run(
        graph.ainvoke(
            _base_state("San pham bao hanh may nam?"),
            config={"recursion_limit": 25},
        ),
    )
    # Mindset clean: app no longer injects refusal text — empty is valid contract
    assert "answer" in final, "missing answer key"
    assert final.get("intent") == "factoid"


# ---------------------------------------------------------------------------
# 2. test_pipeline_cache_hit
# ---------------------------------------------------------------------------
def test_pipeline_cache_hit():
    """When semantic cache returns a hit, pipeline skips retrieval+generation."""
    fake_cache = MagicMock()
    fake_cache.find_similar_with_text = AsyncMock(
        return_value=MagicMock(
            answer="Cached answer 24 thang.",
            citations=[{"chunk_id": "aaa"}],
            model_name="openai/gpt-4.1-mini",
        ),
    )
    # store should not be called when serving from cache
    fake_cache.store = AsyncMock()

    # We still need a valid embedder to produce query embeddings for cache lookup.
    # Use spec=[] to prevent MagicMock from auto-creating attributes like embed_one,
    # so _embed_query falls through to the simple `.embed()` path.
    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(semantic_cache=fake_cache, embedder=fake_embedder)
    final = asyncio.run(
        graph.ainvoke(
            _base_state("San pham bao hanh may nam?"),
            config={"recursion_limit": 25},
        ),
    )
    assert final.get("answer") == "Cached answer 24 thang."
    assert final.get("answer_type") == "cache_hit"
    assert final.get("cache_status") == "hit"
    # Original cached model preserved, NOT magic "cache_hit" string.
    assert final.get("model_used") == "openai/gpt-4.1-mini"
    # Cache hit should skip generation — cost stays 0
    assert final.get("cost_usd", 0) == 0.0


# ---------------------------------------------------------------------------
# 3. test_pipeline_out_of_scope
# ---------------------------------------------------------------------------
def test_pipeline_out_of_scope(monkeypatch):
    """Query classified as out_of_scope returns OOS answer and skips retrieval."""
    _patch_understand_structured(monkeypatch, "thoi tiet hom nay", "out_of_scope")

    async def _oos_complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phan loai intent" in joined:
            return _make_llm_response("out_of_scope")
        return _make_llm_response("fallback")

    graph = _build_graph(complete_fn=_oos_complete)
    final = asyncio.run(
        graph.ainvoke(
            _base_state("Thoi tiet hom nay the nao?"),
            config={"recursion_limit": 25},
        ),
    )
    assert final.get("intent") == "out_of_scope"
    # App no longer short-circuits OOS — flows through generate so LLM
    # composes the response per bot persona. answer_type set by generate.
    assert final.get("answer_type") in ("answered", "out_of_scope", "no_context")


# ---------------------------------------------------------------------------
# 4. test_pipeline_empty_retrieval
# ---------------------------------------------------------------------------
def test_pipeline_empty_retrieval():
    """Empty retrieval flows through generate; LLM composes response per persona."""
    # Default _default_complete returns all-relevant grading, but with no chunks
    # the grade node sets retrieval_adequate=False and routes to generate so
    # the LLM (mocked here) produces the answer rather than the application.
    graph = _build_graph()
    final = asyncio.run(
        graph.ainvoke(
            _base_state("San pham khong ton tai xyz?"),
            config={"recursion_limit": 25},
        ),
    )
    # generate sets answer_type="answered"; app does not inject refusal text
    assert final.get("retrieval_adequate") is False
    assert final.get("answer_type") in ("answered", "out_of_scope", "no_context")


# ---------------------------------------------------------------------------
# 5. test_pipeline_all_irrelevant
# ---------------------------------------------------------------------------
def test_pipeline_all_irrelevant():
    """All chunks graded irrelevant triggers CRAG retry or OOS answer."""

    call_count = {"n": 0}

    async def _all_irrelevant_complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại intent" in joined:
            return _make_llm_response('{"query": "test query", "intent": "multi_hop"}')
        if "phan loai intent" in joined:
            return _make_llm_response("multi_hop")
        if "relevant" in joined and "irrelevant" in joined:
            call_count["n"] += 1
            # Always return all irrelevant
            return _make_llm_response("Chunk 1: irrelevant\nChunk 2: irrelevant")
        return _make_llm_response("fallback answer")

    # Need a vector store that returns chunks so grade actually runs.
    # spec=[] prevents MagicMock from auto-creating attributes (embed_one, hybrid_search)
    # so the code falls through to the simple search/embed paths.
    fake_vs = MagicMock(spec=[])
    fake_vs.search = AsyncMock(return_value=[
        {"chunk_id": str(uuid4()), "text": "chunk A content", "score": 0.1, "content": "chunk A content"},
        {"chunk_id": str(uuid4()), "text": "chunk B content", "score": 0.05, "content": "chunk B content"},
    ])
    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(
        complete_fn=_all_irrelevant_complete,
        vector_store=fake_vs,
        embedder=fake_embedder,
    )

    # Set pipeline_config with max_grade_retries=1 and low fallback score
    # so CRAG retry triggers once, then falls through to OOS
    state = _base_state("San pham gi do?", pipeline_config={
        "max_grade_retries": 1,
        "crag_min_fallback_score": 0.9,  # high threshold so fallback also fails
        "skip_rewrite_intents": [],       # ensure multi_hop goes through rewrite
    })

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 40}))
    # Should either hit OOS or have retried
    answer = final.get("answer", "").lower()
    retries = final.get("grade_retries", 0)
    assert retries >= 1 or bool(answer), (
        f"expected CRAG retry or OOS answer, got retries={retries} answer={answer[:100]}"
    )


# ---------------------------------------------------------------------------
# 6. test_pipeline_guardrail_block
# ---------------------------------------------------------------------------
def test_pipeline_guardrail_block():
    """Guardrail blocks input; pipeline short-circuits to persist."""
    graph = _build_graph(guardrail=_BlockingGuardrail())
    final = asyncio.run(
        graph.ainvoke(
            _base_state(""),
            config={"recursion_limit": 25},
        ),
    )
    assert "khong hop le" in final.get("answer", "").lower()
    # Ensure guardrail_flags contain a blocked entry
    blocked_flags = [f for f in final.get("guardrail_flags", []) if f.get("blocked")]
    assert len(blocked_flags) >= 1


# ---------------------------------------------------------------------------
# 7. test_pipeline_with_history
# ---------------------------------------------------------------------------
def test_pipeline_with_history(monkeypatch):
    """Follow-up question with history triggers condense; condensed query
    should incorporate context from history."""
    condensed_queries = []
    condensed = "dich vu goi dau loai dat nhat la gi"

    # Track each invocation of the structured-output stub so we can assert
    # the condense path actually fired.
    from ragbot.application.dto.llm_schemas import UnderstandOutput
    from ragbot.orchestration import query_graph as qg

    parsed = UnderstandOutput(condensed_query=condensed, intent="factoid")

    async def _fake(**kw):
        if kw.get("schema") is UnderstandOutput:
            condensed_queries.append(condensed)
            return parsed
        return None

    monkeypatch.setattr(qg, "_call_with_schema", _fake)

    async def _history_complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phan loai intent" in joined:
            return _make_llm_response("factoid")
        if "relevant" in joined and "irrelevant" in joined:
            return _make_llm_response("Chunk 1: relevant")
        return _make_llm_response("Dich vu goi dau dat nhat la VIP Keratin.")

    graph = _build_graph(complete_fn=_history_complete)
    state = _base_state(
        "Loai dat nhat la gi?",
        conversation_history=[
            {"role": "user", "content": "Ban co nhung dich vu goi dau nao?"},
            {"role": "assistant", "content": "Chung toi co dich vu goi dau thuong, goi dau VIP, va goi dau Keratin."},
            {"role": "user", "content": "Chi tiet ve goi dau Keratin?"},
            {"role": "assistant", "content": "Goi dau Keratin la dich vu cao cap voi gia 500k."},
            {"role": "user", "content": "Con goi dau VIP?"},
            {"role": "assistant", "content": "Goi dau VIP la dich vu trung cap voi gia 300k."},
        ],
    )
    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 25}))
    # Mindset clean: app no longer injects refusal text — empty is valid contract
    assert "answer" in final, "missing answer key"
    # The LLM mock was asked to condense; verify it returned a query with "goi dau"
    assert len(condensed_queries) >= 1
    assert "goi dau" in condensed_queries[0].lower()


# ---------------------------------------------------------------------------
# 8. test_pipeline_vietnamese_preprocessing
# ---------------------------------------------------------------------------
def test_pipeline_vietnamese_preprocessing():
    """Vietnamese abbreviation expansion: 'ko' -> 'khong' before retrieval."""
    from ragbot.shared.vi_tokenizer import expand_abbreviations

    result = expand_abbreviations("ko biet san pham nay")
    # expand_abbreviations returns Vietnamese with diacritics: "ko" -> "không"
    assert "không" in result.lower() or "khong" in result.lower(), (
        f"expected 'không'/'khong' in expanded text, got: {result}"
    )
    # Verify 'ko' is no longer present as a standalone word
    words = result.lower().split()
    assert "ko" not in words, f"'ko' should have been expanded, got: {result}"


# ---------------------------------------------------------------------------
# 9. test_pipeline_multi_hop_goes_through_rewrite
# ---------------------------------------------------------------------------
def test_pipeline_multi_hop_goes_through_rewrite(monkeypatch):
    """Multi-hop intent should go through the rewrite node (not skip it)."""
    _patch_understand_structured(monkeypatch, "so sanh hai san pham", "multi_hop")
    rewrite_called = {"called": False}

    async def _multi_hop_complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phan loai intent" in joined:
            return _make_llm_response("multi_hop")
        if (
            "hyde" in joined or "viet lai" in joined or "viết lại" in joined
            or "search query optimizer" in joined or "cụm từ tìm kiếm" in joined
            or "keywords" in joined
        ):
            rewrite_called["called"] = True
            return _make_llm_response("So sanh dac diem cua san pham A va san pham B")
        if "relevant" in joined and "irrelevant" in joined:
            return _make_llm_response("Chunk 1: relevant")
        return _make_llm_response("San pham A tot hon san pham B.")

    graph = _build_graph(complete_fn=_multi_hop_complete)
    state = _base_state(
        "So sanh san pham A va B?",
        pipeline_config={"skip_rewrite_intents": ["factoid"]},
    )
    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 25}))
    assert final.get("intent") == "multi_hop"
    # Mindset clean: app no longer injects refusal text — empty is valid contract
    assert "answer" in final, "missing answer key"
    # The rewrite node should have been called for multi_hop
    assert rewrite_called["called"], "rewrite node should be called for multi_hop intent"


# ---------------------------------------------------------------------------
# 10. test_pipeline_reflect_retry
# ---------------------------------------------------------------------------
def test_pipeline_reflect_retry():
    """Reflect node returns 'retry' on first pass, 'done' on second."""
    reflect_count = {"n": 0}

    async def _reflect_complete(_cfg, messages, **_kw):
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại intent" in joined:
            return _make_llm_response('{"query": "test", "intent": "multi_hop"}')
        if "phan loai intent" in joined:
            return _make_llm_response("multi_hop")
        # HyDE rewrite / search query optimizer
        if (
            "hyde" in joined or "viet lai" in joined or "viết lại" in joined
            or "search query optimizer" in joined or "cụm từ tìm kiếm" in joined
            or "keywords" in joined
        ):
            return _make_llm_response("rewritten query for test")
        if "relevant" in joined and "irrelevant" in joined:
            return _make_llm_response("Chunk 1: relevant")
        # Reflect node
        if "phan tinh" in joined or "đánh giá" in joined:
            reflect_count["n"] += 1
            if reflect_count["n"] == 1:
                return _make_llm_response("retry")
            return _make_llm_response("done")
        return _make_llm_response("Answer after reflection.")

    # Need a vector store that returns chunks so the pipeline reaches generate+reflect
    fake_vs = MagicMock(spec=[])
    fake_vs.search = AsyncMock(return_value=[
        {"chunk_id": str(uuid4()), "text": "chunk content", "score": 0.9, "content": "chunk content"},
    ])
    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(
        complete_fn=_reflect_complete,
        vector_store=fake_vs,
        embedder=fake_embedder,
    )
    state = _base_state(
        "Cau hoi phuc tap can reflect?",
        pipeline_config={
            "skip_rewrite_intents": ["factoid"],
            "skip_reflect_intents": [],  # do NOT skip reflect for any intent
            "max_reflect_retries": 1,
            # Reflect is opt-in per bot (DEFAULT_REFLECTION_ENABLED=False
            # since 2026-05-18 — gated on plan_limits.reflection_enabled).
            # Test exercises the retry loop ⇒ must opt in explicitly.
            "reflection_enabled": True,
        },
    )
    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 40}))
    # Mindset clean: app no longer injects refusal text — empty is valid contract
    assert "answer" in final, "missing answer key"
    # Reflect should have been called at least once
    assert reflect_count["n"] >= 1


# ---------------------------------------------------------------------------
# 10. test_pipeline_bypass_cache_skips_semantic_cache
# ---------------------------------------------------------------------------
def test_pipeline_bypass_cache_skips_semantic_cache():
    """When bypass_cache=True, check_cache node skips lookup even if cache has a hit."""
    fake_cache = MagicMock()
    # Cache would return a hit — but bypass_cache must prevent this from being used
    fake_cache.find_similar_with_text = AsyncMock(
        return_value=MagicMock(
            answer="Stale cached answer.",
            citations=[],
        ),
    )
    fake_cache.store = AsyncMock()

    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(semantic_cache=fake_cache, embedder=fake_embedder)
    state = _base_state("San pham bao hanh may nam?")
    # Inject bypass_cache flag — only /test/chat sets this
    state["bypass_cache"] = True

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 25}))

    # The cache should NOT have been queried (find_similar_with_text not called)
    fake_cache.find_similar_with_text.assert_not_called()
    # answer_type must NOT be "cache_hit" — pipeline ran for real
    assert final.get("answer_type") != "cache_hit", (
        f"Expected pipeline to run (not cache_hit), got answer_type={final.get('answer_type')!r}"
    )
    # cache_status in state should be "bypassed"
    assert final.get("cache_status") == "bypassed", (
        f"Expected cache_status='bypassed', got: {final.get('cache_status')!r}"
    )
    # Answer should come from pipeline (LLM mock), not stale cache
    assert final.get("answer") != "Stale cached answer.", (
        "Expected fresh pipeline answer, not stale cached response"
    )
    # Mindset clean: app no longer injects refusal text — empty is valid contract
    assert "answer" in final, "missing answer key"


def test_pipeline_bypass_cache_false_still_hits_cache():
    """When bypass_cache=False (default), semantic cache hit is served normally."""
    fake_cache = MagicMock()
    fake_cache.find_similar_with_text = AsyncMock(
        return_value=MagicMock(
            answer="Cached answer 24 thang.",
            citations=[{"chunk_id": "bbb"}],
            model_name="openai/gpt-4.1-mini",
        ),
    )
    fake_cache.store = AsyncMock()

    fake_embedder = MagicMock(spec=[])
    fake_embedder.embed = AsyncMock(return_value=[[0.1] * 10])

    graph = _build_graph(semantic_cache=fake_cache, embedder=fake_embedder)
    # bypass_cache not set (defaults to missing key — falsy)
    state = _base_state("San pham bao hanh may nam?")

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 25}))

    fake_cache.find_similar_with_text.assert_called_once()
    assert final.get("answer") == "Cached answer 24 thang."
    assert final.get("answer_type") == "cache_hit"
    assert final.get("cache_status") == "hit"
    assert final.get("model_used") == "openai/gpt-4.1-mini"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
