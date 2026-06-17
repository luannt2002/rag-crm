"""Regression test for mega-sprint-G1-pgvector — read-path RLS routing.

Asserts ``PgVectorStore.search`` / ``hybrid_search`` / ``count`` thread
``record_tenant_id`` and open the session via ``session_with_tenant`` so
the per-transaction ``SET LOCAL app.tenant_id = <tid>`` runs before any
SELECT executes.

Without this routing the read path uses a plain ``factory()`` session;
when the runtime DSN is the unprivileged ``ragbot_app`` role (RLS enforced)
the SELECT returns zero rows because ``app.tenant_id`` is unset and every
RLS policy on ``documents`` / ``document_chunks`` evaluates false.

Mirrors the existing ``test_pgvector_store_tenant_scoping.py`` pattern for
the write path. Domain-neutral — no brand / industry literals.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


def test_search_signature_accepts_record_tenant_id_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.search)
    assert "record_tenant_id" in sig.parameters, (
        "mega-sprint-G1 regression — search must accept record_tenant_id "
        "kwarg so the session opens with SET LOCAL app.tenant_id"
    )


def test_hybrid_search_signature_accepts_record_tenant_id_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.hybrid_search)
    assert "record_tenant_id" in sig.parameters, (
        "mega-sprint-G1 regression — hybrid_search must accept "
        "record_tenant_id kwarg"
    )


def test_count_signature_accepts_record_tenant_id_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.count)
    assert "record_tenant_id" in sig.parameters, (
        "mega-sprint-G1 regression — count must accept record_tenant_id"
    )


@pytest.mark.asyncio
async def test_search_without_tenant_binding_raises() -> None:
    """Calling without tenant_id_ctx + without record_tenant_id must raise.

    ``session_with_tenant`` raises ``RuntimeError`` when no tenant is bound;
    proves the read path can no longer silently bypass RLS by issuing the
    SELECT against a session that never ran ``SET LOCAL app.tenant_id``.
    """
    def _explode_factory() -> object:  # pragma: no cover — must not be called
        raise AssertionError(
            "factory called directly — search must route through "
            "session_with_tenant so SET LOCAL app.tenant_id runs first",
        )

    store = PgVectorStore(_explode_factory)
    with pytest.raises(RuntimeError, match="tenant_id_ctx"):
        await store.search(
            query_embedding=[0.0],
            record_bot_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_hybrid_search_without_tenant_binding_raises() -> None:
    def _explode_factory() -> object:  # pragma: no cover
        raise AssertionError(
            "factory called directly — hybrid_search must route through "
            "session_with_tenant",
        )

    store = PgVectorStore(_explode_factory)
    with pytest.raises(RuntimeError, match="tenant_id_ctx"):
        await store.hybrid_search(
            query_text="anything",
            query_embedding=[0.0],
            record_bot_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_count_without_tenant_binding_raises() -> None:
    def _explode_factory() -> object:  # pragma: no cover
        raise AssertionError(
            "factory called directly — count must route through "
            "session_with_tenant",
        )

    store = PgVectorStore(_explode_factory)
    with pytest.raises(RuntimeError, match="tenant_id_ctx"):
        await store.count(uuid.uuid4())
