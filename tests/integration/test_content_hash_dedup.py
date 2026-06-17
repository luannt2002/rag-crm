"""Content-hash dedup at INGEST (integration, real Postgres).

Pre-fix: identical Google Sheet exports landed twice (Sheet-1 == Sheet-4
audit 2026-04-29). DocumentService now sha256-hashes raw_content and
rejects the second insert with ``DocumentDuplicateError`` when a live
row with the same (record_bot_id, content_hash) exists.

Tests touch the real DB (DATABASE_URL from .env). Each test seeds an
isolated bot UUID and cleans up after itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.document_service import DocumentService
from ragbot.config.settings import Settings
from ragbot.shared.errors import DocumentDuplicateError

pytestmark = pytest.mark.asyncio

# Every DocumentService.ingest() call now requires either an explicit
# ``record_tenant_id`` kwarg or a bound ``tenant_id_ctx`` ContextVar
# (else ``RuntimeError: tenant_id_ctx not bound``). Tests share one fixture
# UUID so cross-tenant filtering still works correctly.
_TEST_TENANT_UUID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


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


@pytest.fixture()
def settings() -> Settings:
    return Settings()


class _StubEmbedder:
    """Deterministic embedder so ingest completes without LLM calls."""

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any = None,
    ) -> list[list[float]]:
        return [[0.001 * (i + 1) for i in range(spec.dimension)] for _ in texts]


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


async def _seed_bot(
    sf: Any, *, record_tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    record_bot_id = uuid.uuid4()
    if record_tenant_id is None:
        record_tenant_id = _TEST_TENANT_UUID
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
                    :id, :rt, :ws, :bot_id, 'web',
                    't1-dedup test bot', '', false,
                    now(), now(), CAST(:opts AS jsonb),
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                ON CONFLICT DO NOTHING
                """,
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": f"ws-{record_bot_id.hex[:8]}",
                "bot_id": f"t1-dedup-{record_bot_id.hex[:12]}",
                "opts": json.dumps({}),
            },
        )
        await session.commit()
    return record_bot_id


async def _cleanup_bot(sf: Any, record_bot_id: uuid.UUID) -> None:
    async with sf() as session:
        await session.execute(
            text("DELETE FROM document_chunks WHERE record_document_id IN "
                 "(SELECT id FROM documents WHERE record_bot_id = :bid)"),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE record_bot_id = :bid"),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :bid"),
            {"bid": record_bot_id},
        )
        await session.commit()


def _doc_service(sf: Any, settings: Settings) -> DocumentService:
    return DocumentService(
        session_factory=sf,
        embedder=_StubEmbedder(),
        settings=settings,
        config_service=None,
    )


@pytest.fixture(autouse=True)
def _bind_tenant_ctx() -> Any:
    """Bind ``tenant_id_ctx`` so DocumentService can open RLS-scoped sessions.

    ``session_with_tenant`` refuses to open a session while the
    ContextVar is unset (cross-tenant write leak fix). Tests in this
    file legitimately call ``ingest()`` without an explicit
    ``record_tenant_id`` kwarg — a real tenant UUID is bound here so the
    safety check passes without weakening it.
    """
    from ragbot.config.logging import tenant_id_ctx
    token = tenant_id_ctx.set(str(_TEST_TENANT_UUID))
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


async def test_duplicate_content_raises_error(session_factory, settings):
    """Same raw_content + same bot → second ingest raises DocumentDuplicateError."""
    bot = await _seed_bot(session_factory)
    try:
        svc = _doc_service(session_factory, settings)
        content = "alpha beta gamma duplicate fixture body T1"
        await svc.ingest(record_bot_id=bot, title="first", content=content)
        with pytest.raises(DocumentDuplicateError):
            await svc.ingest(
                record_bot_id=bot, title="second", content=content,
                # Different source_url so the source_url-dedup path is bypassed
                # and the new content_hash dedup path is the one being tested.
                source_url="https://example.test/duplicate-content",
            )
    finally:
        await _cleanup_bot(session_factory, bot)


async def test_different_content_same_bot_ok(session_factory, settings):
    """Different raw_content under the same bot succeeds twice."""
    bot = await _seed_bot(session_factory)
    try:
        svc = _doc_service(session_factory, settings)
        r1 = await svc.ingest(
            record_bot_id=bot,
            title="doc-A",
            content="content A unique fixture body T1 alpha",
        )
        r2 = await svc.ingest(
            record_bot_id=bot,
            title="doc-B",
            content="content B unique fixture body T1 bravo",
        )
        assert r1.document_id != r2.document_id
    finally:
        await _cleanup_bot(session_factory, bot)


async def test_same_content_different_bot_ok(session_factory, settings):
    """Same content under DIFFERENT bots is allowed — UNIQUE is per-bot."""
    bot_a = await _seed_bot(
        session_factory,
        record_tenant_id=uuid.UUID("00000000-0000-0000-0000-000000099002"),
    )
    bot_b = await _seed_bot(
        session_factory,
        record_tenant_id=uuid.UUID("00000000-0000-0000-0000-000000099003"),
    )
    try:
        svc = _doc_service(session_factory, settings)
        content = "shared fixture body across two bots T1"
        r1 = await svc.ingest(record_bot_id=bot_a, title="A-doc", content=content)
        r2 = await svc.ingest(record_bot_id=bot_b, title="B-doc", content=content)
        assert r1.document_id != r2.document_id
    finally:
        await _cleanup_bot(session_factory, bot_a)
        await _cleanup_bot(session_factory, bot_b)


async def test_content_hash_calculated_on_ingest(session_factory, settings):
    """content_hash column == sha256(raw_content) — verified end-to-end."""
    bot = await _seed_bot(session_factory)
    try:
        svc = _doc_service(session_factory, settings)
        content = "hash calculation fixture body T1 12345"
        expected = hashlib.sha256(content.encode()).hexdigest()
        result = await svc.ingest(
            record_bot_id=bot, title="hash-check", content=content,
        )
        async with session_factory() as session:
            row = await session.execute(
                text("SELECT content_hash FROM documents WHERE id = :id"),
                {"id": result.document_id},
            )
            stored = row.scalar_one()
        assert stored == expected
    finally:
        await _cleanup_bot(session_factory, bot)
