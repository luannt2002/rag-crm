"""F-2: ``update_status`` with an unresolved tenant (record_tenant_id=None)
runs an UNSCOPED UPDATE (job_id is globally unique, so this is allowed on the
fail-before-lookup / system-error path) — but the bypass MUST be observable.

This pins that a structured ``job_update_unscoped`` warning is emitted only on
the None-tenant path, never on a normal tenant-scoped update.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from ragbot.infrastructure.repositories.job_repository import (
    SqlAlchemyJobRepository,
)


def _session_factory() -> MagicMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    sf = MagicMock()
    sf.return_value = session
    return sf


@pytest.mark.asyncio
async def test_unscoped_update_emits_warning() -> None:
    """None tenant → exactly one ``job_update_unscoped`` warning, with job_id."""
    repo = SqlAlchemyJobRepository(_session_factory())
    job_id = uuid.uuid4()

    with capture_logs() as logs:
        await repo.update_status(job_id, record_tenant_id=None, status="failed")

    warns = [e for e in logs if e.get("event") == "job_update_unscoped"]
    assert len(warns) == 1, "unscoped tenant update must emit one warning"
    assert warns[0]["job_id"] == str(job_id)
    assert warns[0]["status"] == "failed"
    assert warns[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_scoped_update_emits_no_warning() -> None:
    """A tenant-scoped update must NOT emit the unscoped warning."""
    repo = SqlAlchemyJobRepository(_session_factory())

    with capture_logs() as logs:
        await repo.update_status(
            uuid.uuid4(), record_tenant_id=uuid.uuid4(), status="success",
        )

    warns = [e for e in logs if e.get("event") == "job_update_unscoped"]
    assert not warns, "scoped update must not warn about an unscoped bypass"
