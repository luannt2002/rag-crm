"""Rechunk must validate re-ingestability BEFORE wiping chunks.

Confirmed bug: ``RechunkDocumentUseCase`` deleted existing chunks via
``vector_store.delete_by_document`` BEFORE checking the document still had a
usable content source. A document with an empty ``source_url`` and no
``raw_content`` therefore had its chunks destroyed with nothing to rebuild
from — silent, unrecoverable data loss.

Fix: validate all preconditions (exists, belongs to bot, has a content
source) FIRST; if the content-source invariant fails, raise
``InvariantViolation`` and DO NOT call ``delete_by_document``.

Pins both entry points: :meth:`execute` (URL-keyed) and
:meth:`execute_by_document_id` (PK-keyed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.shared.errors import InvariantViolation


@dataclass
class _StubDoc:
    id: UUID
    record_bot_id: UUID
    source_url: str
    document_name: str
    tool_name: str
    mime_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _StubUoW:
    add_outbox: AsyncMock = field(default_factory=lambda: AsyncMock())
    commit: AsyncMock = field(default_factory=lambda: AsyncMock())

    async def __aenter__(self) -> "_StubUoW":
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False


def _make_uc(doc_lookup_result: _StubDoc | None) -> tuple[Any, MagicMock]:
    """Build a RechunkDocumentUseCase with mocked deps.

    Returns the use case and the vector-store mock so the test can assert
    on ``delete_by_document`` call state.
    """
    from ragbot.application.use_cases.rechunk_document import (
        RechunkDocumentUseCase,
    )

    docs = MagicMock()
    docs.get_by_id = AsyncMock(return_value=doc_lookup_result)
    docs.get_by_source_url = AsyncMock(return_value=doc_lookup_result)

    jobs = MagicMock()
    jobs.create = AsyncMock(return_value=None)

    vector = MagicMock()
    vector.delete_by_document = AsyncMock(return_value=None)

    def _uow_factory() -> _StubUoW:
        return _StubUoW()

    clock = MagicMock()
    clock.now = MagicMock(return_value=datetime.now(tz=timezone.utc))

    uc = RechunkDocumentUseCase(
        doc_repo=docs,
        job_repo=jobs,
        vector_store=vector,
        uow_factory=_uow_factory,
        clock=clock,
    )
    return uc, vector


# ---------------------------------------------------------------------------
# execute_by_document_id — PK-keyed entry point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_by_id_no_content_source_raises_and_does_not_wipe() -> None:
    """Empty source_url + no raw_content → raise, never touch vector store."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    bot = uuid4()
    doc_id = uuid4()
    bad_doc = _StubDoc(
        id=doc_id,
        record_bot_id=bot,
        source_url="",  # no remote source
        document_name="orphan",
        tool_name="orphan",
        mime_type="text/plain",
        metadata={},  # no raw_content
    )
    uc, vector = _make_uc(bad_doc)

    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=uuid4(),
        record_bot_id=bot,
        workspace_id="ws",
        document_id=doc_id,
        trace_id="t",
    )
    with pytest.raises(InvariantViolation, match="no usable content source"):
        await uc.execute_by_document_id(cmd)

    vector.delete_by_document.assert_not_called()


@pytest.mark.asyncio
async def test_by_id_whitespace_only_source_raises_and_does_not_wipe() -> None:
    """Whitespace-only source_url counts as empty — still refuses to wipe."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    bot = uuid4()
    doc_id = uuid4()
    bad_doc = _StubDoc(
        id=doc_id,
        record_bot_id=bot,
        source_url="   \n\t  ",
        document_name="orphan",
        tool_name="orphan",
        mime_type="text/plain",
        metadata={"raw_content": "   "},  # whitespace-only inline content
    )
    uc, vector = _make_uc(bad_doc)

    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=uuid4(),
        record_bot_id=bot,
        workspace_id="ws",
        document_id=doc_id,
        trace_id="t",
    )
    with pytest.raises(InvariantViolation, match="no usable content source"):
        await uc.execute_by_document_id(cmd)

    vector.delete_by_document.assert_not_called()


@pytest.mark.asyncio
async def test_by_id_valid_source_url_proceeds_and_wipes() -> None:
    """A doc with a real source_url proceeds: chunks wiped, job queued."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    bot = uuid4()
    doc_id = uuid4()
    good_doc = _StubDoc(
        id=doc_id,
        record_bot_id=bot,
        source_url="https://example.test/doc.csv",
        document_name="doc.csv",
        tool_name="doc_csv",
        mime_type="text/csv",
        metadata={},
    )
    uc, vector = _make_uc(good_doc)

    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=uuid4(),
        record_bot_id=bot,
        workspace_id="ws",
        document_id=doc_id,
        trace_id="t",
    )
    result = await uc.execute_by_document_id(cmd)

    assert result.status == "queued"
    vector.delete_by_document.assert_called_once()


@pytest.mark.asyncio
async def test_by_id_raw_content_only_proceeds_and_wipes() -> None:
    """Inline raw_content with empty source_url is still re-ingestable."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    bot = uuid4()
    doc_id = uuid4()
    good_doc = _StubDoc(
        id=doc_id,
        record_bot_id=bot,
        source_url="",
        document_name="inline",
        tool_name="inline",
        mime_type="text/plain",
        metadata={"raw_content": "real stored content here"},
    )
    uc, vector = _make_uc(good_doc)

    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=uuid4(),
        record_bot_id=bot,
        workspace_id="ws",
        document_id=doc_id,
        trace_id="t",
    )
    result = await uc.execute_by_document_id(cmd)

    assert result.status == "queued"
    vector.delete_by_document.assert_called_once()


# ---------------------------------------------------------------------------
# execute — URL-keyed entry point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_no_content_source_raises_and_does_not_wipe() -> None:
    """URL-keyed path must also validate before wiping."""
    from ragbot.application.commands import RechunkDocumentCommand

    bot = uuid4()
    bad_doc = _StubDoc(
        id=uuid4(),
        record_bot_id=bot,
        source_url="",
        document_name="orphan",
        tool_name="orphan",
        mime_type="text/plain",
        metadata={},
    )
    uc, vector = _make_uc(bad_doc)

    cmd = RechunkDocumentCommand(
        record_tenant_id=uuid4(),
        record_bot_id=bot,
        workspace_id="ws",
        source_url="https://example.test/whatever",
        trace_id="t",
    )
    with pytest.raises(InvariantViolation, match="no usable content source"):
        await uc.execute(cmd)

    vector.delete_by_document.assert_not_called()
