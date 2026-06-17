"""RLS tenant scope enforcement integration tests.

Hidden bug scan flagged two cross-tenant leaks:

1. ``tenant_id_ctx`` defaulted to ``""`` — workers that forgot to call
   ``bind_request_context()`` opened DB sessions WITHOUT
   ``SET LOCAL app.tenant_id``, so writes bypassed Row-Level-Security.
2. ``semantic_cache`` lookups used ``OR record_tenant_id IS NULL`` —
   any legacy NULL row leaked across all tenants.

These integration tests assert the fixes hold end-to-end:

* The new ``UNSET`` sentinel surfaces a ``RuntimeError`` when DB access
  happens before ``bind_request_context()``.
* ``DocumentService`` opens sessions through ``session_with_tenant`` so
  RLS is always applied.
* ``semantic_cache`` hits are strictly scoped to the calling tenant.

The semantic_cache test uses an isolated test bot row + two tenant UUIDs
and tears down what it inserts. It does NOT depend on any pre-existing
data shape so it's safe to run repeatedly.
"""
from __future__ import annotations

import inspect
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.services.document_service import DocumentService
from ragbot.config.logging import tenant_id_ctx
from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("DATABASE_URL=") and "DATABASE_URL_SYNC" not in line:
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not set and .env not found")


@pytest.fixture(scope="module")
def database_url() -> str:
    return _database_url()


@pytest.fixture()
async def session_factory(database_url: str) -> Any:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


# ── Bug 1: tenant_id_ctx UNSET sentinel + worker bind contract ─────────────


async def test_unset_tenant_ctx_raises_loud(session_factory) -> None:
    """A bare UoW or session_with_tenant call without ``bind_request_context``
    must raise ``RuntimeError`` — the previous silent skip allowed worker
    writes to bypass RLS (cross-tenant write leak)."""
    from ragbot.infrastructure.db.engine import session_with_tenant
    from ragbot.infrastructure.db.uow import SqlAlchemyUnitOfWork

    # Force ContextVar to the loud sentinel for this test.
    token = tenant_id_ctx.set("UNSET")
    try:
        with pytest.raises(RuntimeError, match="tenant_id_ctx not bound"):
            async with session_with_tenant(session_factory):
                pass  # pragma: no cover

        with pytest.raises(RuntimeError, match="tenant_id_ctx not bound"):
            async with SqlAlchemyUnitOfWork(session_factory):
                pass  # pragma: no cover
    finally:
        tenant_id_ctx.reset(token)


async def test_bind_request_context_required_before_db_access(
    session_factory,
) -> None:
    """Once ``bind_request_context`` is called, the same code paths succeed."""
    from ragbot.config.logging import bind_request_context, clear_request_context
    from ragbot.infrastructure.db.engine import session_with_tenant

    tenant = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    bind_request_context(tenant_id=tenant)
    try:
        async with session_with_tenant(session_factory) as session:
            row = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            assert row.scalar_one() == str(tenant)
    finally:
        clear_request_context()
        # belt-and-braces — clear_request_context only touches structlog
        # contextvars, the ragbot ones are reset to UNSET explicitly.
        tenant_id_ctx.set("UNSET")


async def test_worker_binds_before_handle_event() -> None:
    """document_worker + chat_worker MUST call ``bind_request_context`` at
    the very top of their handle function, BEFORE any DB work. We grep
    the source as a contract test (cheap + reliable)."""
    from ragbot.interfaces.workers import chat_worker, document_worker

    chat_src = inspect.getsource(chat_worker.handle_chat_received)
    doc_src = inspect.getsource(document_worker.handle_document_uploaded)

    # bind_request_context is called inside both worker entry points.
    assert "bind_request_context(" in chat_src, (
        "chat_worker.handle_chat_received must call bind_request_context"
    )
    assert "bind_request_context(" in doc_src, (
        "document_worker.handle_document_uploaded must call bind_request_context"
    )


async def test_document_service_uses_session_with_tenant() -> None:
    """DocumentService must open sessions through ``session_with_tenant``,
    NOT raw ``self._sf()`` — the latter skips ``SET LOCAL app.tenant_id``
    and lets writes bypass RLS."""
    src = inspect.getsource(DocumentService)
    # Bare ``async with self._sf()`` is the anti-pattern we removed.
    assert "async with self._sf()" not in src, (
        "DocumentService still has a bare ``async with self._sf()`` call — "
        "use ``session_with_tenant`` so SET LOCAL app.tenant_id is applied."
    )
    # And the safe helper is used at multiple sites.
    assert src.count("session_with_tenant(") >= 5, (
        "DocumentService should use session_with_tenant at multiple sites"
    )


# ── Bug 2: semantic_cache strict tenant scope (no OR-NULL leak) ────────────


async def test_semantic_cache_no_or_null_leak() -> None:
    """The semantic_cache lookup SQL must NOT contain
    ``record_tenant_id IS NULL`` — that clause used to leak legacy NULL
    rows across tenants. Fail-static so the regression can't sneak back
    through a copy-paste in a future PR."""
    src = inspect.getsource(PgSemanticCache)
    # Strip Python comment lines first — `record_tenant_id IS NULL` legitimately
    # appears in a #-comment that EXPLAINS the closed bug. The grep must match
    # only EXECUTABLE SQL inside text() / triple-quoted strings.
    code_only = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "record_tenant_id IS NULL" not in code_only, (
        "semantic_cache still has the OR-NULL clause in executable SQL — "
        "that's a cross-tenant leak. Strict tenant scoping required."
    )
    # Spot-check that we still filter on tenant_id positively.
    assert "AND record_tenant_id = :record_tenant_id" in src, (
        "semantic_cache lost its strict tenant filter"
    )


async def test_semantic_cache_query_returns_none_for_other_tenant(
    session_factory,
) -> None:
    """End-to-end: insert a row tagged tenant A, query with tenant B,
    expect no hit. With the old OR-NULL clause a NULL row would have
    matched both — we no longer insert NULL rows so this also covers
    the cleanup behaviour."""
    from ragbot.application.ports.cache_port import CachedResponse

    bot_id = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeee0001")
    tenant_a = uuid.UUID("eeeeeeee-eeee-eeee-eeee-aaaaaaaaaaaa")
    tenant_b = uuid.UUID("eeeeeeee-eeee-eeee-eeee-bbbbbbbbbbbb")

    cache = PgSemanticCache(session_factory=session_factory)

    # 1536-dim placeholder embedding — semantic_cache schema demands it.
    emb = [0.001 * (i + 1) for i in range(1536)]
    response = CachedResponse(
        answer="tenant A answer",
        citations=[],
        model_name="stub",
        cached_at_ts=0,
    )

    try:
        await cache.store(
            query="hidden bug scan probe T1",
            query_embedding=emb,
            response=response,
            record_tenant_id=tenant_a,
            record_bot_id=bot_id,
            workspace_id="ws-rls-test",
            bot_version="latest",
            corpus_version="latest",
            ttl_s=60,
        )

        # Same bot, DIFFERENT tenant — must miss.
        miss = await cache.find_similar_with_text(
            query_embedding=emb,
            query_text="hidden bug scan probe T1",
            record_tenant_id=tenant_b,
            record_bot_id=bot_id,
            bot_version="latest",
            corpus_version="latest",
        )
        assert miss is None, (
            "semantic_cache leaked tenant A row to tenant B query"
        )

        # Sanity: same tenant, same query — must hit.
        hit = await cache.find_similar_with_text(
            query_embedding=emb,
            query_text="hidden bug scan probe T1",
            record_tenant_id=tenant_a,
            record_bot_id=bot_id,
            bot_version="latest",
            corpus_version="latest",
        )
        assert hit is not None
        assert hit.answer == "tenant A answer"
    finally:
        # Clean up the row we inserted.
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
                {"bid": bot_id},
            )
            await session.commit()
