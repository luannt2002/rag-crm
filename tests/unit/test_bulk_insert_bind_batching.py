"""I12: _bulk_insert_chunks must batch below the Postgres int16 bind ceiling.

A single VALUES(...) INSERT binds ~12-13 params/row; a >~2900-chunk document
would overflow 32767 and abort the whole ingest. The helper batches so a large
document splits into several round trips instead.
"""
from __future__ import annotations

import uuid

import pytest

from ragbot.application.services.document_service.ingest_helpers import (
    _bulk_insert_chunks,
)
from ragbot.shared.constants import POSTGRES_MAX_BIND_PARAMS


class _CapturingSession:
    """Records every execute() call's compiled SQL + bind params."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):  # noqa: ANN001
        # ``stmt`` is a sqlalchemy TextClause; str() yields the SQL body.
        self.calls.append((str(stmt), dict(params or {})))
        return None


def _make_rows(n: int) -> list[dict]:
    return [
        {
            "id": uuid.uuid4(),
            "doc_id": uuid.uuid4(),
            "idx": i,
            "content": f"chunk {i}",
            "content_segmented": None,
            "hash": f"h{i}",
            "emb": None,
            "meta": "{}",
            "chunk_chars": 7,
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_small_document_single_batch() -> None:
    session = _CapturingSession()
    rows = _make_rows(50)
    await _bulk_insert_chunks(
        session, rows, record_bot_id=uuid.uuid4(),  # type: ignore[arg-type]
    )
    assert len(session.calls) == 1
    # One INSERT, 50 value tuples.
    assert session.calls[0][0].count("(:id_") == 50


@pytest.mark.asyncio
async def test_large_document_splits_into_multiple_batches() -> None:
    # 6000 rows × ~12 binds/row = ~72k binds → must split (>32767 ceiling).
    session = _CapturingSession()
    rows = _make_rows(6000)
    await _bulk_insert_chunks(
        session, rows, record_bot_id=uuid.uuid4(),  # type: ignore[arg-type]
    )
    assert len(session.calls) >= 2, "6000 chunks must not fit one statement"

    total_tuples = 0
    for sql, params in session.calls:
        # No single statement may exceed the protocol ceiling.
        assert len(params) <= POSTGRES_MAX_BIND_PARAMS, (
            f"batch bound {len(params)} params > {POSTGRES_MAX_BIND_PARAMS}"
        )
        total_tuples += sql.count("(:id_")
    # Every row is still inserted exactly once across the batches.
    assert total_tuples == 6000


@pytest.mark.asyncio
async def test_batching_with_parent_chunk_id() -> None:
    session = _CapturingSession()
    rows = _make_rows(6000)
    for r in rows:
        r["parent_chunk_id"] = uuid.uuid4()
    await _bulk_insert_chunks(
        session, rows, record_bot_id=uuid.uuid4(),  # type: ignore[arg-type]
        has_parent_chunk_id=True,
    )
    assert len(session.calls) >= 2
    total = 0
    for sql, params in session.calls:
        assert len(params) <= POSTGRES_MAX_BIND_PARAMS
        total += sql.count("(:id_")
    assert total == 6000
