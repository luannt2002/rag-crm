"""Chat HTTP schemas.

Body carries the 2-key bot identity ``(bot_id, channel_type)`` plus an
optional ``workspace_id`` slug; tenant is lifted from the JWT bearer
(``request.state.record_tenant_id`` UUID) in the route. The workspace
slug is body-supplied (or resolves to ``str(record_tenant_id)`` on
fallback) and combined with the tenant UUID + bot_id + channel_type to
form the 4-key identity used for bot resolution. Internal UUID
``record_bot_id`` is resolved by ``BotRegistryService`` and never
accepted from upstream.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ragbot.shared.constants import (
    BOT_ID_PATTERN,
    CHANNEL_TYPE_PATTERN,
    DEFAULT_HISTORY_LIMIT,
    MAX_BOT_ID_LENGTH,
    MAX_BOT_NAME_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    MAX_CHAT_CONTENT_LENGTH,
    MAX_HISTORY_LIMIT_REQUEST,
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_ID_PATTERN,
)


class ChatRequest(BaseModel):
    # ``extra="forbid"`` rejects payloads that smuggle extra fields the
    # bot-owner-controlled system_prompt was being smuggled through (the
    # ``system_prompt`` body field used to override LLM instructions —
    # forbidden per CLAUDE.md "Application KHÔNG inject text vào LLM
    # prompt"; bot owner's stored ``bots.system_prompt`` is the single
    # source of truth).
    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        pattern=BOT_ID_PATTERN,
        description="External bot slug (RAG-agnostic opaque string)",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        pattern=CHANNEL_TYPE_PATTERN,
        description="Channel — opaque string, RAG-agnostic (e.g. 'web', 'zalo', 'api')",
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; the route resolver substitutes "
            "str(record_tenant_id) when the wire payload omits it."
        ),
    )
    user_id: str = Field(min_length=1, max_length=MAX_BOT_NAME_LENGTH)
    content: str = Field(min_length=1, max_length=MAX_CHAT_CONTENT_LENGTH)
    history_limit: int = Field(
        default=DEFAULT_HISTORY_LIMIT, ge=1, le=MAX_HISTORY_LIMIT_REQUEST,
    )
    external_message_id: str | None = None
    mode: Literal["sync", "async"] = "async"
    callback_url: str | None = None

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.startswith("https://") and not v.startswith("http://"):
                raise ValueError("callback_url must be a valid HTTP(S) URL")
        return v


class ChatAcceptedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: Literal[True] = True
    job_id: str
    status: Literal["queued"] = "queued"
    status_url: str
    trace_id: str


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        pattern=BOT_ID_PATTERN,
        description="External bot slug",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        pattern=CHANNEL_TYPE_PATTERN,
        description="Channel — opaque string, RAG-agnostic",
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; missing value falls back to "
            "str(record_tenant_id) at the route layer."
        ),
    )
    conversation_id: UUID
    message_id: UUID
    user_id: str
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=MAX_CHAT_CONTENT_LENGTH)


__all__ = ["ChatAcceptedResponse", "ChatRequest", "FeedbackRequest"]
