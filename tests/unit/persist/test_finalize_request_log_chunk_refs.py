"""G15 — finalize_request_log writes refs to ``request_chunk_refs`` table.

Pre-G15: ``finalize_request_log`` set ``row.retrieved_chunks = [...]`` on
the request_logs JSONB column.

Post-G15: the column is dropped (alembic 0109) and the repository
calls ``self._build_chunk_refs(...)`` then ``session.add_all(...)`` to
insert one ``RequestChunkRefModel`` row per ref.

This test pins that contract by spying on the AsyncSession the repository
uses -- we don't need a live DB to verify the write shape.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.infrastructure.db.models_monitoring import RequestChunkRefModel
from ragbot.infrastructure.repositories.request_log_repository import (
    RequestLogRepository,
)


def _make_session_factory(existing_log_row: Any) -> tuple[Any, MagicMock]:
    """Build a session factory whose ``async with`` yields a spy session.

    Returns ``(factory, session)`` so the test can assert against
    ``session.add_all.call_args`` after the repository runs.
    """
    session = MagicMock()
    session.commit = AsyncMock(return_value=None)
    session.get = AsyncMock(return_value=existing_log_row)
    # execute() is awaited by the durable monitoring_log dual-write (alembic 0217).
    session.execute = AsyncMock(return_value=None)
    # add_all + add are sync on AsyncSession (see SQLAlchemy contract).
    session.add_all = MagicMock()
    session.add = MagicMock()

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory, session


@pytest.mark.asyncio
async def test_finalize_request_log_writes_chunk_refs_via_add_all() -> None:
    tenant_id = uuid4()
    request_id = uuid4()
    cid_1 = uuid4()
    cid_2 = uuid4()

    # ``existing_log_row`` mimics the row session.get() would return --
    # finalize mutates it in place. We only need the fields finalize touches.
    log_row = MagicMock()
    log_row.record_tenant_id = tenant_id
    log_row.started_at = datetime.now(tz=timezone.utc)
    log_row.metadata_json = {}

    factory, session = _make_session_factory(log_row)
    repo = RequestLogRepository(session_factory=factory)

    await repo.finalize_request_log(
        request_id,
        record_tenant_id=tenant_id,
        prompt_tokens=10,
        completion_tokens=20,
        cost_usd=0.001,
        retrieved_chunks=[
            {"chunk_id": str(cid_1), "rank": 0, "score": 0.9},
            {"chunk_id": str(cid_2), "rank": 1, "score": 0.8},
        ],
    )

    # The new path: session.add_all(...) called with two RequestChunkRef rows.
    assert session.add_all.call_count == 1
    refs = session.add_all.call_args.args[0]
    assert len(refs) == 2
    assert all(isinstance(r, RequestChunkRefModel) for r in refs)
    assert {r.record_chunk_id for r in refs} == {cid_1, cid_2}
    # All refs bind back to this request_id (FK CASCADE target).
    assert all(r.record_request_id == request_id for r in refs)


@pytest.mark.asyncio
async def test_finalize_request_log_no_chunk_refs_for_empty_input() -> None:
    """Empty list still calls add_all([]) -- no INSERT issued downstream."""
    tenant_id = uuid4()
    request_id = uuid4()

    log_row = MagicMock()
    log_row.record_tenant_id = tenant_id
    log_row.started_at = datetime.now(tz=timezone.utc)
    log_row.metadata_json = {}

    factory, session = _make_session_factory(log_row)
    repo = RequestLogRepository(session_factory=factory)

    await repo.finalize_request_log(
        request_id,
        record_tenant_id=tenant_id,
        retrieved_chunks=[],
    )

    assert session.add_all.call_count == 1
    assert session.add_all.call_args.args[0] == []


@pytest.mark.asyncio
async def test_finalize_request_log_no_longer_writes_jsonb_column() -> None:
    """Defensive: finalize MUST NOT touch ``row.retrieved_chunks`` anymore.

    The column was dropped in alembic 0109; assigning to it would either
    raise ORM error at flush or silently set a transient attribute -- both
    are bugs masked by the test mock. We pin the absence of the assignment
    by spying on the row's setattr.
    """
    tenant_id = uuid4()
    request_id = uuid4()

    log_row = MagicMock()
    log_row.record_tenant_id = tenant_id
    log_row.started_at = datetime.now(tz=timezone.utc)
    log_row.metadata_json = {}

    factory, session = _make_session_factory(log_row)
    repo = RequestLogRepository(session_factory=factory)

    await repo.finalize_request_log(
        request_id,
        record_tenant_id=tenant_id,
        retrieved_chunks=[{"chunk_id": str(uuid4()), "rank": 0, "score": 0.5}],
    )

    # Detect a stray ``row.retrieved_chunks = ...`` assignment by
    # inspecting MagicMock.method_calls (setattrs land here).
    assigned_attrs = {
        c[0].lstrip(".") if isinstance(c, tuple) else str(c)
        for c in log_row.method_calls
    }
    assert "retrieved_chunks" not in assigned_attrs
    # And a defensive check via mock_calls representation.
    mock_repr = str(log_row.mock_calls)
    assert "retrieved_chunks" not in mock_repr, (
        f"finalize_request_log must NOT assign row.retrieved_chunks "
        f"(JSONB column dropped in alembic 0109 / G15). Got: {mock_repr}"
    )


@pytest.mark.asyncio
async def test_finalize_request_log_raises_on_cross_tenant_lookup() -> None:
    """Tenant isolation contract: caller's tenant must own the row."""
    log_row = MagicMock()
    log_row.record_tenant_id = uuid4()  # different tenant
    log_row.started_at = datetime.now(tz=timezone.utc)
    log_row.metadata_json = {}

    factory, _session = _make_session_factory(log_row)
    repo = RequestLogRepository(session_factory=factory)

    from ragbot.shared.errors import TenantIsolationViolation
    with pytest.raises(TenantIsolationViolation):
        await repo.finalize_request_log(
            UUID("00000000-0000-0000-0000-0000000000aa"),
            record_tenant_id=uuid4(),  # not log_row.record_tenant_id
            retrieved_chunks=[],
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
