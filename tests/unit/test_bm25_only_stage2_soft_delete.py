"""FALLBACK-SOFTDELETE: BM25-only stage-2 must exclude soft-deleted docs.

The canonical lexical retriever (``pg_bm25_retrieval``) gates its
``documents`` JOIN with ``AND d.deleted_at IS NULL``. The multistage
fallback stage 2 ran the same JOIN WITHOUT the gate, so chunks belonging
to soft-deleted documents leaked into the fallback result set. This test
pins the gate into the emitted SQL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from ragbot.infrastructure.retrieval_fallback.bm25_only_stage2 import (
    BM25OnlyStage2Retriever,
)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self):
        class _M:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _M(self._rows)


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.last_sql: str | None = None
        self.last_params: dict | None = None

    async def execute(self, statement, params=None):
        self.last_sql = str(statement)
        self.last_params = params
        return _FakeResult(self._rows)


def _make_session_factory(rows=None, sink: list | None = None):
    @asynccontextmanager
    async def _cm():
        s = _FakeSession(rows=rows)
        if sink is not None:
            sink.append(s)
        yield s

    def _factory():
        return _cm()

    return _factory


@pytest.mark.asyncio
async def test_stage2_sql_gates_soft_deleted_documents() -> None:
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    retriever = BM25OnlyStage2Retriever()
    await retriever.retrieve(
        query="Điều 8 quy định gì",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=10,
        session_factory=sf,
    )
    assert len(sink) == 1
    sql = sink[0].last_sql or ""
    assert "deleted_at IS NULL" in sql, (
        "stage-2 BM25 fallback SQL missing soft-delete gate; "
        "soft-deleted docs would leak into the fallback result"
    )
