"""Regression — stats-index write is idempotent under re-processing.

Root cause (live SQL 2026-06-24): ``document_service_index`` accumulated
duplicate entities within a SINGLE active document (xe doc x3.00, spa x4).
The DELETE-before-INSERT in ``_stage_finalize`` was gated on ``is_reindex``
while the INSERT was unconditional. For a first-time ``doc_id``
(``is_reindex = existing_doc_id is not None`` = False) the stats path can still
run more than once for that same ``doc_id`` under Redis-Stream at-least-once
redelivery / worker retry — each pass inserting a full copy with NO preceding
delete, so duplicates accumulate.

Contract pinned here: the stats persist path ALWAYS deletes the document's
existing stats rows immediately before inserting the freshly-extracted set,
regardless of ``is_reindex``. On a brand-new doc the delete removes 0 rows
(cheap, safe); on any re-processing it guarantees exactly one copy.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragbot.application.services.document_service.ingest_stages import _IngestCtx
from ragbot.application.services.document_service.ingest_stages_final import (
    _StageFinalizeMixin,
)


class _Host(_StageFinalizeMixin):
    """Minimal host exposing only what ``_stage_finalize`` touches."""

    def __init__(self, stats_repo: object) -> None:
        self._sf = MagicMock()
        self._stats_index_repo = stats_repo
        # ADR-0006: _stage_finalize reads self._bot_repo for owner column_roles;
        # None → inference-only (no per-bot overrides in this unit).
        self._bot_repo = None
        # Skip the GraphRAG background task and use config defaults.
        self._cfg = SimpleNamespace(
            get_bool=AsyncMock(return_value=True),  # graph_rag_lazy_mode → skip
            get=AsyncMock(side_effect=lambda _k, default=None: default),
        )
        self._insert_stats_index = AsyncMock()
        self._upsert_doc_summary = AsyncMock()
        self._invalidate_corpus_version = AsyncMock()


def _fake_session_with_tenant() -> object:
    """Patch target for ``_core.session_with_tenant`` — yields a session whose
    state-flip SELECT reports a fully-embedded (=> active) document."""
    session = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = (2, 2, 0)  # total, embedded, null_non_parent
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *_a: object) -> bool:
            return False

    return MagicMock(return_value=_Cm())


def _build_ctx(*, doc_id: uuid.UUID, is_reindex: bool) -> _IngestCtx:
    ctx = _IngestCtx(
        record_bot_id=uuid.uuid4(),
        title="catalog",
        content="x",
        source_url="",
        source_type="manual",
        language="vi",
        mime_type="text/csv",
        existing_doc_id=doc_id if is_reindex else None,
        record_tenant_id=uuid.uuid4(),
        workspace_id="ws",
        channel_type="web",
        raw_bytes=None,
        file_name=None,
        blocks=None,
        step_tracker=None,
    )
    ctx.doc_id = doc_id
    ctx.is_reindex = is_reindex
    ctx.chunks = ["Gói A,500.000\nGói B,1.200.000"]
    ctx.chunks_to_embed = []
    ctx.unchanged_indices = []
    ctx.stale_indices = []
    ctx.any_embedded = True
    # Table-shaped rows so parse_table_chunks yields entities.
    ctx.rows = [{"content": "Gói A,500.000\nGói B,1.200.000", "meta": None}]
    return ctx


@pytest.mark.asyncio
async def test_stats_delete_runs_even_when_not_reindex() -> None:
    """is_reindex=False must STILL delete before insert (idempotent write)."""
    stats_repo = AsyncMock()
    stats_repo.delete_by_document = AsyncMock(return_value=0)
    host = _Host(stats_repo)
    doc_id = uuid.uuid4()
    ctx = _build_ctx(doc_id=doc_id, is_reindex=False)

    with patch(
        "ragbot.application.services.document_service.ingest_core."
        "session_with_tenant",
        new=_fake_session_with_tenant(),
    ):
        await host._stage_finalize(ctx)

    stats_repo.delete_by_document.assert_awaited_once_with(doc_id)
    host._insert_stats_index.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_passes_each_delete_first_no_duplicate_accumulation() -> None:
    """Re-processing the same doc_id (is_reindex=False both passes) deletes
    each time → the index never accumulates a second copy."""
    stats_repo = AsyncMock()
    stats_repo.delete_by_document = AsyncMock(return_value=0)
    host = _Host(stats_repo)
    doc_id = uuid.uuid4()

    with patch(
        "ragbot.application.services.document_service.ingest_core."
        "session_with_tenant",
        new=_fake_session_with_tenant(),
    ):
        await host._stage_finalize(_build_ctx(doc_id=doc_id, is_reindex=False))
        await host._stage_finalize(_build_ctx(doc_id=doc_id, is_reindex=False))

    assert stats_repo.delete_by_document.await_count == 2, (
        "delete must precede insert on EVERY pass, not only on the reindex path"
    )
    assert host._insert_stats_index.await_count == 2
