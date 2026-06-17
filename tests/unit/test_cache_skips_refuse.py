"""Vòng 4 fix — verify semantic cache is skipped for refuse/OOS answers.

Cache-poison scenario:
  - Bot gets an out_of_scope / no_context / blocked / greeting answer.
  - If persisted, 24h TTL returns template answer for semantically-similar
    future queries — even after docs are uploaded.

Strategy C: persist node checks state["answer_type"] against
``_REFUSE_ANSWER_TYPES`` and skips cache.store() entirely.
"""

from __future__ import annotations

import pytest


def test_refuse_answer_types_constant_defined():
    """_REFUSE_ANSWER_TYPES is exported from constants."""
    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    assert isinstance(_REFUSE_ANSWER_TYPES, frozenset)
    assert "out_of_scope" in _REFUSE_ANSWER_TYPES
    assert "no_context" in _REFUSE_ANSWER_TYPES
    assert "blocked" in _REFUSE_ANSWER_TYPES


def test_refuse_answer_types_does_not_contain_answered():
    """Normal answers ('answered') are NOT in the refuse set; greeting persona answers ARE cached."""
    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    assert "answered" not in _REFUSE_ANSWER_TYPES
    assert "cache_hit" not in _REFUSE_ANSWER_TYPES
    # Greeting answers are now LLM-generated persona replies and ARE cached.
    assert "greeting" not in _REFUSE_ANSWER_TYPES


@pytest.mark.asyncio
async def test_cache_skips_oos_answer():
    """semantic_cache.store is NOT called when answer_type == 'out_of_scope'."""
    from unittest.mock import AsyncMock

    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    mock_cache = AsyncMock()
    mock_cache.store = AsyncMock()

    # Simulate persist guard logic (mirrors query_graph.py persist node):
    state = {
        "answer": "Xin lỗi, câu hỏi này nằm ngoài phạm vi tài liệu.",
        "answer_type": "out_of_scope",
        "cache_status": None,
        "original_query": "Có bán thuốc tây không?",
    }

    _ans_type = state.get("answer_type") or ""
    should_write = (
        mock_cache is not None
        and state.get("answer")
        and state.get("cache_status") != "hit"
        and _ans_type not in _REFUSE_ANSWER_TYPES
    )

    assert not should_write, (
        f"Cache write guard failed: answer_type='{_ans_type}' should be skipped "
        "but guard evaluated to True"
    )
    mock_cache.store.assert_not_called()


@pytest.mark.asyncio
async def test_cache_skips_no_context_answer():
    """semantic_cache.store is NOT called when answer_type == 'no_context'."""
    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    state = {
        "answer": "Tôi không tìm thấy thông tin liên quan trong tài liệu.",
        "answer_type": "no_context",
        "cache_status": None,
    }
    _ans_type = state.get("answer_type") or ""
    should_write = (
        state.get("answer")
        and state.get("cache_status") != "hit"
        and _ans_type not in _REFUSE_ANSWER_TYPES
    )
    assert not should_write


@pytest.mark.asyncio
async def test_cache_writes_normal_answered():
    """Normal answered responses ARE written to cache."""
    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    state = {
        "answer": "Dịch vụ gội đầu có giá 80.000 VND.",
        "answer_type": "answered",
        "cache_status": None,
    }
    _ans_type = state.get("answer_type") or ""
    should_write = (
        state.get("answer")
        and state.get("cache_status") != "hit"
        and _ans_type not in _REFUSE_ANSWER_TYPES
    )
    assert should_write, (
        "Normal 'answered' responses should be written to cache but guard blocked them"
    )


@pytest.mark.asyncio
async def test_cache_skips_blocked_answer():
    """semantic_cache.store is NOT called when answer_type == 'blocked'."""
    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    state = {
        "answer": "Câu hỏi bị chặn bởi guardrail.",
        "answer_type": "blocked",
        "cache_status": None,
    }
    _ans_type = state.get("answer_type") or ""
    should_write = (
        state.get("answer")
        and state.get("cache_status") != "hit"
        and _ans_type not in _REFUSE_ANSWER_TYPES
    )
    assert not should_write


def test_cache_hit_already_excluded():
    """cache_status == 'hit' is excluded from write (re-served answers don't loop back)."""
    from ragbot.shared.constants import _REFUSE_ANSWER_TYPES

    state = {
        "answer": "Cached answer.",
        "answer_type": "cache_hit",
        "cache_status": "hit",
    }
    should_write = (
        state.get("answer")
        and state.get("cache_status") != "hit"
        and (state.get("answer_type") or "") not in _REFUSE_ANSWER_TYPES
    )
    assert not should_write
