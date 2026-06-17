"""Message entity (immutable).

Ref: PLAN_04 §message.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from ragbot.shared.errors import InvariantViolation
from ragbot.shared.constants import MAX_CONTENT_LENGTH
from ragbot.shared.types import (
    BotId,
    Channel,
    ConversationId,
    MessageId,
    Role,
    TenantId,
)


@dataclass(frozen=True, slots=True)
class Message:
    """Single conversation message — immutable.

    Use `Message.new_user_message` / `new_assistant_message` to create.
    Mutations return new instances via `dataclasses.replace`.
    """

    id: MessageId
    conversation_id: ConversationId
    record_tenant_id: TenantId
    record_bot_id: BotId
    role: Role
    content: str
    channel: Channel
    created_at: datetime
    citations: tuple[str, ...] = ()  # JSON refs to Citation entities
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content and self.role == "user":
            raise InvariantViolation("user message content must not be empty")
        if len(self.content) > MAX_CONTENT_LENGTH:
            raise InvariantViolation(
                f"message content too long: {len(self.content)} > {MAX_CONTENT_LENGTH}",
            )

    @classmethod
    def new_user_message(
        cls,
        *,
        conversation_id: ConversationId,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        content: str,
        channel: Channel,
        created_at: datetime,
    ) -> Message:
        """Tạo message mới từ phía người dùng.
        @param content: nội dung câu hỏi
        @return: Message instance với role=user
        """
        return cls(
            id=MessageId(uuid4()),
            conversation_id=conversation_id,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            role="user",
            content=content,
            channel=channel,
            created_at=created_at,
        )

    @classmethod
    def new_assistant_message(
        cls,
        *,
        conversation_id: ConversationId,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        content: str,
        channel: Channel,
        created_at: datetime,
        citations: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Tạo message mới từ phía trợ lý AI.
        @param content: nội dung câu trả lời
        @param citations: danh sách trích dẫn
        @return: Message instance với role=assistant
        """
        return cls(
            id=MessageId(uuid4()),
            conversation_id=conversation_id,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            role="assistant",
            content=content,
            channel=channel,
            created_at=created_at,
            citations=citations,
            metadata=metadata or {},
        )

    def with_redacted_content(self, redacted: str) -> Message:
        """Tạo bản sao với nội dung đã che dấu (GDPR).
        @param redacted: nội dung thay thế
        @return: Message mới
        """
        return replace(self, content=redacted)


__all__ = ["Message"]
