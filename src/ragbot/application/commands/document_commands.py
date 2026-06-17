"""Document commands.

Ref: PLAN_05 §document_commands.py.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ragbot.shared.constants import (
    DEFAULT_AUTHORITY_SCORE,
    DEFAULT_LANGUAGE,
    MAX_DOCUMENT_NAME_LENGTH,
)
from ragbot.shared.types import (
    BotId,
    EmbeddingModelVersion,
    TenantId,
    TraceId,
    WorkspaceId,
)


class IngestDocumentCommand(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    workspace_id: WorkspaceId
    source_url: HttpUrl
    document_name: str = Field(min_length=1, max_length=MAX_DOCUMENT_NAME_LENGTH)
    mime_type: str | None = None
    language: str = DEFAULT_LANGUAGE
    authority_score: float = Field(default=DEFAULT_AUTHORITY_SCORE, ge=0.0, le=1.0)
    uploaded_by: str | None = None
    trace_id: TraceId


class DeleteDocumentCommand(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    workspace_id: WorkspaceId
    tool_name: str = Field(min_length=1, max_length=MAX_DOCUMENT_NAME_LENGTH)
    trace_id: TraceId


class RechunkDocumentCommand(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    workspace_id: WorkspaceId
    source_url: HttpUrl
    trace_id: TraceId


class RechunkByDocumentIdCommand(BaseModel):
    """Rechunk a single document by its internal UUID.

    260525 Bug #2 fix — the URL-keyed ``RechunkDocumentCommand`` cannot
    address bots with multiple documents sharing the same ``source_url``
    (Google Sheets workbook with multiple tab gids, S3 paths refused
    by re-upload, etc.). This command lets the caller name the exact
    document by primary key so the use case never has to disambiguate.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    record_bot_id: BotId
    workspace_id: WorkspaceId
    document_id: UUID
    trace_id: TraceId


class ReindexCorpusCommand(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_tenant_id: TenantId
    new_embedding_model_version: EmbeddingModelVersion
    dry_run: bool = False
    trace_id: TraceId


__all__ = [
    "DeleteDocumentCommand",
    "IngestDocumentCommand",
    "RechunkByDocumentIdCommand",
    "RechunkDocumentCommand",
    "ReindexCorpusCommand",
]
