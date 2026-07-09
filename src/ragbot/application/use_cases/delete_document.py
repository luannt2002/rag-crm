"""DeleteDocumentUseCase — sync vector delete + archive document."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.application.commands.document_commands import DeleteDocumentCommand
from ragbot.application.dto.document_dto import DeleteResultDTO
from ragbot.application.services.tenant_guard import TenantGuardService
from ragbot.domain.events.document_events import DocumentArchived
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.types import CorpusVersion

if TYPE_CHECKING:
    from ragbot.application.ports.repository_ports import (
        BotRepositoryPort,
        DocumentRepositoryPort,
        UnitOfWorkPort,
    )
    from ragbot.application.ports.vector_store_port import VectorStorePort
    from ragbot.shared.clock import Clock

logger = structlog.get_logger(__name__)


class DeleteDocumentUseCase:
    def __init__(
        self,
        *,
        doc_repo: DocumentRepositoryPort,
        bot_repo: BotRepositoryPort,
        vector_store: VectorStorePort,
        uow_factory: object,
        clock: Clock,
        stats_index_repo: object | None = None,
    ) -> None:
        self._docs = doc_repo
        self._bots = bot_repo
        self._vector = vector_store
        self._uow_factory = uow_factory
        self._clock = clock
        # ING-7: purge the pre-extracted entities for this document from
        # ``document_service_index`` on delete so the price/list/keyword routes
        # stop surfacing a removed catalog. Optional — None = passthrough
        # (the serving queries also defensively filter ``deleted_at IS NULL``).
        self._stats_index_repo = stats_index_repo

    async def execute(self, cmd: DeleteDocumentCommand) -> DeleteResultDTO:
        TenantGuardService.assert_owns(cmd.record_tenant_id, cmd.record_tenant_id)

        doc = await self._docs.get_by_tool_name(
            cmd.record_bot_id, cmd.tool_name, record_tenant_id=cmd.record_tenant_id,
        )
        if doc is None:
            raise InvariantViolation(
                f"Document tool_name={cmd.tool_name} not found for bot {cmd.record_bot_id}",
            )

        deleted = await self._vector.delete_by_tool_name(
            cmd.record_bot_id, cmd.tool_name, record_tenant_id=cmd.record_tenant_id,
        )

        uow_call = self._uow_factory  # type: ignore[assignment]
        async with uow_call() as uow:  # type: ignore[operator]
            archived = doc.archive()
            await self._docs.save(
                archived,
                record_tenant_id=cmd.record_tenant_id,
                workspace_id=cmd.workspace_id,
            )

            await uow.add_outbox(
                DocumentArchived(
                    occurred_at=self._clock.now(),
                    record_tenant_id=cmd.record_tenant_id,
                    trace_id=cmd.trace_id,
                    workspace_id=cmd.workspace_id,
                    record_bot_id=cmd.record_bot_id,
                    document_id=doc.id,
                ),
            )
            await uow.commit()

        # ING-7: purge the document's stats-index entities AFTER the archive
        # commits. Best-effort — a stats-store failure must not abort the
        # delete (retrieval is already protected: the document is soft-deleted
        # and the serving queries filter ``deleted_at IS NULL``).
        if self._stats_index_repo is not None:
            try:
                purged = await self._stats_index_repo.delete_by_document(
                    doc.id, record_bot_id=cmd.record_bot_id
                )
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                logger.warning(
                    "delete_document.stats_purge_failed",
                    doc_id=str(doc.id),
                    error_type=type(exc).__name__,
                )
            else:
                logger.info(
                    "delete_document.stats_purged",
                    doc_id=str(doc.id),
                    rows_purged=purged,
                )

        logger.info(
            "delete_document.completed",
            doc_id=str(doc.id),
            deleted_chunks=deleted,
        )

        return DeleteResultDTO(
            deleted_chunks=deleted,
            corpus_version=CorpusVersion(0),
        )


__all__ = ["DeleteDocumentUseCase"]
