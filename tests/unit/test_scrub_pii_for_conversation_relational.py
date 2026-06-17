"""[T2-CostPerf] G15 / alembic 0109 — ``scrub_pii_for_conversation``
behavior after the JSONB column drop.

Pre-fix the method ran ``UPDATE request_logs SET retrieved_chunks=[]``
keyed by (tenant, conversation) — it nullified the PII chunk previews
inlined into the JSONB blob.

Post-G15 the JSONB column is gone. The replacement
``request_chunk_refs`` table carries ONLY (request_id, chunk_id, rank,
score) — no chunk preview text, no document_name. There is no PII left
on the request-side audit trail to scrub.

The method's external contract is preserved: it still returns a count
that the GDPR admin route writes into a forensic audit row. The count
now reflects the number of request_log rows in the conversation (the
upstream callers don't care about the precise denominator — they only
gate the audit emit on ``count > 0``).

These tests use a hand-rolled fake session — no DB — so they run as
pure unit tests in the same pass as the rest of the repository suite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.infrastructure.repositories.request_log_repository import (
    RequestLogRepository,
)


_TENANT = UUID("00000000-0000-0000-0000-000000000099")


class _FakeResult:
    """Mimic the bits of SQLAlchemy ``Result`` the repo touches."""

    def __init__(self, scalar: int) -> None:
        self._scalar = scalar

    def scalar_one(self) -> int:
        return self._scalar


class _FakeSession:
    """Captures ``execute`` calls so the test can inspect what SQL ran."""

    def __init__(self, count: int) -> None:
        self._count = count
        self.executed: list[Any] = []
        self.committed = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed.append(stmt)
        return _FakeResult(self._count)

    async def commit(self) -> None:  # pragma: no cover — current impl is read-only
        self.committed = True


def _factory(session: _FakeSession) -> Any:
    """Mimic ``async_sessionmaker`` — calling it yields the session."""
    sf = MagicMock()
    sf.return_value = session
    return sf


# ---------------------------------------------------------------------------
# 1. Conversation has 3 request_logs → repo runs SELECT COUNT and
#    returns 3. Caller (admin_gdpr.gdpr_erase_message) emits one
#    forensic audit row when count > 0.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scrub_returns_count_of_request_logs_in_conversation() -> None:
    conv = uuid4()
    session = _FakeSession(count=3)
    repo = RequestLogRepository(session_factory=_factory(session))

    n = await repo.scrub_pii_for_conversation(conv, record_tenant_id=_TENANT)

    assert n == 3
    # Single SELECT round-trip — JSONB UPDATE no longer needed.
    assert len(session.executed) == 1


# ---------------------------------------------------------------------------
# 2. Conversation has no request_logs → repo returns 0; caller MUST
#    suppress the forensic audit row in that case (count == 0 contract).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scrub_returns_zero_when_no_matching_request_logs() -> None:
    conv = uuid4()
    session = _FakeSession(count=0)
    repo = RequestLogRepository(session_factory=_factory(session))

    n = await repo.scrub_pii_for_conversation(conv, record_tenant_id=_TENANT)

    assert n == 0
    assert len(session.executed) == 1


# ---------------------------------------------------------------------------
# 3. Tenant isolation — missing record_tenant_id raises immediately,
#    matching the legacy contract enforced by ``_ensure``.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scrub_rejects_missing_tenant() -> None:
    from ragbot.shared.errors import TenantIsolationViolation
    repo = RequestLogRepository(session_factory=AsyncMock())
    with pytest.raises(TenantIsolationViolation):
        await repo.scrub_pii_for_conversation(uuid4(), record_tenant_id=None)
