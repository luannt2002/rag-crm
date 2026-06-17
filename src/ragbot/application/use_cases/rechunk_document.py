"""RechunkDocumentUseCase — re-ingest existing document."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from ragbot.application.commands.document_commands import (
    RechunkByDocumentIdCommand,
    RechunkDocumentCommand,
)
from ragbot.application.dto.document_dto import IngestAcceptedDTO
from ragbot.domain.events.document_events import DocumentUploaded
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.types import JobId

if TYPE_CHECKING:
    from ragbot.application.ports.repository_ports import (
        DocumentRepositoryPort,
        JobRepositoryPort,
        UnitOfWorkPort,
    )
    from ragbot.application.ports.vector_store_port import VectorStorePort
    from ragbot.shared.clock import Clock

logger = structlog.get_logger(__name__)


def _assert_reingestable(doc: object) -> None:
    """Validate the document carries a usable content source.

    A rechunk re-ingests from either the stored ``raw_content`` (reused by
    the worker) or, failing that, a refetch of ``source_url``. If a document
    has neither, wiping its existing chunks would destroy the only copy with
    nothing to rebuild from — silent data loss. This invariant MUST be
    checked BEFORE any destructive delete so it fails loud and the chunks
    survive.
    """
    source_url = (getattr(doc, "source_url", "") or "").strip()
    metadata = getattr(doc, "metadata", None) or {}
    raw_content = ""
    if isinstance(metadata, dict):
        raw_content = (metadata.get("raw_content") or "").strip()
    if not source_url and not raw_content:
        raise InvariantViolation(
            f"document {getattr(doc, 'id', '?')} has no usable content source "
            "(empty source_url and no raw_content) — refusing to wipe chunks",
        )


class RechunkDocumentUseCase:
    def __init__(
        self,
        *,
        doc_repo: DocumentRepositoryPort,
        job_repo: JobRepositoryPort,
        vector_store: VectorStorePort,
        uow_factory: object,
        clock: Clock,
    ) -> None:
        self._docs = doc_repo
        self._jobs = job_repo
        self._vector = vector_store
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, cmd: RechunkDocumentCommand) -> IngestAcceptedDTO:
        doc = await self._docs.get_by_source_url(
            cmd.record_bot_id, str(cmd.source_url), record_tenant_id=cmd.record_tenant_id,
        )
        if doc is None:
            raise InvariantViolation(
                f"document not found: {cmd.source_url}",
            )

        # Validate ALL preconditions BEFORE the destructive delete: a doc
        # with no usable content source cannot be rebuilt, so wiping its
        # chunks would be silent, unrecoverable data loss.
        _assert_reingestable(doc)

        # Wipe old chunks (sync — small payload usually fast)
        await self._vector.delete_by_document(doc.id, record_tenant_id=cmd.record_tenant_id)

        job_id = JobId(uuid4())
        uow_call = self._uow_factory  # type: ignore[assignment]
        async with uow_call() as uow:  # type: ignore[operator]
            await self._jobs.create(
                job_id=job_id,
                record_tenant_id=cmd.record_tenant_id,
                kind="document.rechunk",
                payload={
                    "bot_id": str(cmd.record_bot_id),
                    "document_id": str(doc.id),
                    "source_url": str(cmd.source_url),
                    "tool_name": doc.tool_name,
                },
            )
            event = DocumentUploaded(
                occurred_at=self._clock.now(),
                record_tenant_id=cmd.record_tenant_id,
                trace_id=cmd.trace_id,
                workspace_id=cmd.workspace_id,
                job_id=job_id,
                record_bot_id=cmd.record_bot_id,
                document_id=doc.id,
                source_url=doc.source_url,
                document_name=doc.document_name,
                tool_name=doc.tool_name,
                mime_type=doc.mime_type,
                force_reingest=True,
            )
            await uow.add_outbox(event)
            await uow.commit()

        return IngestAcceptedDTO(
            job_id=job_id,
            tool_name=doc.tool_name,
            status="queued",
            trace_id=cmd.trace_id,
        )

    async def execute_by_document_id(
        self, cmd: RechunkByDocumentIdCommand,
    ) -> IngestAcceptedDTO:
        """Rechunk a document by its UUID primary key.

        260525 Bug #2 fix — sibling of :meth:`execute` that addresses
        the document directly so URL-based lookup ambiguity (multiple
        docs sharing source_url, e.g. Google Sheets workbook tabs) is
        eliminated.
        """
        doc = await self._docs.get_by_id(
            cmd.document_id, record_tenant_id=cmd.record_tenant_id,
        )
        if doc is None:
            raise InvariantViolation(
                f"document not found: id={cmd.document_id}",
            )
        if doc.record_bot_id != cmd.record_bot_id:
            # Tenant + doc UUID may exist but belong to a different bot
            # — defence against cross-bot rechunk inside the same tenant.
            raise InvariantViolation(
                f"document {cmd.document_id} belongs to a different bot",
            )

        # Validate ALL preconditions BEFORE the destructive delete: a doc
        # with no usable content source cannot be rebuilt, so wiping its
        # chunks would be silent, unrecoverable data loss.
        _assert_reingestable(doc)

        # Wipe old chunks (sync — small payload usually fast)
        await self._vector.delete_by_document(
            doc.id, record_tenant_id=cmd.record_tenant_id,
        )

        job_id = JobId(uuid4())
        uow_call = self._uow_factory  # type: ignore[assignment]
        async with uow_call() as uow:  # type: ignore[operator]
            await self._jobs.create(
                job_id=job_id,
                record_tenant_id=cmd.record_tenant_id,
                kind="document.rechunk",
                payload={
                    "bot_id": str(cmd.record_bot_id),
                    "document_id": str(doc.id),
                    "source_url": str(doc.source_url),
                    "tool_name": doc.tool_name,
                },
            )
            event = DocumentUploaded(
                occurred_at=self._clock.now(),
                record_tenant_id=cmd.record_tenant_id,
                trace_id=cmd.trace_id,
                workspace_id=cmd.workspace_id,
                job_id=job_id,
                record_bot_id=cmd.record_bot_id,
                document_id=doc.id,
                source_url=doc.source_url,
                document_name=doc.document_name,
                tool_name=doc.tool_name,
                mime_type=doc.mime_type,
                force_reingest=True,
            )
            await uow.add_outbox(event)
            await uow.commit()

        return IngestAcceptedDTO(
            job_id=job_id,
            tool_name=doc.tool_name,
            status="queued",
            trace_id=cmd.trace_id,
        )


__all__ = ["RechunkDocumentUseCase"]
