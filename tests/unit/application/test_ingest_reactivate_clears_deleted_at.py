"""Regression — re-ingesting a soft-deleted document clears ``deleted_at``.

Bug (live 2026-06-24): the demo UI showed "tài liệu = 0" for bots whose corpus
was soft-deleted then re-ingested. Root cause: ``delete_*`` sets
``documents.deleted_at = now()`` WITHOUT changing ``state``, and the ingest
state-flip in ``_stage_finalize`` set ``state='active'`` WITHOUT clearing
``deleted_at`` — so a re-ingested doc ended up ``state='active'`` + live chunks
(retrievable, answering) yet ``deleted_at IS NOT NULL`` (invisible to the
``deleted_at IS NULL`` count the UI uses).

Contract pinned: when the terminal state-flip lands on ``active`` (a
successfully (re)ingested document is live, not deleted) it MUST clear
``deleted_at``. A non-active (failed) flip leaves ``deleted_at`` untouched.
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
    def __init__(self) -> None:
        self._sf = MagicMock()
        self._stats_index_repo = None  # skip the stats block — irrelevant here
        self._cfg = SimpleNamespace(
            get_bool=AsyncMock(return_value=True),
            get=AsyncMock(side_effect=lambda _k, default=None: default),
        )
        self._invalidate_corpus_version = AsyncMock()


def _session_capturing(captured: list[str]) -> object:
    session = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = (2, 2, 0)  # total, embedded, null → active

    async def _execute(stmt, params=None):  # noqa: ANN001
        captured.append(str(stmt))
        return result

    session.execute = _execute
    session.commit = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *_a: object) -> bool:
            return False

    return MagicMock(return_value=_Cm())


def _ctx() -> _IngestCtx:
    ctx = _IngestCtx(
        record_bot_id=uuid.uuid4(), title="doc", content="x", source_url="",
        source_type="manual", language="vi", mime_type="text/csv",
        existing_doc_id=uuid.uuid4(), record_tenant_id=uuid.uuid4(),
        workspace_id="ws", channel_type="web", raw_bytes=None, file_name=None,
        blocks=None, step_tracker=None,
    )
    ctx.is_reindex = True
    ctx.chunks = ["a"]
    ctx.any_embedded = True
    return ctx


@pytest.mark.asyncio
async def test_active_flip_clears_deleted_at() -> None:
    captured: list[str] = []
    host = _Host()
    with patch(
        "ragbot.application.services.document_service.ingest_core."
        "session_with_tenant",
        new=_session_capturing(captured),
    ):
        await host._stage_finalize(_ctx())

    flip_sql = [s for s in captured if "update documents" in s.lower()]
    assert flip_sql, "state-flip UPDATE documents must run"
    sql = flip_sql[0].lower()
    assert "deleted_at" in sql, (
        "the active state-flip must clear deleted_at so a re-ingested doc is "
        "no longer hidden as soft-deleted"
    )
    # Regression guard (2026-06-25): the clear MUST use a dedicated bool param,
    # NOT ``:s = 'active'``. Binding the same ``:s`` in both a varchar
    # assignment (state = :s) and a text comparison makes asyncpg raise
    # AmbiguousParameterError at prepare time → the whole state-flip fails and
    # the doc never goes active (it broke a real re-ingest before this guard).
    assert ":clear_deleted" in sql, "deleted_at clear must bind a :clear_deleted bool param"
    assert ":s = 'active'" not in sql, (
        "must NOT compare :s = 'active' in the CASE (asyncpg ambiguous-param)"
    )
