"""End-to-end Postgres RLS enforcement (alembic 0069).

Migration 0069 enables Row-Level Security on every tenant-scoped table
plus child tables that inherit tenancy via FK (``document_chunks`` →
``documents``; ``knowledge_edges`` → ``bots``). Once enabled, the
existing ``SET LOCAL app.tenant_id`` plumbed through ``session_with_tenant``
becomes a hard cross-tenant boundary at the database layer — even a
raw-SQL path that forgets to add ``record_tenant_id = :tid`` to its
``WHERE`` clause is now invisible to the other tenant.

These tests exercise the policy itself, not the application filter:

1. With ``SET LOCAL app.tenant_id = '<tenant-A>'`` a ``SELECT`` on
   ``bots`` returns ONLY tenant A's row even though tenant B's row
   exists in the same transaction's snapshot.
2. The same session cannot DELETE a tenant-B-owned ``documents`` row
   — RLS hides it so the DELETE affects 0 rows.
3. A child-table policy (``document_chunks``) inherits parent tenancy:
   chunks belonging to tenant B's document are invisible while bound
   to tenant A.
4. A session with NO ``app.tenant_id`` GUC bound is fail-closed —
   ``current_setting('app.tenant_id', true)`` returns NULL, the policy
   excludes every row, and queries see an empty set.

The test fixtures seed a self-contained pair of tenants, one bot per
tenant, one document per bot, one chunk per document. Tear-down uses
``BYPASSRLS`` semantics inside an admin role; we take the pragmatic
shortcut of issuing a DELETE in a separate transaction with the GUC
set to each tenant in turn.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.db.engine import session_with_tenant


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RAGBOT_RLS_TEST_USER") in (None, ""),
        reason=(
            "PostgreSQL superuser BYPASSES RLS even with FORCE ROW LEVEL SECURITY. "
            "Default DATABASE_URL uses postgres (superuser) so policies are dead "
            "for testing AND production. Set RAGBOT_RLS_TEST_USER + matching DSN "
            "to a non-superuser role to enable."
        ),
    ),
]


# ── DB fixture ─────────────────────────────────────────────────────────────


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    dsn = _database_url()
    if not dsn:
        pytest.skip("DATABASE_URL env var required for integration tests")
    engine = create_async_engine(dsn, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────


async def _ensure_tenant_row(sf: Any, record_tenant_id: uuid.UUID) -> None:
    """Idempotent insert into ``tenants`` so the FK on ``bots`` is satisfied.

    We do this via a session with the same tenant GUC bound so RLS on
    ``tenants`` (if ever enabled later) would still admit the write.
    """
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": record_tenant_id, "name": f"rls-test-{str(record_tenant_id)[:8]}"},
        )
        await session.commit()


async def _seed_bot_doc_chunk(
    sf: Any,
    *,
    record_tenant_id: uuid.UUID,
    bot_slug: str,
    workspace_slug: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert ``bots`` → ``documents`` → ``document_chunks`` for one tenant."""
    record_bot_id = uuid.uuid4()
    record_document_id = uuid.uuid4()
    record_chunk_id = uuid.uuid4()
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        await session.execute(
            text(
                """
                INSERT INTO bots (
                    id, record_tenant_id, workspace_id, bot_id, channel_type,
                    bot_name, system_prompt, is_deleted,
                    created_at, updated_at, setting_options,
                    custom_vocabulary, max_documents, plan_limits,
                    bypass_token_limit, bypass_rate_limit, language
                )
                VALUES (
                    :id, :rt, :ws, :bot_id, 'web',
                    :name, '', false,
                    now(), now(), '{}'::jsonb,
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                """
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": workspace_slug,
                "bot_id": bot_slug,
                "name": f"rls-fixture-{bot_slug}",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, record_tenant_id, record_bot_id, workspace_id,
                    source_url, document_name, tool_name, mime_type,
                    content_hash, raw_content, content_chars, version, state,
                    metadata_json, language, acl, created_at, updated_at
                )
                VALUES (
                    :id, :rt, :rb, :ws,
                    :url, 'rls-fixture-doc', :tool, 'text/plain',
                    :h, 'rls fixture content', 19, 1, 'active',
                    '{}'::jsonb, 'vi', ARRAY[]::varchar[], now(), now()
                )
                """
            ),
            {
                "id": record_document_id,
                "rt": record_tenant_id,
                "rb": record_bot_id,
                "ws": workspace_slug,
                "url": f"rls-fixture://{record_document_id.hex}",
                "tool": f"rls-fixture-{record_document_id.hex[:8]}",
                "h": uuid.uuid4().hex,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO document_chunks (
                    id, record_document_id, chunk_index, content,
                    content_hash, metadata_json
                )
                VALUES (
                    :id, :doc, 0, 'rls fixture chunk',
                    :h, '{}'::jsonb
                )
                """
            ),
            {
                "id": record_chunk_id,
                "doc": record_document_id,
                "h": uuid.uuid4().hex,
            },
        )
        await session.commit()
    return record_bot_id, record_document_id, record_chunk_id


async def _hard_cleanup(
    sf: Any,
    record_tenant_id: uuid.UUID,
    record_bot_id: uuid.UUID,
    record_document_id: uuid.UUID,
) -> None:
    """Best-effort teardown bound to the SAME tenant so RLS admits it."""
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        await session.execute(
            text("DELETE FROM document_chunks WHERE record_document_id = :d"),
            {"d": record_document_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE id = :d"),
            {"d": record_document_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :b"),
            {"b": record_bot_id},
        )
        await session.commit()


@pytest.fixture()
async def two_tenant_setup(
    session_factory: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Seed two isolated tenants, yield IDs, tear down on exit."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _ensure_tenant_row(session_factory, tenant_a)
    await _ensure_tenant_row(session_factory, tenant_b)

    bot_a, doc_a, chunk_a = await _seed_bot_doc_chunk(
        session_factory,
        record_tenant_id=tenant_a,
        bot_slug=f"rls-bot-a-{tenant_a.hex[:8]}",
        workspace_slug=f"ws-a-{tenant_a.hex[:8]}",
    )
    bot_b, doc_b, chunk_b = await _seed_bot_doc_chunk(
        session_factory,
        record_tenant_id=tenant_b,
        bot_slug=f"rls-bot-b-{tenant_b.hex[:8]}",
        workspace_slug=f"ws-b-{tenant_b.hex[:8]}",
    )

    yield {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "bot_a": bot_a,
        "bot_b": bot_b,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "chunk_a": chunk_a,
        "chunk_b": chunk_b,
    }

    await _hard_cleanup(session_factory, tenant_a, bot_a, doc_a)
    await _hard_cleanup(session_factory, tenant_b, bot_b, doc_b)


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_bots_only_returns_bound_tenant_rows(
    session_factory: Any,
    two_tenant_setup: dict[str, Any],
) -> None:
    """A session bound to tenant A must NOT see tenant B's bot row.

    Sanity probe is a ``SELECT id FROM bots WHERE id IN (:a, :b)``: the
    application filter is bypassed so any returned row is RLS-admitted.
    """
    tenant_a = two_tenant_setup["tenant_a"]
    bot_a = two_tenant_setup["bot_a"]
    bot_b = two_tenant_setup["bot_b"]

    async with session_with_tenant(session_factory, record_tenant_id=tenant_a) as session:
        result = await session.execute(
            text("SELECT id FROM bots WHERE id IN (:a, :b)"),
            {"a": bot_a, "b": bot_b},
        )
        ids = {row[0] for row in result.fetchall()}

    assert bot_a in ids, "tenant A's session must see tenant A's bot"
    assert bot_b not in ids, (
        "RLS leak — tenant A's session returned tenant B's bot row"
    )
    assert ids == {bot_a}, (
        f"unexpected extra rows visible to tenant A: {ids - {bot_a}}"
    )


@pytest.mark.asyncio
async def test_delete_other_tenants_document_affects_zero_rows(
    session_factory: Any,
    two_tenant_setup: dict[str, Any],
) -> None:
    """A DELETE targeted at tenant B's document while bound to tenant A
    must affect 0 rows — the row is invisible under RLS."""
    tenant_a = two_tenant_setup["tenant_a"]
    tenant_b = two_tenant_setup["tenant_b"]
    doc_b = two_tenant_setup["doc_b"]

    async with session_with_tenant(session_factory, record_tenant_id=tenant_a) as session:
        result = await session.execute(
            text("DELETE FROM documents WHERE id = :d"),
            {"d": doc_b},
        )
        rowcount = result.rowcount
        await session.commit()

    assert rowcount == 0, (
        f"RLS leak — tenant A's DELETE on tenant B's doc affected {rowcount} rows"
    )

    # Sanity: tenant B's session can still see + delete its own row would
    # work; we only verify visibility here so the fixture teardown stays
    # responsible for cleanup.
    async with session_with_tenant(session_factory, record_tenant_id=tenant_b) as session:
        check = await session.execute(
            text("SELECT id FROM documents WHERE id = :d"),
            {"d": doc_b},
        )
        assert check.scalar_one_or_none() == doc_b, (
            "tenant B's own session lost visibility of its own document"
        )


@pytest.mark.asyncio
async def test_join_policy_hides_other_tenants_chunks(
    session_factory: Any,
    two_tenant_setup: dict[str, Any],
) -> None:
    """``document_chunks`` has no direct tenant column; the JOIN-based
    policy via ``documents`` must still hide the other tenant's chunks."""
    tenant_a = two_tenant_setup["tenant_a"]
    chunk_a = two_tenant_setup["chunk_a"]
    chunk_b = two_tenant_setup["chunk_b"]

    async with session_with_tenant(session_factory, record_tenant_id=tenant_a) as session:
        result = await session.execute(
            text("SELECT id FROM document_chunks WHERE id IN (:a, :b)"),
            {"a": chunk_a, "b": chunk_b},
        )
        ids = {row[0] for row in result.fetchall()}

    assert chunk_a in ids, "tenant A's session must see its own chunk"
    assert chunk_b not in ids, (
        "RLS leak — JOIN policy let tenant B's chunk through to tenant A"
    )


@pytest.mark.asyncio
async def test_unbound_session_sees_zero_rows(
    session_factory: Any,
    two_tenant_setup: dict[str, Any],
) -> None:
    """A raw session with NO ``app.tenant_id`` GUC set must fail-closed.

    ``current_setting('app.tenant_id', true)`` returns NULL when the GUC
    is unset; the policy compares ``record_tenant_id = NULL::uuid`` which
    yields NULL (treated as exclusion). Result: 0 rows visible.
    """
    bot_a = two_tenant_setup["bot_a"]
    bot_b = two_tenant_setup["bot_b"]

    # Plain session — NO session_with_tenant wrapper, NO SET LOCAL.
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT id FROM bots WHERE id IN (:a, :b)"),
            {"a": bot_a, "b": bot_b},
        )
        ids = {row[0] for row in result.fetchall()}

    assert ids == set(), (
        f"unbound session leaked rows under fail-closed RLS: {ids}"
    )
