"""Pin tests — 260525 Phase D Bug #1+#2 rechunk-by-id + ambiguity guard.

Two related fixes:

Bug #1 — ``DocumentRepository.get_by_source_url`` previously returned the
first row when more than one document shared a source_url. Rechunk
silently mutated the wrong document. Fixed: now raises
``InvariantViolation`` when ≥2 rows match.

Bug #2 — ``RechunkDocumentUseCase`` had no path to rechunk by primary
key, forcing every caller through the URL-keyed lookup. Fixed: new
``RechunkByDocumentIdCommand`` + ``execute_by_document_id`` method.

These tests cover the command + use case wiring directly; the HTTP
route surface is covered by the smoke test in
``test_documents_rechunk_by_id_route.py`` (also added).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest


# ---------------------------------------------------------------------------
# Bug #2: RechunkByDocumentIdCommand wiring
# ---------------------------------------------------------------------------


def test_rechunk_by_document_id_command_exists_and_validates() -> None:
    """The new command must round-trip through pydantic validation."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    tenant = uuid4()
    bot = uuid4()
    doc = uuid4()
    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=tenant,
        record_bot_id=bot,
        workspace_id="some-workspace",
        document_id=doc,
        trace_id="test-trace-123",
    )
    assert cmd.document_id == doc
    assert cmd.record_tenant_id == tenant


def test_rechunk_by_document_id_command_requires_uuid() -> None:
    """document_id must be a UUID — string slugs rejected by pydantic."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    with pytest.raises(Exception):  # noqa: BLE001 — pydantic ValidationError
        RechunkByDocumentIdCommand(
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            workspace_id="ws",
            document_id="not-a-uuid",
            trace_id="t",
        )


# ---------------------------------------------------------------------------
# Bug #2: use case execute_by_document_id
# ---------------------------------------------------------------------------


@dataclass
class _StubDoc:
    id: UUID
    record_bot_id: UUID
    source_url: str
    document_name: str
    tool_name: str
    mime_type: str


@dataclass
class _StubUoW:
    add_outbox: AsyncMock = field(default_factory=lambda: AsyncMock())
    commit: AsyncMock = field(default_factory=lambda: AsyncMock())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _make_uc(doc_lookup_result: _StubDoc | None) -> Any:
    """Build a RechunkDocumentUseCase with mocked deps."""
    from ragbot.application.use_cases.rechunk_document import RechunkDocumentUseCase

    docs = MagicMock()
    docs.get_by_id = AsyncMock(return_value=doc_lookup_result)

    jobs = MagicMock()
    jobs.create = AsyncMock(return_value=None)

    vector = MagicMock()
    vector.delete_by_document = AsyncMock(return_value=None)

    def _uow_factory():
        return _StubUoW()

    clock = MagicMock()
    clock.now = MagicMock(return_value=__import__("datetime").datetime.now(
        tz=__import__("datetime").timezone.utc,
    ))

    return RechunkDocumentUseCase(
        doc_repo=docs,
        job_repo=jobs,
        vector_store=vector,
        uow_factory=_uow_factory,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_execute_by_document_id_dispatches_when_doc_matches() -> None:
    """Happy path — document exists, belongs to the requested bot."""
    from ragbot.application.commands import RechunkByDocumentIdCommand

    tenant = uuid4()
    bot = uuid4()
    doc_id = uuid4()
    stub_doc = _StubDoc(
        id=doc_id,
        record_bot_id=bot,
        source_url="https://example.test/doc",
        document_name="test.csv",
        tool_name="test_csv",
        mime_type="text/csv",
    )
    uc = _make_uc(stub_doc)

    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=tenant,
        record_bot_id=bot,
        workspace_id="ws",
        document_id=doc_id,
        trace_id="t",
    )
    result = await uc.execute_by_document_id(cmd)
    assert result.status == "queued"


@pytest.mark.asyncio
async def test_execute_by_document_id_raises_when_doc_not_found() -> None:
    from ragbot.application.commands import RechunkByDocumentIdCommand
    from ragbot.shared.errors import InvariantViolation

    uc = _make_uc(None)
    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        workspace_id="ws",
        document_id=uuid4(),
        trace_id="t",
    )
    with pytest.raises(InvariantViolation, match="document not found"):
        await uc.execute_by_document_id(cmd)


@pytest.mark.asyncio
async def test_execute_by_document_id_rejects_cross_bot_doc() -> None:
    """Defence in depth — even with tenant scope, prevent rechunking a
    doc that belongs to a sibling bot in the same tenant."""
    from ragbot.application.commands import RechunkByDocumentIdCommand
    from ragbot.shared.errors import InvariantViolation

    real_bot = uuid4()
    other_bot = uuid4()
    stub_doc = _StubDoc(
        id=uuid4(),
        record_bot_id=real_bot,
        source_url="https://x/y",
        document_name="x.csv",
        tool_name="x",
        mime_type="text/csv",
    )
    uc = _make_uc(stub_doc)
    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=uuid4(),
        record_bot_id=other_bot,  # mismatch
        workspace_id="ws",
        document_id=stub_doc.id,
        trace_id="t",
    )
    with pytest.raises(InvariantViolation, match="different bot"):
        await uc.execute_by_document_id(cmd)
