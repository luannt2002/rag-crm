"""request_logs plaintext verify columns are OPT-IN (Privacy 2.B preserved).

The repository must persist ``question_text`` / ``answer_text`` ONLY when
constructed with ``store_plaintext=True``. Default (False) keeps the hash-only
posture even when a caller passes the raw text — so enabling the verify flow is a
single deployment switch, never an accidental leak.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.infrastructure.repositories.request_log_repository import (
    RequestLogRepository,
)


class _CaptureSession:
    """Captures the RequestLogModel instance handed to ``session.add``."""

    def __init__(self) -> None:
        self.added: object | None = None

    def add(self, row: object) -> None:
        self.added = row

    async def commit(self) -> None:
        return None


def _factory(session: _CaptureSession):
    @asynccontextmanager
    async def _cm():
        yield session
    return _cm


async def _run_create(store_plaintext: bool) -> object:
    session = _CaptureSession()
    repo = RequestLogRepository(_factory(session), store_plaintext=store_plaintext)
    await repo.create_request_log(
        request_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        workspace_id="ws",
        connect_id="c1",
        question_hash="h" * 64,
        question_text="giá lốp 155/80R13 bao nhiêu",
        message_id=1,
    )
    return session.added


@pytest.mark.asyncio
async def test_plaintext_stored_when_enabled() -> None:
    row = await _run_create(store_plaintext=True)
    assert row.question_text == "giá lốp 155/80R13 bao nhiêu"


@pytest.mark.asyncio
async def test_plaintext_dropped_when_disabled() -> None:
    row = await _run_create(store_plaintext=False)
    assert row.question_text is None, (
        "default must NOT persist raw question — Privacy 2.B hash-only posture"
    )
    # hash is always stored regardless of the flag
    assert row.question_hash == "h" * 64
