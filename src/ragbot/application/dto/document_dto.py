"""Document DTOs.

Ref: PLAN_05 §dto/document_dto.py.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ragbot.shared.types import (
    BotId,
    ChunkingStrategyName,
    CorpusVersion,
    DocumentId,
    DocumentState,
    JobId,
    JobStatus,
    TraceId,
)


class DocumentDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: DocumentId
    record_bot_id: BotId
    document_name: str
    tool_name: str
    source_url: str
    state: DocumentState
    version: int
    language: str
    chunk_count: int = 0
    ingested_at: datetime | None = None


class IngestAcceptedDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    job_id: JobId
    tool_name: str
    status: JobStatus
    trace_id: TraceId


class IngestResultDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    document_id: DocumentId
    tool_name: str
    chunk_count: int
    strategy_used: ChunkingStrategyName
    corpus_version: CorpusVersion
    success: bool
    error: str | None = None


class DeleteResultDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    deleted_chunks: int
    corpus_version: CorpusVersion


__all__ = [
    "DeleteResultDTO",
    "DocumentDTO",
    "IngestAcceptedDTO",
    "IngestResultDTO",
]
