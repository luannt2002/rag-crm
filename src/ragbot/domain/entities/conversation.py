"""Conversation aggregate (immutable, with consecutive-user merge).

Ref: PLAN_04 §conversation.py / RAGBOT_MASTER §9.9 / §35.11.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import uuid4

from ragbot.domain.entities.message import Message
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.constants import ROLLING_SUMMARY_THRESHOLD
from ragbot.shared.types import (
    BotId,
    Channel,
    ConversationId,
    TenantId,
    UserId,
)


@dataclass(frozen=True, slots=True)
class Conversation:
    """Conversation aggregate root.

    Messages are stored as immutable tuple. Mutations return new Conversation.
    """

    id: ConversationId
    record_tenant_id: TenantId
    record_bot_id: BotId
    connect_id: UserId
    channel: Channel
    messages: tuple[Message, ...]
    rolling_summary: str
    turn_count: int
    created_at: datetime
    last_message_at: datetime
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        connect_id: UserId,
        channel: Channel,
        when: datetime,
    ) -> Conversation:
        """Tạo conversation mới (rỗng, chưa có message).
        @param connect_id: ID người dùng
        @param channel: kênh giao tiếp
        @return: Conversation instance mới
        """
        return cls(
            id=ConversationId(uuid4()),
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            connect_id=connect_id,
            channel=channel,
            messages=(),
            rolling_summary="",
            turn_count=0,
            created_at=when,
            last_message_at=when,
        )

    # --- Mutations (return new instance) -----------------------------------
    def add_message(self, message: Message) -> Conversation:
        """Thêm message vào conversation (trả về bản sao mới).
        @param message: Message cần thêm
        @return: Conversation mới chứa message
        """
        if message.record_tenant_id != self.record_tenant_id:
            raise InvariantViolation("message tenant != conversation tenant")
        if message.record_bot_id != self.record_bot_id:
            raise InvariantViolation("message bot != conversation bot")
        return replace(
            self,
            messages=(*self.messages, message),
            turn_count=self.turn_count + (1 if message.role == "user" else 0),
            last_message_at=message.created_at,
        )

    def with_summary(self, summary: str) -> Conversation:
        """Cập nhật rolling summary cho conversation.
        @param summary: tóm tắt mới
        @return: Conversation mới với summary đã cập nhật
        """
        return replace(self, rolling_summary=summary)

    # --- Queries -----------------------------------------------------------
    def history_for_llm(self, limit: int = 20) -> list[Message]:
        """Return last `limit` messages with consecutive-user merge."""
        recent = list(self.messages[-limit:])
        return _merge_consecutive_user(recent)

    def should_summarize(self) -> bool:
        """Kiểm tra conversation đã đủ lượt để cần tóm tắt.
        @return: True nếu turn_count vượt ngưỡng
        """
        return self.turn_count > ROLLING_SUMMARY_THRESHOLD


def _merge_consecutive_user(msgs: list[Message]) -> list[Message]:
    """Merge consecutive user-role messages into one (Zalo debounce ported)."""
    if not msgs:
        return []
    out: list[Message] = []
    for m in msgs:
        if (
            out
            and out[-1].role == "user"
            and m.role == "user"
        ):
            merged_content = f"{out[-1].content}\n{m.content}"
            out[-1] = replace(out[-1], content=merged_content, created_at=m.created_at)
        else:
            out.append(m)
    return out


__all__ = ["Conversation"]
