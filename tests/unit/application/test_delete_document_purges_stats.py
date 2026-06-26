"""ING-7 — DeleteDocumentUseCase purges the stats index on delete.

A canonical DELETE archives the ``documents`` row + drops vector chunks.
The pre-extracted entities in ``document_service_index`` must ALSO be
removed so the price/list/keyword routes can never surface a deleted
catalog's entities. The serving queries also defensively join
``deleted_at IS NULL`` (belt-and-suspenders), but the purge frees the
rows immediately and keeps the index size bounded.

Contract pinned here:
  - the use case calls ``stats_index_repo.delete_by_document(doc.id)``;
  - the purge is best-effort — a stats-store hiccup must NOT abort the
    archive (the soft-delete + vector drop already protect retrieval);
  - when no stats repo is wired (None), delete still succeeds (passthrough).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.commands.document_commands import DeleteDocumentCommand
from ragbot.application.use_cases.delete_document import DeleteDocumentUseCase
from ragbot.shared.types import BotId, TenantId, TraceId, WorkspaceId


def _uow_factory() -> MagicMock:
    uow = AsyncMock()
    uow.add_outbox = AsyncMock()
    uow.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=uow)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _cmd() -> DeleteDocumentCommand:
    return DeleteDocumentCommand(
        record_tenant_id=TenantId(uuid4()),
        record_bot_id=BotId(uuid4()),
        workspace_id=WorkspaceId("ws-1"),
        tool_name="catalog",
        trace_id=TraceId("trace-1"),
    )


def _doc(doc_id):
    doc = MagicMock()
    doc.id = doc_id
    doc.archive.return_value = doc
    return doc


def _build(*, stats_repo, doc):
    doc_repo = AsyncMock()
    doc_repo.get_by_tool_name.return_value = doc
    doc_repo.save = AsyncMock()
    vector = AsyncMock()
    vector.delete_by_tool_name.return_value = 7
    return DeleteDocumentUseCase(
        doc_repo=doc_repo,
        bot_repo=AsyncMock(),
        vector_store=vector,
        uow_factory=_uow_factory(),
        clock=MagicMock(now=MagicMock(return_value="2026-01-01T00:00:00Z")),
        stats_index_repo=stats_repo,
    )


@pytest.mark.asyncio
async def test_delete_purges_stats_index() -> None:
    doc_id = uuid4()
    stats_repo = AsyncMock()
    stats_repo.delete_by_document.return_value = 3
    uc = _build(stats_repo=stats_repo, doc=_doc(doc_id))

    result = await uc.execute(_cmd())

    stats_repo.delete_by_document.assert_awaited_once_with(doc_id)
    assert result.deleted_chunks == 7


@pytest.mark.asyncio
async def test_delete_succeeds_without_stats_repo() -> None:
    uc = _build(stats_repo=None, doc=_doc(uuid4()))
    result = await uc.execute(_cmd())
    assert result.deleted_chunks == 7


@pytest.mark.asyncio
async def test_stats_purge_failure_does_not_abort_delete() -> None:
    """A stats-store error must not prevent the soft-delete / vector drop —
    retrieval is already protected by the archive + the serving-side filter."""
    stats_repo = AsyncMock()
    stats_repo.delete_by_document.side_effect = RuntimeError("stats db down")
    uc = _build(stats_repo=stats_repo, doc=_doc(uuid4()))

    # Must not raise.
    result = await uc.execute(_cmd())
    assert result.deleted_chunks == 7
