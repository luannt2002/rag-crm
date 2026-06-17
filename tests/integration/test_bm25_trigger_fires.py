"""BM25 search_vector trigger fires on INSERT/UPDATE.

Pre-fix audit 2026-04-29 found ``search_vector = NULL`` on 24/24 chunks
for the demo bot. Trigger ``trg_chunk_search_vector`` (migrations 0028 +
0046) is correct — the NULLs were from rows inserted before the trigger
landed. Migration 0048 backfills them. These tests guard the trigger
contract so a future migration cannot silently drop it.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    raise RuntimeError("DATABASE_URL not set")


@pytest.fixture(scope="module")
def database_url() -> str:
    return _database_url()


@pytest.fixture()
async def session_factory(database_url: str):
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _ensure_tenant_row(sf: Any, record_tenant_id: uuid.UUID) -> None:
    """Idempotent insert into ``tenants`` so the FK on ``bots`` is satisfied."""
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": record_tenant_id, "name": f"test-{str(record_tenant_id)[:8]}"},
        )
        await session.commit()


async def _seed_doc(sf: Any) -> uuid.UUID:
    """Insert minimal documents row so chunks FK passes."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000098801")
    await _ensure_tenant_row(sf, record_tenant_id)
    async with sf() as session:
        # Bot row (4-key required).
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
                    :id, :rt, :ws, :slug, 'web',
                    'bm25 trigger bot', '', false,
                    now(), now(), '{}'::jsonb,
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                """,
            ),
            {
                "id": bot_id,
                "rt": record_tenant_id,
                "ws": f"ws-{bot_id.hex[:8]}",
                "slug": f"bm25-{bot_id.hex[:12]}",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO documents (id, record_bot_id, source_url, document_name,
                    tool_name, mime_type, language, state, version, content_hash,
                    acl, metadata_json, content_chars, raw_content)
                VALUES (:id, :bid, 'http://x', 'bm25-doc', 'bm25_doc', 'text/plain',
                    'vi', 'active', 1, :h, ARRAY[]::varchar[], '{}'::jsonb, 0, 'placeholder')
                """,
            ),
            {"id": doc_id, "bid": bot_id, "h": uuid.uuid4().hex},
        )
        await session.commit()
    return doc_id, bot_id


async def _cleanup(sf, bot_id):
    async with sf() as session:
        await session.execute(
            text("DELETE FROM document_chunks WHERE record_document_id IN "
                 "(SELECT id FROM documents WHERE record_bot_id = :bid)"),
            {"bid": bot_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE record_bot_id = :bid"),
            {"bid": bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :bid"),
            {"bid": bot_id},
        )
        await session.commit()


async def test_search_vector_populated_after_insert(session_factory):
    """INSERT → trigger fires → search_vector NOT NULL."""
    doc_id, bot_id = await _seed_doc(session_factory)
    try:
        async with session_factory() as session:
            chunk_id = uuid.uuid4()
            await session.execute(
                text("""
                    INSERT INTO document_chunks (id, record_document_id, chunk_index,
                        content, content_hash, embedding, metadata_json, chunk_chars)
                    VALUES (:id, :did, 0, :content, :h, NULL, '{}'::jsonb, :n)
                """),
                {
                    "id": chunk_id,
                    "did": doc_id,
                    "content": "trigger fires fixture content alpha bravo charlie",
                    "h": uuid.uuid4().hex,
                    "n": 50,
                },
            )
            await session.commit()
            row = (
                await session.execute(
                    text(
                        "SELECT search_vector::text FROM document_chunks WHERE id = :id"
                    ),
                    {"id": chunk_id},
                )
            ).scalar_one()
        assert row is not None and row != ""
        assert "alpha" in row or "'alpha'" in row
    finally:
        await _cleanup(session_factory, bot_id)


async def test_search_vector_updated_after_content_change(session_factory):
    """UPDATE OF content → trigger fires → search_vector reflects new content."""
    doc_id, bot_id = await _seed_doc(session_factory)
    try:
        async with session_factory() as session:
            chunk_id = uuid.uuid4()
            await session.execute(
                text("""
                    INSERT INTO document_chunks (id, record_document_id, chunk_index,
                        content, content_hash, embedding, metadata_json, chunk_chars)
                    VALUES (:id, :did, 0, :content, :h, NULL, '{}'::jsonb, :n)
                """),
                {
                    "id": chunk_id,
                    "did": doc_id,
                    "content": "first version oldword",
                    "h": uuid.uuid4().hex,
                    "n": 20,
                },
            )
            await session.execute(
                text(
                    "UPDATE document_chunks SET content = :c WHERE id = :id"
                ),
                {"id": chunk_id, "c": "second version newword"},
            )
            await session.commit()
            row = (
                await session.execute(
                    text(
                        "SELECT search_vector::text FROM document_chunks WHERE id = :id"
                    ),
                    {"id": chunk_id},
                )
            ).scalar_one()
        assert "newword" in row or "'newword'" in row
        assert "oldword" not in row
    finally:
        await _cleanup(session_factory, bot_id)


async def test_bm25_query_returns_chunks(session_factory):
    """search_vector @@ to_tsquery returns the chunk by content keyword."""
    doc_id, bot_id = await _seed_doc(session_factory)
    try:
        async with session_factory() as session:
            chunk_id = uuid.uuid4()
            await session.execute(
                text("""
                    INSERT INTO document_chunks (id, record_document_id, chunk_index,
                        content, content_hash, embedding, metadata_json, chunk_chars)
                    VALUES (:id, :did, 0, :content, :h, NULL, '{}'::jsonb, :n)
                """),
                {
                    "id": chunk_id,
                    "did": doc_id,
                    "content": "bm25unique_token_xyz appears in this text",
                    "h": uuid.uuid4().hex,
                    "n": 50,
                },
            )
            await session.commit()
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM document_chunks "
                        "WHERE record_document_id = :did "
                        "AND search_vector @@ to_tsquery('simple', 'bm25unique_token_xyz')"
                    ),
                    {"did": doc_id},
                )
            ).scalar_one()
        assert count == 1
    finally:
        await _cleanup(session_factory, bot_id)
