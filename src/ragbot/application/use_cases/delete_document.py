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
    ) -> None:
        self._docs = doc_repo
        self._bots = bot_repo
        self._vector = vector_store
        self._uow_factory = uow_factory
        self._clock = clock

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
