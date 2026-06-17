"""P24-L1 integration tests — semantic_cache invalidation, embed NULL raise,
re-upload dedup on DocumentService.

Hits the real DB (DATABASE_URL from .env). Each test uses a unique test bot
UUID and cleans up after itself.

Covers:
  1. delete_all_for_bot invalidates semantic_cache
  2. delete_document invalidates semantic_cache
  3. incremental re-ingest invalidates semantic_cache
  4. re-upload same source_url reuses existing document (no duplicate row)
  5. embed_batch failure raises (does NOT silently insert NULL embeddings)
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
from ragbot.shared.errors import ExternalServiceError

pytestmark = pytest.mark.asyncio


# Every DocumentService.ingest() call now requires either an explicit
# ``record_tenant_id`` kwarg or a bound ``tenant_id_ctx`` ContextVar
# (else ``RuntimeError: tenant_id_ctx not bound``).
_TEST_TENANT_UUID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest.fixture(autouse=True)
def _bind_tenant_ctx() -> Any:
    """Bind ``tenant_id_ctx`` so DocumentService can open RLS-scoped sessions."""
    from ragbot.config.logging import tenant_id_ctx
    token = tenant_id_ctx.set(str(_TEST_TENANT_UUID))
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Fallback: try to read .env directly (pytest runs without env_file auto-load
    # for DatabaseSettings if parent shell didn't `set -a`).
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    env_path = os.path.abspath(env_path)
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
    """Returns a deterministic 1536-dim vector per input text."""

    def __init__(self, *, dimension: int = 1536) -> None:
        self._dim = dimension
        self.call_count = 0

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any = None,
    ) -> list[list[float]]:
        self.call_count += 1
        # Deterministic fake vector — [0.001, 0.002, ..., 0.001*dim] per text
        return [[0.001 * (i + 1) for i in range(self._dim)] for _ in texts]


class _FailingEmbedder:
    """Simulates embedding provider outage — every call raises."""

    def __init__(self) -> None:
        self.call_count = 0

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any = None,
    ) -> list[list[float]]:
        self.call_count += 1
        raise ExternalServiceError("simulated embedding outage")


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
    sf: Any,
    record_bot_id: uuid.UUID,
    *,
    record_tenant_id: uuid.UUID | None = None,
) -> None:
    """Insert a test bot row so FK constraints pass (4-key identity)."""
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
                    'p24-l1 test bot', '', false,
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
                "bot_id": f"p24-l1-{record_bot_id.hex[:12]}",
                "opts": json.dumps({}),
            },
        )
        await session.commit()


async def _seed_semantic_cache_row(sf: Any, record_bot_id: uuid.UUID) -> uuid.UUID:
    """Insert a fake semantic_cache row scoped to this bot."""
    row_id = uuid.uuid4()
    async with sf() as session:
        await session.execute(
            text(
                """
                INSERT INTO semantic_cache (id, record_bot_id, record_tenant_id,
                    query_hash, answer, citations, model_name, cached_at_ts,
                    metadata_json)
                VALUES (:id, :bid, NULL, :qh, :ans, CAST('[]' AS jsonb), 'test-model',
                    0, CAST('{}' AS jsonb))
                """,
            ),
            {
                "id": row_id,
                "bid": record_bot_id,
                "qh": "a" * 64,
                "ans": "cached answer (should be invalidated)",
            },
        )
        await session.commit()
    return row_id


async def _count_cache(sf: Any, record_bot_id: uuid.UUID) -> int:
    async with sf() as session:
        r = await session.execute(
            text("SELECT COUNT(*) FROM semantic_cache WHERE record_bot_id = :bid"),
            {"bid": record_bot_id},
        )
        row = r.fetchone()
        return int(row[0]) if row else 0


async def _count_docs(sf: Any, record_bot_id: uuid.UUID, source_url: str) -> int:
    async with sf() as session:
        r = await session.execute(
            text(
                """
                SELECT COUNT(*) FROM documents
                WHERE record_bot_id = :bid AND source_url = :url AND deleted_at IS NULL
                """,
            ),
            {"bid": record_bot_id, "url": source_url},
        )
        row = r.fetchone()
        return int(row[0]) if row else 0


async def _count_chunks(sf: Any, document_id: uuid.UUID) -> int:
    async with sf() as session:
        r = await session.execute(
            text("SELECT COUNT(*) FROM document_chunks WHERE record_document_id = :d"),
            {"d": document_id},
        )
        row = r.fetchone()
        return int(row[0]) if row else 0


async def _cleanup_bot(sf: Any, record_bot_id: uuid.UUID) -> None:
    async with sf() as session:
        await session.execute(
            text(
                """
                DELETE FROM document_chunks
                WHERE record_document_id IN (
                    SELECT id FROM documents WHERE record_bot_id = :bid
                )
                """,
            ),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE record_bot_id = :bid"),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :id"),
            {"id": record_bot_id},
        )
        await session.commit()


# ── Tests ──────────────────────────────────────────────────────────────────


async def test_delete_all_for_bot_invalidates_semantic_cache(
    session_factory: Any,
    settings: Settings,
) -> None:
    """L1.1(a): delete_all_for_bot must DELETE all semantic_cache rows for that bot."""
    record_bot_id = uuid.uuid4()
    await _seed_bot(session_factory, record_bot_id)
    try:
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
        )
        # Ingest a tiny doc so there is something to delete
        await svc.ingest(
            record_bot_id=record_bot_id,
            title="doc-v1",
            content="Giá dịch vụ: 100k. Nội dung kiểm thử.",
            source_url=f"http://test/p24/l1/{record_bot_id.hex[:8]}/v1",
        )
        # Seed a cached answer
        await _seed_semantic_cache_row(session_factory, record_bot_id)
        assert await _count_cache(session_factory, record_bot_id) >= 1

        # Act
        await svc.delete_all_for_bot(record_bot_id)

        # Assert: cache purged
        assert await _count_cache(session_factory, record_bot_id) == 0
    finally:
        await _cleanup_bot(session_factory, record_bot_id)


async def test_delete_document_invalidates_semantic_cache(
    session_factory: Any,
    settings: Settings,
) -> None:
    """L1.1(b): delete_document must purge semantic_cache for the owning bot."""
    record_bot_id = uuid.uuid4()
    await _seed_bot(session_factory, record_bot_id)
    try:
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
        )
        res = await svc.ingest(
            record_bot_id=record_bot_id,
            title="doc-to-delete",
            content="Bảng giá phiên bản A. Rất nhiều nội dung kiểm thử ở đây.",
            source_url=f"http://test/p24/l1/{record_bot_id.hex[:8]}/del",
        )
        await _seed_semantic_cache_row(session_factory, record_bot_id)
        assert await _count_cache(session_factory, record_bot_id) >= 1

        # Act
        await svc.delete_document(res.document_id)

        # Assert
        assert await _count_cache(session_factory, record_bot_id) == 0
    finally:
        await _cleanup_bot(session_factory, record_bot_id)


async def test_incremental_reingest_invalidates_stale_cache(
    session_factory: Any,
    settings: Settings,
) -> None:
    """L1.1(c): changing a doc's content must purge semantic_cache."""
    record_bot_id = uuid.uuid4()
    await _seed_bot(session_factory, record_bot_id)
    source_url = f"http://test/p24/l1/{record_bot_id.hex[:8]}/reindex"
    try:
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
        )
        # Ingest v1
        res1 = await svc.ingest(
            record_bot_id=record_bot_id,
            title="reindex-doc",
            content="Giá: 100k / tháng. Phiên bản một.",
            source_url=source_url,
        )
        # Seed cache AFTER first ingest (so ingest's own invalidation doesn't hide it)
        await _seed_semantic_cache_row(session_factory, record_bot_id)
        assert await _count_cache(session_factory, record_bot_id) >= 1

        # Act: incremental re-ingest same source_url → dedup path → mutation
        res2 = await svc.ingest(
            record_bot_id=record_bot_id,
            title="reindex-doc",
            content="Giá: 200k / tháng. Phiên bản HAI với nội dung đã thay đổi.",
            source_url=source_url,
        )

        # Assert: cache purged, doc row is the same one
        assert res2.document_id == res1.document_id
        assert await _count_cache(session_factory, record_bot_id) == 0
    finally:
        await _cleanup_bot(session_factory, record_bot_id)


async def test_reupload_same_source_url_reuses_existing_doc(
    session_factory: Any,
    settings: Settings,
) -> None:
    """L1.3: second ingest() of same source_url reuses the existing doc id,
    never creates a duplicate document row."""
    record_bot_id = uuid.uuid4()
    await _seed_bot(session_factory, record_bot_id)
    source_url = f"http://test/p24/l1/{record_bot_id.hex[:8]}/dedup"
    try:
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
        )
        res1 = await svc.ingest(
            record_bot_id=record_bot_id,
            title="dedup-doc",
            content="Nội dung đầu tiên đủ dài để tạo vài chunk. " * 5,
            source_url=source_url,
        )
        res2 = await svc.ingest(
            record_bot_id=record_bot_id,
            title="dedup-doc (v2)",
            content="Nội dung đã thay đổi hoàn toàn ở lần upload thứ hai. " * 5,
            source_url=source_url,
        )

        assert res2.document_id == res1.document_id, (
            "Re-upload with same source_url must reuse the existing document row"
        )
        assert await _count_docs(session_factory, record_bot_id, source_url) == 1
        # Chunks exist for the single doc
        assert await _count_chunks(session_factory, res1.document_id) > 0
    finally:
        await _cleanup_bot(session_factory, record_bot_id)


async def test_embed_batch_fail_raises_instead_of_nulls(
    session_factory: Any,
    settings: Settings,
) -> None:
    """L1.2: when the embedder raises, ingest() must propagate (no NULL-embed
    chunks silently stored) and mark the document as 'failed'."""
    record_bot_id = uuid.uuid4()
    await _seed_bot(session_factory, record_bot_id)
    try:
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_FailingEmbedder(),
            settings=settings,
        )
        with pytest.raises(ExternalServiceError):
            await svc.ingest(
                record_bot_id=record_bot_id,
                title="embed-fail-doc",
                content="Nội dung kiểm thử. Embedder sẽ raise. " * 10,
                source_url=f"http://test/p24/l1/{record_bot_id.hex[:8]}/fail",
            )

        # No chunk rows exist for this bot (insert block never ran)
        async with session_factory() as session:
            r = await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM document_chunks dc
                    JOIN documents d ON d.id = dc.record_document_id
                    WHERE d.record_bot_id = :bid
                    """,
                ),
                {"bid": record_bot_id},
            )
            row = r.fetchone()
            assert int(row[0]) == 0, "No chunks should be inserted on embed failure"

            # Document row exists with state='failed'
            r2 = await session.execute(
                text("SELECT state FROM documents WHERE record_bot_id = :bid"),
                {"bid": record_bot_id},
            )
            states = [row[0] for row in r2.fetchall()]
            assert states == ["failed"], f"expected ['failed'], got {states}"
    finally:
        await _cleanup_bot(session_factory, record_bot_id)
