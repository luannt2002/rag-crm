"""Chat commands.

Ref: PLAN_05 §chat_commands.py.

Pydantic v2 frozen models for runtime validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ragbot.shared.constants import DEFAULT_HISTORY_LIMIT, MAX_CHAT_CONTENT_LENGTH
from ragbot.shared.types import (
    BotId,
    Channel,
    ConversationId,
    MessageId,
    TenantId,
    TraceId,
    UserId,
    WorkspaceId,
)


class AnswerQuestionCommand(BaseModel):
    """Inbound request to answer a user question.

    `mode == "async"` is the production default — returns 202 immediately.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    # External identity slugs — paired with record_tenant_id UUID; forwarded
    # onto the ChatReceived outbox event so the worker re-resolves the
    # bot row without trusting the cached UUID alone. ``workspace_id`` is
    # the tenant-supplied scope slug; the route resolver fills it from
    # ``str(record_tenant_id)`` when the wire payload omits the value.
    workspace_id: WorkspaceId
    bot_id: str = Field(..., min_length=1, description="External bot slug")
    channel_type: str = Field(..., min_length=1, description="External channel")
    user_id: UserId
    conversation_id: ConversationId | None = None  # auto-derive if None
    content: str = Field(min_length=1, max_length=MAX_CHAT_CONTENT_LENGTH)
    channel: Channel
    history_limit: int = Field(default=DEFAULT_HISTORY_LIMIT, ge=1, le=50)
    external_message_id: str | None = None  # for idempotency
    mode: Literal["sync", "async"] = "async"
    callback_url: str | None = None
    trace_id: TraceId
    received_at: datetime


class GiveFeedbackCommand(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    workspace_id: WorkspaceId
    conversation_id: ConversationId
    message_id: MessageId
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=MAX_CHAT_CONTENT_LENGTH)
    user_id: UserId
    trace_id: TraceId


__all__ = ["AnswerQuestionCommand", "GiveFeedbackCommand"]
