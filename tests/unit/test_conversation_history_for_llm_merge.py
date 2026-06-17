"""Activate `_merge_consecutive_user` retroactive merge in chat_worker history-load.

S22 quick-win: chat_worker previously sliced raw `messages[-6:]`, bypassing the
existing `history_for_llm()` method which merges consecutive user-role messages
("Zalo debounce ported"). Test that the activated path collapses repeated
user turns into a single prompt entry — gives free LLM cost reduction when
history already contains spam from prior session.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.message import Message
from ragbot.shared.types import BotId, ConversationId, TenantId, UserId


def _msg(conv_id: ConversationId, tid: TenantId, bid: BotId, role: str, content: str, ts: datetime) -> Message:
    cls = Message.new_user_message if role == "user" else Message.new_assistant_message
    return cls(
        conversation_id=conv_id,
        record_tenant_id=tid,
        record_bot_id=bid,
        content=content,
        channel="web",
        created_at=ts,
    )


def _conv(messages: tuple[Message, ...]) -> Conversation:
    tid = TenantId(uuid4())
    bid = BotId(uuid4())
    cid = ConversationId(uuid4())
    now = datetime.now(timezone.utc)
    return Conversation(
        id=cid,
        record_tenant_id=tid,
        record_bot_id=bid,
        connect_id=UserId("user-1"),
        channel="web",
        messages=messages,
        rolling_summary="",
        turn_count=len(messages),
        created_at=now,
        last_message_at=now,
    )


def test_three_consecutive_user_messages_merge_into_one():
    tid = TenantId(uuid4())
    bid = BotId(uuid4())
    cid = ConversationId(uuid4())
    t = datetime.now(timezone.utc)
    messages = (
        _msg(cid, tid, bid, "user", "giá sản phẩm X?", t),
        _msg(cid, tid, bid, "user", "còn hàng không?", t),
        _msg(cid, tid, bid, "user", "ship HN bao lâu?", t),
    )
    conv = Conversation(
        id=cid, record_tenant_id=tid, record_bot_id=bid,
        connect_id=UserId("user-1"), channel="web",
        messages=messages, rolling_summary="", turn_count=3,
        created_at=t, last_message_at=t,
    )

    out = conv.history_for_llm(limit=6)

    assert len(out) == 1
    assert "giá sản phẩm X?" in out[0].content
    assert "còn hàng không?" in out[0].content
    assert "ship HN bao lâu?" in out[0].content


def test_user_assistant_user_does_not_merge():
    tid = TenantId(uuid4())
    bid = BotId(uuid4())
    cid = ConversationId(uuid4())
    t = datetime.now(timezone.utc)
    messages = (
        _msg(cid, tid, bid, "user", "câu 1", t),
        _msg(cid, tid, bid, "assistant", "trả lời 1", t),
        _msg(cid, tid, bid, "user", "câu 2", t),
    )
    conv = _conv(messages)

    out = conv.history_for_llm(limit=6)

    assert len(out) == 3
    assert [m.role for m in out] == ["user", "assistant", "user"]


def test_empty_history_returns_empty():
    conv = _conv(())
    assert conv.history_for_llm(limit=6) == []


def test_chat_worker_uses_history_for_llm_method():
    """Pin: chat_worker.py must call history_for_llm, not raw messages slice."""
    from pathlib import Path

    # chat_worker was split into a package — scan every module.
    pkg = Path(__file__).resolve().parents[2] / "src" / "ragbot" / "interfaces" / "workers" / "chat_worker"
    content = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
    )

    assert "history_for_llm" in content, (
        "chat_worker.py must invoke conv.history_for_llm() to activate "
        "consecutive-user merge — see plans/260508-master-replan-tiered/."
    )
