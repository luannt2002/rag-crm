"""Document-related domain events.

Ref: PLAN_04 §events/document_events.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ragbot.domain.events.base import DomainEvent
from ragbot.shared.types import (
    BotId,
    ChunkingStrategyName,
    CorpusVersion,
    DocumentId,
    EmbeddingModelVersion,
    JobId,
    WorkspaceId,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class DocumentUploaded(DomainEvent):
    event_type: ClassVar[str] = "document.uploaded.v1"

    workspace_id: WorkspaceId
    job_id: JobId
    record_bot_id: BotId
    document_id: DocumentId
    source_url: str
    document_name: str
    tool_name: str
    mime_type: str
    uploaded_by: str | None = None
    force_reingest: bool = False


@dataclass(frozen=True, kw_only=True, slots=True)
class DocumentIngested(DomainEvent):
    event_type: ClassVar[str] = "document.ingested.v1"

    workspace_id: WorkspaceId
    job_id: JobId
    record_bot_id: BotId
    document_id: DocumentId
    tool_name: str
    chunk_count: int
    strategy_used: ChunkingStrategyName
    corpus_version: CorpusVersion
    embedding_model_version: EmbeddingModelVersion


@dataclass(frozen=True, kw_only=True, slots=True)
class DocumentFailed(DomainEvent):
    event_type: ClassVar[str] = "document.failed.v1"

    workspace_id: WorkspaceId
    job_id: JobId
    record_bot_id: BotId
    document_id: DocumentId
    stage: str  # "ocr" | "chunking" | "embedding" | "upsert"
    error_code: str
    error_message: str


@dataclass(frozen=True, kw_only=True, slots=True)
class DocumentArchived(DomainEvent):
    event_type: ClassVar[str] = "document.archived.v1"

    workspace_id: WorkspaceId
    record_bot_id: BotId
    document_id: DocumentId


@dataclass(frozen=True, kw_only=True, slots=True)
class DocumentPurged(DomainEvent):
    event_type: ClassVar[str] = "document.purged.v1"

    workspace_id: WorkspaceId
    record_bot_id: BotId
    document_id: DocumentId


@dataclass(frozen=True, kw_only=True, slots=True)
class CorpusVersionBumped(DomainEvent):
    event_type: ClassVar[str] = "corpus.version_changed.v1"

    workspace_id: WorkspaceId
    record_bot_id: BotId
    old_version: CorpusVersion
    new_version: CorpusVersion
    reason: str  # "doc_added" | "doc_deleted" | "rechunk" | "model_upgrade"


@dataclass(frozen=True, kw_only=True, slots=True)
class BotConfigUpdated(DomainEvent):
    event_type: ClassVar[str] = "bot.config_updated.v1"

    workspace_id: WorkspaceId
    record_bot_id: BotId
    bot_version_old: int
    bot_version_new: int
    fields_changed: list[str]


__all__ = [
    "BotConfigUpdated",
    "CorpusVersionBumped",
    "DocumentArchived",
    "DocumentFailed",
    "DocumentIngested",
    "DocumentPurged",
    "DocumentUploaded",
]
