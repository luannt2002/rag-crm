"""Chat / document queries (read side).

Ref: PLAN_05 §queries/chat_queries.py.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ragbot.shared.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from ragbot.shared.types import (
    BotId,
    ConversationId,
    JobId,
    TenantId,
    TraceId,
)


class GetConversationHistoryQuery(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    conversation_id: ConversationId
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    before_ts: datetime | None = None


class GetJobStatusQuery(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    job_id: JobId


class GetTraceQuery(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    trace_id: TraceId


class ListDocumentsQuery(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    state_filter: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


__all__ = [
    "GetConversationHistoryQuery",
    "GetJobStatusQuery",
    "GetTraceQuery",
    "ListDocumentsQuery",
]
