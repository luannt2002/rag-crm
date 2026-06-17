"""Chat-related domain events.

Ref: PLAN_04 §events/chat_events.py / RAGBOT_MASTER §14.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from ragbot.domain.events.base import DomainEvent
from ragbot.shared.types import (
    BotId,
    Channel,
    ConversationId,
    IdempotencyKey,
    JobId,
    MessageId,
    UserId,
    WorkspaceId,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class ChatReceived(DomainEvent):
    """Inbound chat event — carries the bot identity slugs + UUID tenant.

    Identity resolves to ``(record_tenant_id, workspace_id, bot_id,
    channel_type)``. ``record_tenant_id`` is the canonical UUID PK from
    ``tenants.id``; legacy upstream INT claims are translated upstream
    (HTTP layer) before publish. ``workspace_id`` is the tenant-supplied
    slug; missing slugs are filled with ``str(record_tenant_id)`` by the
    resolver before this event is built.
    """

    event_type: ClassVar[str] = "chat.received.v1"

    # External identity slugs — paired with inherited record_tenant_id UUID.
    workspace_id: WorkspaceId
    bot_id: str
    channel_type: str

    # Internal references.
    job_id: JobId
    record_bot_id: BotId
    user_id: UserId
    conversation_id: ConversationId
    message_id: MessageId
    content: str
    channel: Channel
    idempotency_key: IdempotencyKey
    history_limit: int = 6
    callback_url: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ChatAnswered(DomainEvent):
    event_type: ClassVar[str] = "chat.answered.v1"

    workspace_id: WorkspaceId
    job_id: JobId
    record_bot_id: BotId
    user_id: UserId
    conversation_id: ConversationId
    message_id: MessageId
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model_name: str = ""
    callback_url: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ChatFailed(DomainEvent):
    event_type: ClassVar[str] = "chat.failed.v1"

    workspace_id: WorkspaceId
    job_id: JobId
    record_bot_id: BotId
    conversation_id: ConversationId
    error_code: str
    error_message: str
    callback_url: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ChatDeliveryFailed(DomainEvent):
    event_type: ClassVar[str] = "chat.delivery_failed.v1"

    workspace_id: WorkspaceId
    job_id: JobId
    record_bot_id: BotId
    conversation_id: ConversationId
    channel: Channel
    retry_count: int
    error: str


@dataclass(frozen=True, kw_only=True, slots=True)
class FeedbackGiven(DomainEvent):
    event_type: ClassVar[str] = "feedback.given.v1"

    workspace_id: WorkspaceId
    record_bot_id: BotId
    conversation_id: ConversationId
    message_id: MessageId
    rating: str  # "up" | "down"
    comment: str | None = None


__all__ = [
    "ChatAnswered",
    "ChatDeliveryFailed",
    "ChatFailed",
    "ChatReceived",
    "FeedbackGiven",
]
