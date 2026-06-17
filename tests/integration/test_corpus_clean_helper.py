"""Corpus-clean helper script — DB-backed integration tests.

Each test seeds a fresh bot + 5 chunks (2 duplicate-by-hash, 2
price-conflict, 1 unique with NULL embedding) under a shared test
tenant, runs one subcommand handler against the real Postgres, asserts
on the JSON shape, then tears down.

Pure-logic tests (excerpt / regex / scoring / output / parser) live in
``tests/unit/test_corpus_clean_helper_logic.py`` and run on every
``pytest`` invocation. The tests in this file are auto-marked
``integration`` by ``tests/conftest.py`` and skipped without
``--run-integration``.
"""

from __future__ import annotations

import io
import json
import os
import uuid
from contextlib import redirect_stdout
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.corpus_clean import (
    cmd_find_conflict_prices,
    cmd_find_duplicate_chunks,
    cmd_find_empty_embeddings,
)
from ragbot.shared.constants import DEFAULT_CORPUS_CLEAN_SERVICE_MIN_CHARS

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
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


async def _ensure_tenant_row(sf: Any, record_tenant_id: UUID) -> None:
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


async def _seed_bot(sf: Any) -> tuple[UUID, UUID]:
    """Seed one bot + 2 documents + 5 chunks; return ``(record_tenant_id, record_bot_id)``."""
    record_tenant_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    record_bot_id = uuid.uuid4()
    document_id = uuid.uuid4()
    sibling_doc_id = uuid.uuid4()
    chunk_dup_a = uuid.uuid4()
    chunk_dup_b = uuid.uuid4()
    chunk_conflict_a = uuid.uuid4()
    chunk_conflict_b = uuid.uuid4()
    chunk_empty = uuid.uuid4()

    await _ensure_tenant_row(sf, record_tenant_id)
    async with sf() as session:
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
                    :id, :rtid, 'sys-corpus-test', :bot_slug, 'web',
                    'corpus-clean test', '', false,
                    now(), now(), '{}'::jsonb,
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                ON CONFLICT DO NOTHING
                """,
            ),
            {
                "id": record_bot_id,
                "rtid": record_tenant_id,
                "bot_slug": f"corpus-clean-{record_bot_id.hex[:10]}",
            },
        )
        for did, name, src in (
            (document_id, "fixture-doc-1.md", "https://example.test/d1"),
            (sibling_doc_id, "fixture-doc-2.md", "https://example.test/d2"),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO documents (id, record_tenant_id, workspace_id,
                        record_bot_id, source_url, document_name, tool_name,
                        mime_type, language, state, version, content_hash, acl,
                        metadata_json, created_at, updated_at, raw_content)
                    VALUES (:id, :rtid, 'sys-corpus-test', :bot, :src, :name,
                        'corpus_clean_test', 'text/markdown', 'vi', 'active',
                        1, :hash, ARRAY[]::varchar[], '{}'::jsonb, now(), now(),
                        :raw)
                    """,
                ),
                {
                    "id": did,
                    "rtid": record_tenant_id,
                    "bot": record_bot_id,
                    "src": src,
                    "name": name,
                    "hash": f"hash-{did.hex[:16]}",
                    "raw": "## Header\nbody " + " ".join(["w"] * 300) + " 199K",
                },
            )
        # 2 duplicate-hash chunks
        for ch in (chunk_dup_a, chunk_dup_b):
            await session.execute(
                text(
                    """
                    INSERT INTO document_chunks (id, document_id, bot_id,
                        record_document_id, record_bot_id, tenant_id,
                        chunk_index, content, content_hash, embedding,
                        metadata_json, created_at)
                    VALUES (:id, :did, :bot, :did, :bot, :rtid,
                        0, :c, 'DUPLICATEHASH', NULL, '{}'::jsonb, now())
                    """,
                ),
                {
                    "id": ch,
                    "did": document_id,
                    "bot": record_bot_id,
                    "rtid": record_tenant_id,
                    "c": "Repeated chunk body for dedup test",
                },
            )
        # 2 price-conflict chunks (same service prefix, different prices)
        for ch, did, body, h in (
            (
                chunk_conflict_a,
                document_id,
                "Cham soc da chuyen sau gia 199K combo",
                "CONFLICTHASH-A",
            ),
            (
                chunk_conflict_b,
                sibling_doc_id,
                "Cham soc da chuyen sau gia 299K combo",
                "CONFLICTHASH-B",
            ),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO document_chunks (id, document_id, bot_id,
                        record_document_id, record_bot_id, tenant_id,
                        chunk_index, content, content_hash, embedding,
                        metadata_json, created_at)
                    VALUES (:id, :did, :bot, :did, :bot, :rtid,
                        1, :c, :h, NULL, '{}'::jsonb, now())
                    """,
                ),
                {
                    "id": ch,
                    "did": did,
                    "bot": record_bot_id,
                    "rtid": record_tenant_id,
                    "c": body,
                    "h": h,
                },
            )
        # 1 unique-hash chunk with NULL embedding (control case)
        await session.execute(
            text(
                """
                INSERT INTO document_chunks (id, document_id, bot_id,
                    record_document_id, record_bot_id, tenant_id,
                    chunk_index, content, content_hash, embedding,
                    metadata_json, created_at)
                VALUES (:id, :did, :bot, :did, :bot, :rtid,
                    3, :c, 'UNIQUEHASH', NULL, '{}'::jsonb, now())
                """,
            ),
            {
                "id": chunk_empty,
                "did": sibling_doc_id,
                "bot": record_bot_id,
                "rtid": record_tenant_id,
                "c": "Solo chunk no embedding",
            },
        )
        await session.commit()
    return record_tenant_id, record_bot_id


async def _cleanup(sf: Any, record_bot_id: UUID) -> None:
    async with sf() as session:
        await session.execute(
            text("DELETE FROM document_chunks WHERE record_bot_id = :b"),
            {"b": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE record_bot_id = :b"),
            {"b": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :b"),
            {"b": record_bot_id},
        )
        await session.commit()


def _make_args(**overrides: Any) -> Any:
    """Lightweight Namespace stand-in matching the attributes the handlers read."""
    base: dict[str, Any] = {
        "record_tenant_id": None,
        "workspace_id": None,
        "bot_id": None,
        "channel_type": None,
        "bot_uuid": None,
        "allow_uuid": True,
        "format": "json",
        "dry_run": False,
        "regex": None,
        "service_min_chars": DEFAULT_CORPUS_CLEAN_SERVICE_MIN_CHARS,
        "apply": False,
    }
    base.update(overrides)

    class _NS:
        def __init__(self, d: dict[str, Any]) -> None:
            for k, v in d.items():
                setattr(self, k, v)

    return _NS(base)


async def _run(handler: Any, *, record_tenant_id: UUID, record_bot_id: UUID) -> dict[str, Any]:
    """Bind tenant context, run handler, return parsed JSON."""
    from ragbot.config.logging import tenant_id_ctx

    token = tenant_id_ctx.set(str(record_tenant_id))
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await handler(_make_args(bot_uuid=str(record_bot_id)))
        assert rc == 0
        return json.loads(buf.getvalue())
    finally:
        tenant_id_ctx.reset(token)


async def test_find_duplicate_chunks_reports_dup_group(session_factory: Any) -> None:
    record_tenant_id, record_bot_id = await _seed_bot(session_factory)
    try:
        payload = await _run(
            cmd_find_duplicate_chunks,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
        )
        assert payload["header"]["groups_with_duplicates"] >= 1
        hashes = {row["content_hash"] for row in payload["rows"]}
        assert "DUPLICATEHASH" in hashes
        # Each duplicate row carries the chunk_ids array for the owner to inspect.
        dup_row = next(r for r in payload["rows"] if r["content_hash"] == "DUPLICATEHASH")
        assert dup_row["dup_count"] == 2
        assert len(dup_row["chunk_ids"]) == 2
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_find_conflict_prices_detects_two_prices_same_service(
    session_factory: Any,
) -> None:
    record_tenant_id, record_bot_id = await _seed_bot(session_factory)
    try:
        payload = await _run(
            cmd_find_conflict_prices,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
        )
        all_prices = {p for row in payload["rows"] for p in row["prices"].split(",")}
        assert "199K" in all_prices
        assert "299K" in all_prices
        # Both conflict rows share the same service_key bucket.
        keys = {row["service_key"] for row in payload["rows"]}
        assert len(keys) == 1
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_find_empty_embeddings_lists_seeded_null_chunks(
    session_factory: Any,
) -> None:
    record_tenant_id, record_bot_id = await _seed_bot(session_factory)
    try:
        payload = await _run(
            cmd_find_empty_embeddings,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
        )
        # All 5 seeded chunks have NULL embedding.
        assert payload["header"]["empty_count"] == 5
        assert all(r["suggested_action"].startswith("run re-embed") for r in payload["rows"])
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_dry_run_does_not_mutate(session_factory: Any) -> None:
    """All read-only subcommands leave the chunk table unchanged."""
    record_tenant_id, record_bot_id = await _seed_bot(session_factory)
    try:
        async with session_factory() as session:
            r = await session.execute(
                text("SELECT COUNT(*) FROM document_chunks WHERE record_bot_id = :b"),
                {"b": record_bot_id},
            )
            count_before = r.scalar_one()

        from ragbot.config.logging import tenant_id_ctx

        token = tenant_id_ctx.set(str(record_tenant_id))
        try:
            for handler in (
                cmd_find_duplicate_chunks,
                cmd_find_conflict_prices,
                cmd_find_empty_embeddings,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    await handler(
                        _make_args(bot_uuid=str(record_bot_id), dry_run=True),
                    )
        finally:
            tenant_id_ctx.reset(token)

        async with session_factory() as session:
            r = await session.execute(
                text("SELECT COUNT(*) FROM document_chunks WHERE record_bot_id = :b"),
                {"b": record_bot_id},
            )
            count_after = r.scalar_one()
        assert count_before == count_after
    finally:
        await _cleanup(session_factory, record_bot_id)
