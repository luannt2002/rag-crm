"""Unit tests for `condense_question` orchestration node.

The node lives at `query_graph.py:1041` and rewrites a follow-up
question into a standalone one when there is enough conversation
history. Critical paths:

- empty history → `{}` (no LLM call, no step wrap)
- short history (<=2 messages) → `{}`
- short text (<100 chars total) → `{}`
- meaningful history → LLM called, returns `{"query": ..., "original_query": ...}`
- LLM raises → exception swallowed (`return {}`); pipeline continues
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
    return node_callable(compiled, "condense_question")


def _meaningful_history() -> list[dict[str, str]]:
    """Returns 4 messages totalling >100 chars."""
    return [
        {"role": "user", "content": "Tôi đang quan tâm tới chính sách bảo hành."},
        {"role": "assistant", "content": "Bảo hành 12 tháng cho tất cả sản phẩm chính hãng."},
        {"role": "user", "content": "Vậy còn phụ kiện đi kèm thì sao?"},
        {"role": "assistant", "content": "Phụ kiện được bảo hành riêng, kiểm tra hóa đơn để biết thêm."},
    ]


def test_condense_returns_empty_when_history_empty():
    compiled, tracker, *_ = build_test_graph()
    state = make_state(history=[])
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {}
    # Step MUST NOT wrap when the early-exit path triggers.
    assert tracker.by_name("condense_question") == []


def test_condense_returns_empty_when_history_has_fewer_than_two_messages():
    # Threshold lowered <=2 → <2 (2026-05-27: a 2-msg history where T2 references
    # T1 by pronoun now DOES condense). The skip path is exercised with <2 msgs.
    compiled, tracker, *_ = build_test_graph()
    history = [
        {"role": "user", "content": "Câu hỏi 1 với rất nhiều ký tự để vượt qua threshold 100"},
    ]
    out = asyncio.run(_afunc(compiled)(make_state(history=history)))
    assert out == {}
    assert tracker.by_name("condense_question") == []


def test_condense_returns_empty_when_history_under_100_chars():
    compiled, tracker, *_ = build_test_graph()
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hi back"},
        {"role": "user", "content": "ok"},
    ]
    out = asyncio.run(_afunc(compiled)(make_state(history=history)))
    assert out == {}
    assert tracker.by_name("condense_question") == []


def test_condense_returns_condensed_query_when_history_meaningful():
    resolver, llm, _cfg = make_resolver_and_llm(
        text_response="bảo hành phụ kiện kéo dài bao lâu"
    )
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="còn phụ kiện thì sao", history=_meaningful_history()
    )
    out = asyncio.run(_afunc(compiled)(state))
    assert out["query"] == "bảo hành phụ kiện kéo dài bao lâu"
    assert out["original_query"] == "còn phụ kiện thì sao"
    # Step MUST wrap when the LLM path runs.
    assert len(tracker.by_name("condense_question")) == 1
    # The LLM must have actually been called — once.
    assert llm.complete.await_count == 1


def test_condense_swallows_llm_exception_and_returns_empty():
    resolver, llm, _cfg = make_resolver_and_llm()
    llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="x", history=_meaningful_history())
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {}
    # Even on failure the step DID wrap (we're inside `async with` when
    # the exception fires); tracker count == 1.
    assert len(tracker.by_name("condense_question")) == 1


def test_condense_skips_when_llm_returns_blank_text():
    resolver, llm, _cfg = make_resolver_and_llm(text_response="   ")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="x", history=_meaningful_history())
    out = asyncio.run(_afunc(compiled)(state))
    # Empty/blank LLM output → no rewrite; original query preserved upstream.
    assert out == {}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
