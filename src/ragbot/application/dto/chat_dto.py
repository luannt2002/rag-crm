"""Chat DTOs.

Ref: PLAN_05 §dto/chat_dto.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ragbot.shared.types import (
    BotId,
    ConversationId,
    JobId,
    JobStatus,
    MessageId,
    Role,
    TraceId,
)


class CitationDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    document_id: str
    chunk_id: str
    tool_name: str
    quote_span: str
    page_number: int | None = None
    snippet: str | None = None


class AnswerDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    answer: str
    citations: list[CitationDTO]
    confidence: float = 0.0
    refusal_reason: str | None = None
    trace_id: TraceId
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model_name: str = ""


class ChatAcceptedDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    job_id: JobId
    status: JobStatus
    status_url: str
    trace_id: TraceId


class MessageDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: MessageId
    role: Role
    content: str
    created_at: datetime
    citations: list[CitationDTO] = []


class ConversationHistoryDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    conversation_id: ConversationId
    record_bot_id: BotId
    messages: list[MessageDTO]
    rolling_summary: str
    total_turns: int


class JobStatusDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    job_id: JobId
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


__all__ = [
    "AnswerDTO",
    "ChatAcceptedDTO",
    "CitationDTO",
    "ConversationHistoryDTO",
    "JobStatusDTO",
    "MessageDTO",
]
