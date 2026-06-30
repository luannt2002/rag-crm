"""Embed-failure must NOT leave a dedup-blocking 0-chunk orphan (P3).

Pre-fix bug: a document row is INSERTed + COMMITTed early (carrying its
``content_hash``) before chunking/embedding run. If the embedder raises
``ExternalServiceError`` at U7 (e.g. a dead model 404), ingest aborts
AFTER the row is committed. The abort path marked ``state='failed'`` but
left ``deleted_at`` NULL — so the content-hash dedup
(``WHERE record_bot_id=:bid AND content_hash=:h AND deleted_at IS NULL``)
still found the orphan and raised ``DocumentDuplicateError`` (HTTP 409),
permanently blocking re-upload of the same file after a *transient* embed
failure.

Fix: on an ingest abort that happens before chunks are stored, soft-delete
the orphan (``deleted_at = now()``) so it no longer matches the dedup's
``deleted_at IS NULL`` filter and a re-upload succeeds. The error is still
logged and re-raised (no swallow) — the 5xx surfaces to the caller.

Tests touch the real DB (DATABASE_URL from .env). Each test seeds an
isolated bot UUID and cleans up after itself. Mirrors the fixtures in
``test_content_hash_dedup.py``.
"""
from __future__ import annotations

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
from ragbot.shared.errors import DocumentDuplicateError, ExternalServiceError

pytestmark = pytest.mark.asyncio

# Shared tenant UUID so ``session_with_tenant`` (RLS-scoped) can open.
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


class _OkEmbedder:
    """Deterministic embedder so a happy-path ingest completes."""

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any = None,
    ) -> list[list[float]]:
        return [[0.001 * (i + 1) for i in range(spec.dimension)] for _ in texts]


class _DeadEmbedder:
    """Embedder that raises ``ExternalServiceError`` at U7 (dead model 404)."""

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any = None,
    ) -> list[list[float]]:
        raise ExternalServiceError("embedding provider returned 404 (dead model)")


async def _ensure_tenant_row(sf: Any, record_tenant_id: uuid.UUID) -> None:
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


async def _seed_bot(sf: Any, *, record_tenant_id: uuid.UUID | None = None) -> uuid.UUID:
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
                    'p3-orphan test bot', '', false,
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
                "bot_id": f"p3-orphan-{record_bot_id.hex[:12]}",
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


def _doc_service(sf: Any, settings: Settings, embedder: Any) -> DocumentService:
    return DocumentService(
        session_factory=sf,
        embedder=embedder,
        settings=settings,
        config_service=None,
    )


@pytest.fixture(autouse=True)
def _bind_tenant_ctx() -> Any:
    from ragbot.config.logging import tenant_id_ctx
    token = tenant_id_ctx.set(str(_TEST_TENANT_UUID))
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


async def _n_live_docs(sf: Any, record_bot_id: uuid.UUID) -> int:
    async with sf() as session:
        row = await session.execute(
            text(
                "SELECT count(*) FROM documents "
                "WHERE record_bot_id = :bid AND deleted_at IS NULL"
            ),
            {"bid": record_bot_id},
        )
        return int(row.scalar_one())


async def test_embed_failure_soft_deletes_orphan(session_factory, settings):
    """U7 embed raises ExternalServiceError → the committed doc must NOT
    remain a live, dedup-blocking 0-chunk row: it is soft-deleted."""
    bot = await _seed_bot(session_factory)
    try:
        svc = _doc_service(session_factory, settings, _DeadEmbedder())
        content = "p3 orphan fixture body that must embed but the model is dead"

        # The embed failure must surface (no swallow) as ExternalServiceError.
        with pytest.raises(ExternalServiceError):
            await svc.ingest(
                record_bot_id=bot, title="orphan-doc", content=content,
                record_tenant_id=_TEST_TENANT_UUID,
            )

        # The row was committed early but ingest aborted before chunks were
        # stored. It must NOT count as a live document anymore.
        assert await _n_live_docs(session_factory, bot) == 0

        # Concretely: the row exists but is soft-deleted (deleted_at set) so
        # admin recovery is possible; it is no longer dedup-blocking.
        async with session_factory() as session:
            rows = await session.execute(
                text(
                    "SELECT deleted_at, state, "
                    "(SELECT count(*) FROM document_chunks c "
                    " WHERE c.record_document_id = d.id) AS n_chunks "
                    "FROM documents d WHERE d.record_bot_id = :bid"
                ),
                {"bid": bot},
            )
            row = rows.fetchone()
        assert row is not None, "the orphan row should still exist (soft-delete, not hard-delete)"
        deleted_at, state, n_chunks = row
        assert deleted_at is not None, "orphan must be soft-deleted (deleted_at set)"
        assert n_chunks == 0, "the orphan has zero chunks"
        assert state == "failed", "state still records the failure for admin tooling"
    finally:
        await _cleanup_bot(session_factory, bot)


async def test_reupload_after_embed_failure_not_blocked(session_factory, settings):
    """After a transient embed failure soft-deletes the orphan, a re-upload
    of the SAME content does NOT raise DocumentDuplicateError and succeeds."""
    bot = await _seed_bot(session_factory)
    try:
        content = "p3 reupload fixture identical body across two ingest attempts"

        # Attempt 1: embedder is dead → abort + soft-delete orphan.
        svc_dead = _doc_service(session_factory, settings, _DeadEmbedder())
        with pytest.raises(ExternalServiceError):
            await svc_dead.ingest(
                record_bot_id=bot, title="attempt-1", content=content,
                record_tenant_id=_TEST_TENANT_UUID,
            )

        # Attempt 2: embedder is healthy → SAME content must ingest cleanly,
        # NOT bounce on the content-hash dedup (the bug being fixed).
        svc_ok = _doc_service(session_factory, settings, _OkEmbedder())
        result = await svc_ok.ingest(
            record_bot_id=bot, title="attempt-2", content=content,
            record_tenant_id=_TEST_TENANT_UUID,
        )

        assert result.document_id is not None
        # The second attempt is now the one live document for this bot.
        assert await _n_live_docs(session_factory, bot) == 1

        # And it actually stored chunks (real ingest, not a no-op).
        async with session_factory() as session:
            n = await session.execute(
                text(
                    "SELECT count(*) FROM document_chunks "
                    "WHERE record_document_id = :did"
                ),
                {"did": result.document_id},
            )
        assert int(n.scalar_one()) > 0, "re-upload must store chunks"
    finally:
        await _cleanup_bot(session_factory, bot)


async def test_dedup_still_blocks_a_live_duplicate(session_factory, settings):
    """Regression guard: the soft-delete-on-failure fix must NOT weaken the
    real dedup — a SUCCESSFUL ingest followed by the same content still
    raises DocumentDuplicateError."""
    bot = await _seed_bot(session_factory)
    try:
        svc = _doc_service(session_factory, settings, _OkEmbedder())
        content = "p3 live-dup fixture body that ingests successfully twice"
        await svc.ingest(
            record_bot_id=bot, title="first", content=content,
            record_tenant_id=_TEST_TENANT_UUID,
        )
        with pytest.raises(DocumentDuplicateError):
            await svc.ingest(
                record_bot_id=bot, title="second", content=content,
                source_url="https://example.test/p3-live-dup",
                record_tenant_id=_TEST_TENANT_UUID,
            )
    finally:
        await _cleanup_bot(session_factory, bot)
