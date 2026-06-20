"""Pin tests — canonical re-ingest lifecycle (2026-06-20 canary findings).

The canary delete→recreate test surfaced two defects on the ONE canonical
ingest path (``POST /documents/create`` → ``IngestDocumentUseCase``):

Bug #3a — ``uq_doc_tool`` collision. ``DELETE /documents`` archives the row
(``state=ARCHIVED``) but keeps it; the natural key ``(tenant, bot,
tool_name)`` survives. Re-create minted a *new* UUID and ``save()`` upserts
by PK → INSERT of a fresh row collided with the surviving archived row on
``uq_doc_tool`` → HTTP 500.

Bug #3b — stale source_url idempotency. The Redis ``IdempotencyService`` keyed
on ``hash(tenant, source_url, 24h)`` is registered at *enqueue* and never
reconciled with the job outcome. A deleted or failed doc's key short-circuited
re-ingest for 24h, silently returning a stale ``job_id`` with no real ingest.

Fix — reactivate by natural key: the create path looks up ``get_by_tool_name``;
a surviving row means "re-ingest THIS logical doc" → reuse its PK (so ``save()``
UPDATEs in place, no uq collision) AND skip the source_url fast-path dedup (a
surviving row is authoritative over the stale Redis key). The Redis key remains
only as the rapid-double-POST guard for a genuine first ingest (no row yet).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.commands.document_commands import IngestDocumentCommand
from ragbot.application.use_cases.ingest_document import IngestDocumentUseCase


@dataclass
class _StubUoW:
    add_outbox: AsyncMock = field(default_factory=lambda: AsyncMock())
    commit: AsyncMock = field(default_factory=lambda: AsyncMock())

    async def __aenter__(self) -> "_StubUoW":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _make_uc(
    *,
    existing_doc: Any | None,
    idem_duplicate: bool,
    prior_ref: str | None,
) -> tuple[IngestDocumentUseCase, MagicMock, MagicMock]:
    docs = MagicMock()
    docs.get_by_tool_name = AsyncMock(return_value=existing_doc)
    docs.save = AsyncMock(return_value=None)

    jobs = MagicMock()
    jobs.create = AsyncMock(return_value=None)

    idem = MagicMock()
    idem.is_duplicate = AsyncMock(return_value=idem_duplicate)
    idem.get_prior_result_ref = AsyncMock(return_value=prior_ref)
    idem.register = AsyncMock(return_value=None)

    clock = MagicMock()
    clock.now = MagicMock(return_value=datetime.now(tz=timezone.utc))

    uc = IngestDocumentUseCase(
        doc_repo=docs,
        bot_repo=MagicMock(),
        job_repo=jobs,
        uow_factory=lambda: _StubUoW(),
        idempotency=idem,
        clock=clock,
    )
    return uc, docs, jobs


def _cmd() -> IngestDocumentCommand:
    return IngestDocumentCommand(
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        workspace_id="ws",
        source_url="https://example.test/doc.docx",
        document_name="Thong Tu 09",
        mime_type=None,
        language="vi",
        trace_id="trace-reactivate",
    )


@pytest.mark.asyncio
async def test_reingest_existing_tool_name_reuses_pk() -> None:
    """#3a — when a row already exists for the tool_name (archived after a
    DELETE), the saved doc must REUSE that row's PK so ``save()`` UPDATEs in
    place instead of INSERTing a colliding ``uq_doc_tool`` row.
    """
    existing_id = uuid4()
    existing = SimpleNamespace(id=existing_id, state="ARCHIVED")
    uc, docs, jobs = _make_uc(
        existing_doc=existing, idem_duplicate=False, prior_ref=None,
    )

    result = await uc.execute(_cmd())

    assert result.status == "queued"
    docs.save.assert_awaited_once()
    saved_doc = docs.save.call_args.args[0]
    assert saved_doc.id == existing_id, (
        "re-ingest must reactivate the surviving row (reuse PK), not mint a "
        "new UUID that collides on uq_doc_tool"
    )
    jobs.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_reingest_skips_stale_idempotency_when_doc_survives() -> None:
    """#3b — a surviving row is authoritative over the 24h source_url Redis
    key: even when ``is_duplicate`` is True, re-ingest must proceed (enqueue a
    real job), not short-circuit to the stale prior ``job_id``.
    """
    existing = SimpleNamespace(id=uuid4(), state="ARCHIVED")
    uc, docs, jobs = _make_uc(
        existing_doc=existing,
        idem_duplicate=True,
        prior_ref=str(uuid4()),  # stale job from the prior (deleted) attempt
    )

    result = await uc.execute(_cmd())

    assert result.status == "queued"
    jobs.create.assert_awaited_once()
    docs.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_ingest_still_honors_idempotency_when_no_doc() -> None:
    """Regression guard — with NO surviving row, the rapid-double-POST dedup
    still short-circuits (returns the prior job, no second enqueue).
    """
    prior = str(uuid4())
    uc, docs, jobs = _make_uc(
        existing_doc=None, idem_duplicate=True, prior_ref=prior,
    )

    result = await uc.execute(_cmd())

    assert str(result.job_id) == prior
    jobs.create.assert_not_awaited()
    docs.save.assert_not_awaited()
