"""IngestDocumentUseCase — 202 Accepted + outbox DocumentUploaded.

Ref: PLAN_08 §ingest_document.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from slugify import slugify

from ragbot.application.commands.document_commands import IngestDocumentCommand
from ragbot.application.dto.document_dto import IngestAcceptedDTO
from ragbot.application.services.idempotency import IdempotencyService
from ragbot.domain.entities.document import Document
from ragbot.domain.events.document_events import DocumentUploaded
from ragbot.domain.value_objects.idempotency_key import for_ingest_document
from ragbot.domain.value_objects.versioning import AuthorityScore
from ragbot.shared.types import JobId

if TYPE_CHECKING:
    from ragbot.application.ports.repository_ports import (
        BotRepositoryPort,
        DocumentRepositoryPort,
        JobRepositoryPort,
        UnitOfWorkPort,
    )
    from ragbot.shared.clock import Clock

logger = structlog.get_logger(__name__)


class IngestDocumentUseCase:
    def __init__(
        self,
        *,
        doc_repo: DocumentRepositoryPort,
        bot_repo: BotRepositoryPort,
        job_repo: JobRepositoryPort,
        uow_factory: object,
        idempotency: IdempotencyService,
        clock: Clock,
    ) -> None:
        self._docs = doc_repo
        self._bots = bot_repo
        self._jobs = job_repo
        self._uow_factory = uow_factory
        self._idem = idempotency
        self._clock = clock

    async def execute(self, cmd: IngestDocumentCommand) -> IngestAcceptedDTO:
        # Versioning columns dropped in migration 0011 — corpus_version no
        # longer tracked. Idempotency key now relies on (tenant, source_url)
        # with a constant version slot.
        idem_key = for_ingest_document(
            record_tenant_id=str(cmd.record_tenant_id),
            source_url=str(cmd.source_url),
            corpus_version=0,
        )
        if await self._idem.is_duplicate(idem_key):
            prior = await self._idem.get_prior_result_ref(idem_key)
            if prior:
                logger.info("ingest_document.idempotency_hit", key=idem_key)
                return IngestAcceptedDTO(
                    job_id=JobId(__import__("uuid").UUID(prior)),
                    tool_name=slugify(cmd.document_name)[:255],
                    status="queued",
                    trace_id=cmd.trace_id,
                )

        job_id = JobId(uuid4())
        tool_name = slugify(cmd.document_name)[:255]

        uow_call = self._uow_factory  # type: ignore[assignment]
        async with uow_call() as uow:  # type: ignore[operator]
            doc = Document.new_draft(
                record_tenant_id=cmd.record_tenant_id,
                record_bot_id=cmd.record_bot_id,
                source_url=str(cmd.source_url),
                document_name=cmd.document_name,
                tool_name=tool_name,
                mime_type=cmd.mime_type or "application/octet-stream",
                language=cmd.language,
                content_hash="pending",  # set after fetch in worker
                authority_score=AuthorityScore(cmd.authority_score),
                validity_window=None,
                acl=(),
                created_at=self._clock.now(),
            )
            await self._docs.save(
                doc,
                record_tenant_id=cmd.record_tenant_id,
                workspace_id=cmd.workspace_id,
            )
            await self._jobs.create(
                job_id=job_id,
                record_tenant_id=cmd.record_tenant_id,
                kind="document.ingest",
                payload={
                    "bot_id": str(cmd.record_bot_id),
                    "document_id": str(doc.id),
                    "source_url": str(cmd.source_url),
                    "tool_name": tool_name,
                    "workspace_id": cmd.workspace_id,
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
                source_url=str(cmd.source_url),
                document_name=cmd.document_name,
                tool_name=tool_name,
                mime_type=cmd.mime_type or "application/octet-stream",
                uploaded_by=cmd.uploaded_by,
            )
            await uow.add_outbox(event)
            await uow.commit()

        await self._idem.register(idem_key, result_ref=str(job_id))

        return IngestAcceptedDTO(
            job_id=job_id,
            tool_name=tool_name,
            status="queued",
            trace_id=cmd.trace_id,
        )


__all__ = ["IngestDocumentUseCase"]
