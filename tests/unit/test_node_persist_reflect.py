"""Unit tests for `persist` + `reflect` orchestration nodes.

`persist` (`query_graph.py:2909`, ~80 LoC) writes successful answers to
the semantic cache and emits the terminal `query_completed` audit
event. `reflect` (`:2838`, ~70 LoC) is the Self-RAG judge that decides
whether to retry generation.

Both are end-of-pipeline nodes; bugs here mean lost data or silent
quality regressions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


def _persist(compiled):
    return node_callable(compiled, "persist")


def _reflect(compiled):
    return node_callable(compiled, "reflect")


# --------------------------------------------------------------------------- #
# persist                                                                     #
# --------------------------------------------------------------------------- #


def test_persist_emits_query_completed_audit_event():
    """Every terminal turn — answered, cached, or refused — emits the event."""
    compiled, _tracker, audit, *_ = build_test_graph()
    state = make_state(
        query="bảo hành",
        answer="Bảo hành 12 tháng.",
        answer_type="answered",
        graded_chunks=[{"chunk_id": "c1", "score": 0.9, "content": "12 months"}],
        tokens={"prompt": 100, "completion": 20},
        cost_usd=0.01,
        model_used="mock/model",
    )
    _ = asyncio.run(_persist(compiled)(state))
    payloads = audit.by_event("query_completed")
    assert payloads, audit.events
    p = payloads[-1]
    assert p["answer_type"] == "answered"
    assert p["graded_chunks"] == 1
    assert p["model_used"] == "mock/model"
    assert p["tokens_prompt"] == 100
    assert p["tokens_completion"] == 20


def test_persist_does_not_write_cache_when_answer_type_refused():
    """Refuse answers MUST NOT poison the semantic cache."""
    sem_cache = MagicMock()
    sem_cache.store = AsyncMock()
    compiled, *_ = build_test_graph(semantic_cache=sem_cache)
    state = make_state(
        answer="xin lỗi không có thông tin",
        answer_type="out_of_scope",
        graded_chunks=[],
    )
    _ = asyncio.run(_persist(compiled)(state))
    # Strategy C: out_of_scope is in _REFUSE_ANSWER_TYPES → no store call.
    assert sem_cache.store.await_count == 0


def test_persist_does_not_write_cache_when_already_cache_hit():
    """Cache hits skip re-write so we don't double-write the same answer."""
    sem_cache = MagicMock()
    sem_cache.store = AsyncMock()
    compiled, *_ = build_test_graph(semantic_cache=sem_cache)
    state = make_state(
        answer="Bảo hành 12 tháng.",
        answer_type="cache_hit",
        cache_status="hit",
        graded_chunks=[],
    )
    _ = asyncio.run(_persist(compiled)(state))
    assert sem_cache.store.await_count == 0


def test_persist_returns_persist_meta_when_graded_chunks_present():
    """`_persist_meta` carries context_chars / context_chunks for downstream."""
    compiled, *_ = build_test_graph()
    chunks = [
        {"chunk_id": "c1", "content": "abcdef", "score": 0.9},
        {"chunk_id": "c2", "content": "xyz", "score": 0.8},
    ]
    state = make_state(
        answer="ans",
        answer_type="answered",
        graded_chunks=chunks,
    )
    out = asyncio.run(_persist(compiled)(state))
    assert "_persist_meta" in out
    meta = out["_persist_meta"]
    assert meta["context_chunks"] == 2
    assert meta["context_chars"] == len("abcdef") + len("xyz")


def test_persist_returns_empty_dict_when_no_graded_chunks():
    """No graded chunks → no `_persist_meta` payload (just audit fired)."""
    compiled, *_ = build_test_graph()
    state = make_state(
        answer="ans",
        answer_type="answered",
        graded_chunks=[],
    )
    out = asyncio.run(_persist(compiled)(state))
    assert out == {}


# --------------------------------------------------------------------------- #
# reflect                                                                     #
# --------------------------------------------------------------------------- #


def test_reflect_keeps_answer_when_llm_returns_keep():
    resolver, llm, _cfg = make_resolver_and_llm(text_response="keep")
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="12 tháng.",
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
        },
    )
    out = asyncio.run(_reflect(compiled)(state))
    # `keep` → empty update; the existing answer survives.
    assert out == {}
    assert len(tracker.by_name("reflect")) == 1


def test_reflect_clears_answer_to_retry_when_llm_returns_rewrite():
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite please")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="ngắn quá",
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
        },
        reflect_retries=0,
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Retry: answer cleared so generate fires again.
    assert out["answer"] == ""
    assert out["reflect_retries"] == 1


def test_reflect_does_not_retry_when_max_retries_reached():
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="x",
        answer="some answer",
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
        },
        reflect_retries=1,  # already at cap
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Cap reached → keep current answer, no retry.
    assert out == {}


def test_reflect_step_tracker_wraps_each_call():
    resolver, llm, _cfg = make_resolver_and_llm(text_response="keep")
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="x",
        answer="ans",
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
        },
    )
    asyncio.run(_reflect(compiled)(state))
    asyncio.run(_reflect(compiled)(state))
    asyncio.run(_reflect(compiled)(state))
    assert len(tracker.by_name("reflect")) == 3


def test_reflect_keeps_when_llm_says_keep_and_rewrite():
    """`keep` substring in verdict overrides `rewrite` substring (per node logic)."""
    resolver, llm, _cfg = make_resolver_and_llm(
        text_response="keep — no need to rewrite"
    )
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="x",
        answer="ans",
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
        },
    )
    out = asyncio.run(_reflect(compiled)(state))
    # `keep` present in verdict → no retry, answer kept.
    assert out == {}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
