"""Repository Protocols. Tenant-scoped enforce kwargs.

Ref: PLAN_06 §repository_ports.py.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.document import Document
from ragbot.domain.events.base import DomainEvent
from ragbot.shared.constants import DEFAULT_RAG_TOP_K
from ragbot.shared.types import (
    BotId,
    BotVersion,
    ConversationId,
    CorpusVersion,
    DocumentId,
    JobId,
    JobStatus,
    TenantId,
    UserId,
    WorkspaceId,
)


@runtime_checkable
class UnitOfWorkPort(Protocol):
    async def __aenter__(self) -> UnitOfWorkPort: ...
    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def add_outbox(self, event: DomainEvent) -> None: ...


@runtime_checkable
class ConversationRepositoryPort(Protocol):
    async def get_or_create(
        self,
        record_bot_id: BotId,
        connect_id: UserId,
        *,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
    ) -> Conversation: ...

    async def get_by_id(
        self,
        conversation_id: ConversationId,
        *,
        record_tenant_id: TenantId,
    ) -> Conversation | None: ...

    async def save(
        self,
        conversation: Conversation,
        *,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
    ) -> None: ...


@runtime_checkable
class DocumentRepositoryPort(Protocol):
    async def save(
        self,
        document: Document,
        *,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
    ) -> None: ...
    async def get_by_id(
        self,
        document_id: DocumentId,
        *,
        record_tenant_id: TenantId,
    ) -> Document | None: ...
    async def get_by_source_url(
        self,
        record_bot_id: BotId,
        source_url: str,
        *,
        record_tenant_id: TenantId,
    ) -> Document | None: ...
    async def get_by_tool_name(
        self,
        record_bot_id: BotId,
        tool_name: str,
        *,
        record_tenant_id: TenantId,
    ) -> Document | None: ...
    async def list_by_bot(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_RAG_TOP_K,
    ) -> list[Document]: ...


@runtime_checkable
class BotRepositoryPort(Protocol):
    """Bot repository port — schema mới (migration 0011).

    Versioning / callback fields đã bị loại bỏ khỏi bảng `bots`; các
    method tương ứng đã gỡ. Ingest/delete document use-cases đã chuyển
    qua ``DocumentRepositoryPort`` + ``VectorStorePort`` + outbox events
    (xem application/use_cases/ingest_document.py và delete_document.py),
    không còn phụ thuộc vào bot-level write paths.
    """

    async def find_by_4key(
        self,
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> object | None: ...

    async def find_by_3key_unique(
        self,
        record_tenant_id: UUID,
        bot_id: str,
        channel_type: str,
    ) -> object | None: ...

    async def list_active(
        self, *, record_tenant_id: UUID | None,
    ) -> list[object]: ...


@runtime_checkable
class JobRepositoryPort(Protocol):
    async def create(
        self,
        *,
        job_id: JobId,
        record_tenant_id: TenantId,
        kind: str,
        payload: dict[str, object],
    ) -> None: ...

    async def update_status(
        self,
        job_id: JobId,
        *,
        record_tenant_id: TenantId | None,
        status: JobStatus,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None: ...

    async def get(
        self,
        job_id: JobId,
        *,
        record_tenant_id: TenantId,
    ) -> dict[str, object] | None: ...


@runtime_checkable
class QuotaRepositoryPort(Protocol):
    async def get(self, *, record_tenant_id: TenantId) -> dict[str, object]: ...
    async def increment_usage(
        self,
        *,
        record_tenant_id: TenantId,
        tokens: int,
        cost_usd: float,
    ) -> None: ...
    async def check_within_budget(
        self,
        *,
        record_tenant_id: TenantId,
        estimated_tokens: int,
    ) -> bool: ...


__all__ = [
    "BotRepositoryPort",
    "ConversationRepositoryPort",
    "DocumentRepositoryPort",
    "JobRepositoryPort",
    "QuotaRepositoryPort",
    "UnitOfWorkPort",
]
