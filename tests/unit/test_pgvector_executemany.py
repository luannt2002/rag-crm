"""``PgVectorStore.add_chunks`` should issue one ``execute()`` per call,
not one per chunk. The historical loop submitted N round-trips for an
N-chunk insert; SQLAlchemy's executemany behaviour collapses the same
work into a single statement preparation + bulk parameter bind.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeSession:
    def __init__(self) -> None:
        self.execute_calls: list[tuple] = []

    async def execute(self, stmt, params=None):  # noqa: ANN001
        self.execute_calls.append((str(stmt), params))
        result = MagicMock()
        result.fetchone = lambda: None
        return result

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_add_chunks_issues_single_execute_for_bulk_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    """100 chunks must produce ≤ 2 execute() calls (DELETE pre-clean + INSERT
    bulk), not 1 + N=101."""
    from uuid import uuid4

    from ragbot.infrastructure.vector import pgvector_store as mod

    fake_session = _FakeSession()

    class _SF:
        def __call__(self):  # session factory shape
            return fake_session

    # Stub session_with_tenant to yield our fake session.
    async def _fake_swt(_sf, *, record_tenant_id=None):  # noqa: ANN001
        return fake_session

    class _SwtCtx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *exc):
            return None

    def _swt_factory(_sf, *, record_tenant_id=None):  # noqa: ANN001
        return _SwtCtx()

    monkeypatch.setattr(mod, "session_with_tenant", _swt_factory)

    store = mod.PgVectorStore(_SF())
    chunks = [
        {
            "chunk_index": i,
            "content": f"chunk {i}",
            "content_hash": f"h{i:03d}",
            "embedding": [0.1] * 8,
            "metadata": {"i": i},
        }
        for i in range(100)
    ]
    await store.upsert_chunks(record_document_id=uuid4(), chunks=chunks, embedding_column="embedding")

    insert_calls = [c for c in fake_session.execute_calls if "INSERT INTO document_chunks" in c[0]]
    assert len(insert_calls) == 1, (
        f"expected 1 bulk INSERT execute() for 100-chunk add_chunks; "
        f"got {len(insert_calls)} (round-trip storm)"
    )
    # Bulk param shape: SQLAlchemy executemany receives list of dicts.
    bulk_params = insert_calls[0][1]
    assert isinstance(bulk_params, list), f"bulk params must be list, got {type(bulk_params)}"
    assert len(bulk_params) == 100
