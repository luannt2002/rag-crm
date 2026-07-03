"""IngestDocumentUseCase — 202 Accepted + outbox DocumentUploaded.

Ref: PLAN_08 §ingest_document.py.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

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
            record_bot_id=str(cmd.record_bot_id),  # scope the key per-bot: else a 2nd bot on the same URL collides
            source_url=str(cmd.source_url),
            corpus_version=0,
            workspace_id=str(getattr(cmd, "workspace_id", "") or ""),
        )
        tool_name = slugify(cmd.document_name)[:255]

        # Natural-key identity: the (tenant, bot, tool_name) row owns the
        # document, not the surrogate UUID. A surviving row — archived after a
        # canonical DELETE, or a failed DRAFT — means the caller wants to
        # re-ingest THIS logical doc. The surviving row is authoritative over
        # the 24h source_url Redis key (registered at enqueue, never reconciled
        # with the job outcome), so we honour the fast-path double-POST dedup
        # ONLY when no row survives (a genuine first ingest before the row is
        # created). Otherwise we fall through and reactivate it in place.
        existing = await self._docs.get_by_tool_name(
            cmd.record_bot_id, tool_name, record_tenant_id=cmd.record_tenant_id,
        )
        if existing is None and await self._idem.is_duplicate(idem_key):
            prior = await self._idem.get_prior_result_ref(idem_key)
            if prior:
                logger.info("ingest_document.idempotency_hit", key=idem_key)
                return IngestAcceptedDTO(
                    job_id=JobId(UUID(prior)),
                    tool_name=tool_name,
                    status="queued",
                    trace_id=cmd.trace_id,
                )

        job_id = JobId(uuid4())

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
            if existing is not None:
                # Reuse the surviving row's PK so save() UPDATEs (reactivates)
                # in place instead of INSERTing a colliding (tenant, bot,
                # tool_name) row → uq_doc_tool. Fixes re-ingest after a
                # canonical DELETE (archived row) and after a failed DRAFT.
                doc = replace(doc, id=existing.id)
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
