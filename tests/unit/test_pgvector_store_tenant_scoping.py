"""Lock test — F14-CRIT-1 closes RLS bypass on PgVectorStore mutators.

Asserts that ``upsert_chunks`` + ``delete_by_document`` route through
``session_with_tenant`` (raising RuntimeError without a bound tenant) so a
future caller cannot accidentally bypass tenant isolation.

Domain-neutral: no brand / industry literals. Uses placeholder UUIDs.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


def _make_factory_returning(session: AsyncMock) -> MagicMock:
    """Return a session_factory mock that yields ``session`` from the ctx."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def test_upsert_chunks_signature_accepts_record_tenant_id_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.upsert_chunks)
    assert "record_tenant_id" in sig.parameters, (
        "F14-CRIT-1 regression — upsert_chunks must accept record_tenant_id kwarg"
    )


def test_delete_by_document_signature_accepts_record_tenant_id_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.delete_by_document)
    assert "record_tenant_id" in sig.parameters, (
        "F14-CRIT-1 regression — delete_by_document must accept record_tenant_id"
    )


@pytest.mark.asyncio
async def test_upsert_chunks_without_tenant_binding_raises() -> None:
    """Calling without tenant_id_ctx + without record_tenant_id must raise.

    session_with_tenant raises RuntimeError when no tenant is bound — proves the
    write path can no longer silently bypass RLS.
    """
    factory = MagicMock()
    store = PgVectorStore(factory)

    # No record_tenant_id passed; no contextvar bound.
    with pytest.raises(RuntimeError, match="tenant_id_ctx"):
        await store.upsert_chunks(
            record_document_id=uuid.uuid4(),
            chunks=[{"content": "x", "embedding": [0.0]}],
        )


@pytest.mark.asyncio
async def test_delete_by_document_without_tenant_binding_raises() -> None:
    factory = MagicMock()
    store = PgVectorStore(factory)
    with pytest.raises(RuntimeError, match="tenant_id_ctx"):
        await store.delete_by_document(uuid.uuid4())


@pytest.mark.asyncio
async def test_upsert_chunks_empty_input_short_circuits_without_session() -> None:
    """Empty chunks list returns 0 without opening a session — preserves existing fast path."""
    factory = MagicMock()
    store = PgVectorStore(factory)
    # Empty chunks: no session opened, no tenant required.
    result = await store.upsert_chunks(
        record_document_id=uuid.uuid4(),
        chunks=[],
    )
    assert result == 0
    factory.assert_not_called()
