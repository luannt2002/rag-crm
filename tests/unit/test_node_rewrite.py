"""Unit tests for the ``rewrite`` orchestration node.

The node lives at ``query_graph.py:1257`` and standalone-rewrites the
user query before retrieval. Contract:

- Always wraps in ``step_tracker.step("rewrite")``.
- Always returns ``{"rewritten_query": <text>}``.
- Falls back to the original query when the LLM returns blank text.
- Truncates / preserves long queries verbatim — the LLM, not the node,
  decides shape (we assert the node passes the *full* query through and
  emits whatever the LLM returns).
- Step metadata captured via ``ctx.record(...)`` (token + cost fields).

These tests use the shared ``_node_test_helpers`` scaffold so the
mocking surface stays identical across every node test file.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


def _afunc(compiled):
    """Graph node was renamed to ``rewrite_and_mq_parallel`` (Perf
    Parallel ship). Tests below assert the *inner* ``rewrite`` semantics
    (return shape ``{"rewritten_query": ...}``, step name ``"rewrite"``),
    so we pin ``pipeline_parallel_rewrite_mq_enabled=False`` on every
    invocation — that short-circuits the wrapper straight to plain
    ``rewrite()``.
    """
    inner = node_callable(compiled, "rewrite_and_mq_parallel")

    async def _wrapped(state):
        pcfg = dict(state.get("pipeline_config") or {})
        pcfg["pipeline_parallel_rewrite_mq_enabled"] = False
        state["pipeline_config"] = pcfg
        return await inner(state)

    return _wrapped


# --------------------------------------------------------------------------- #
# 1. Returns rewritten query                                                  #
# --------------------------------------------------------------------------- #


def test_rewrite_returns_llm_text_as_rewritten_query():
    resolver, llm, _cfg = make_resolver_and_llm(
        text_response="bảo hành phụ kiện kéo dài bao lâu"
    )
    compiled, _tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    # Use multi_hop intent — rewrite is enabled for complex intents.
    state = make_state(query="còn phụ kiện thì sao", intent="multi_hop")
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {"rewritten_query": "bảo hành phụ kiện kéo dài bao lâu"}
    # The LLM was invoked exactly once by the node.
    assert llm.complete.await_count == 1


# --------------------------------------------------------------------------- #
# 2. History context applied (pronoun resolution)                             #
# --------------------------------------------------------------------------- #


def test_rewrite_passes_user_query_to_llm_for_pronoun_resolution():
    """The node hands the original query to the LLM — the LLM does the
    pronoun resolution. We assert the user-message content carries the
    raw pronoun query so the rewriter prompt has something to resolve.
    """
    resolver, llm, _cfg = make_resolver_and_llm(
        text_response="chính sách bảo hành sản phẩm"
    )
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="cái đó bảo hành thế nào",
        # Use comparison intent — rewrite is enabled for complex intents.
        intent="comparison",
        history=[
            {"role": "user", "content": "Tôi đang xem mẫu A."},
            {"role": "assistant", "content": "Đây là sản phẩm chính hãng."},
        ],
    )
    out = asyncio.run(_afunc(compiled)(state))
    assert out["rewritten_query"] == "chính sách bảo hành sản phẩm"

    # Inspect the messages list passed into the single llm.complete call.
    assert llm.complete.await_count == 1
    _args, kwargs = llm.complete.call_args
    messages = kwargs.get("messages") or (
        _args[1] if len(_args) > 1 else None
    )
    assert messages is not None
    user_msgs = [m for m in messages if m.get("role") == "user"]
    # Rewrite now wraps the raw query with conversation context for pronoun
    # resolution ("...\nCurrent query to rewrite: <query>"), so the raw query is
    # a substring rather than the whole message.
    assert user_msgs and "cái đó bảo hành thế nào" in user_msgs[-1]["content"]


# --------------------------------------------------------------------------- #
# 3. Empty LLM output → original returned (no-op fallback)                    #
# --------------------------------------------------------------------------- #


def test_rewrite_falls_back_to_original_when_llm_returns_empty_string():
    """Rewrite contract: ``rewritten = payload['text'] or state['query']``.

    An empty LLM reply must not silently erase the user's query — the
    node returns the *original* query string instead.

    NB: only ``""`` is falsy in Python; whitespace strings like ``"   "``
    are truthy and will pass through *as-is*. That's a deliberate
    design choice in the node — upstream should sanitise blanks if it
    wants whitespace replies suppressed. We test the documented
    behaviour rather than the desired behaviour.
    """
    resolver, llm, _cfg = make_resolver_and_llm(text_response="")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    original = "câu hỏi gốc của người dùng"
    # Use multi_hop intent — rewrite is enabled so the LLM path fires.
    state = make_state(query=original, intent="multi_hop")
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {"rewritten_query": original}


def test_rewrite_does_not_fall_back_on_whitespace_only_text():
    """Documents the truthy-string corner: ``"   "`` is truthy, so the
    ``payload["text"] or state["query"]`` short-circuit keeps the
    whitespace reply unchanged. If this ever changes (i.e. node starts
    stripping before the truthiness check), this test is the canary.
    """
    resolver, llm, _cfg = make_resolver_and_llm(text_response="   ")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    # Use aggregation intent — rewrite is enabled so LLM path fires.
    out = asyncio.run(_afunc(compiled)(make_state(query="something", intent="aggregation")))
    # Truthy whitespace passes through unchanged — DO NOT silently strip.
    assert out == {"rewritten_query": "   "}


# --------------------------------------------------------------------------- #
# 4. Long query passes through unchanged at the node boundary                 #
# --------------------------------------------------------------------------- #


def test_rewrite_passes_long_query_unchanged_to_llm():
    """The node MUST NOT pre-truncate the query — that decision belongs
    to the LLM (and to upstream length-guard policies). Long input ↔
    long output is fine; the node merely shuttles text.
    """
    long_query = "Tôi muốn biết " + ("rất chi tiết " * 40) + "về chính sách"
    long_response = "Tóm tắt yêu cầu: " + ("chi tiết " * 40)
    resolver, llm, _cfg = make_resolver_and_llm(text_response=long_response)
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    # Use multi_hop intent — rewrite is enabled for complex intents.
    state = make_state(query=long_query, intent="multi_hop")
    out = asyncio.run(_afunc(compiled)(state))
    assert out["rewritten_query"] == long_response

    # Verify the unmodified long_query reached the LLM.
    _args, kwargs = llm.complete.call_args
    messages = kwargs.get("messages") or (
        _args[1] if len(_args) > 1 else None
    )
    user_msgs = [m for m in messages if m.get("role") == "user"]
    assert user_msgs[-1]["content"] == long_query


# --------------------------------------------------------------------------- #
# 5. Step metadata captured (step wraps + ctx.record receives token data)     #
# --------------------------------------------------------------------------- #


def test_rewrite_wraps_step_and_records_tokens():
    """The node opens ``step_tracker.step('rewrite')`` exactly once and
    forwards the LLM token / cost payload via ``ctx.record(...)``.

    We can't observe ``ctx.record`` directly because the helper builds an
    ``invocation_logger`` ctx via MagicMock — but we *can* observe the
    step wrap from ``RecordingStepTracker`` and confirm only one wrap
    happens per invocation.
    """
    resolver, llm, _cfg = make_resolver_and_llm(text_response="đã viết lại")
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    # Use multi_hop intent so the rewrite LLM path fires and wraps the step.
    state = make_state(query="câu hỏi nào đó", intent="multi_hop")
    asyncio.run(_afunc(compiled)(state))
    asyncio.run(_afunc(compiled)(state))
    # Exactly two step entries — one per invocation.
    assert len(tracker.by_name("rewrite")) == 2
    # No other step name leaked from this node.
    assert tracker.names() == ["rewrite", "rewrite"]


# --------------------------------------------------------------------------- #
# 6. LLM exception propagates — rewrite is on the hot path, must fail loud    #
# --------------------------------------------------------------------------- #


def test_rewrite_propagates_llm_exception():
    """Unlike condense/decompose (best-effort enhancements), ``rewrite``
    is a load-bearing step: if it fails silently the downstream
    retrieval gets a stale query. Assert it raises.
    """
    resolver, llm, _cfg = make_resolver_and_llm()
    llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    # Use multi_hop intent — rewrite is enabled so the LLM path (which raises) fires.
    with pytest.raises(RuntimeError, match="LLM down"):
        asyncio.run(_afunc(compiled)(make_state(query="x", intent="multi_hop")))


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
