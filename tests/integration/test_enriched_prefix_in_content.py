"""enriched_prefix persisted into ``content`` column.

Pre-fix: the contextual prefix lived only in metadata + the embedding
path. BM25 + cross-encoder reranker NEVER saw the prefix because both
score off ``content`` directly. Audit 2026-04-29: this hid the entire
enrichment effort from retrieval.

Post-fix: when ``DEFAULT_ENRICHED_PREFIX_PERSIST`` (or system_config
``enriched_prefix_persist_in_content``) is True, ingest writes the
enriched text into ``content`` and stores the original chunk in
``metadata_json.raw_chunk`` for citation reconstruction.
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

pytestmark = pytest.mark.asyncio


# Bind tenant_id_ctx so DocumentService can open RLS-scoped sessions
# (cross-tenant write leak fix).
_TEST_TENANT_UUID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


@pytest.fixture(autouse=True)
def _bind_tenant_ctx() -> Any:
    from ragbot.config.logging import tenant_id_ctx
    token = tenant_id_ctx.set(str(_TEST_TENANT_UUID))
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


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


@pytest.fixture()
def settings() -> Settings:
    return Settings()


class _StubEmbedder:
    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any = None,
    ) -> list[list[float]]:
        return [[0.001 * (i + 1) for i in range(spec.dimension)] for _ in texts]


class _StubConfig:
    """system_config stub controlled by a dict."""

    def __init__(self, overrides: dict[str, Any] | None = None):
        self._overrides = overrides or {}

    async def get(self, key: str, default: Any = None) -> Any:
        return self._overrides.get(key, default)

    async def get_int(self, key: str, default: int = 0) -> int:
        return int(self._overrides.get(key, default))

    async def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self._overrides.get(key, default))

    async def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self._overrides.get(key, default))


async def _ensure_tenant_row(sf, record_tenant_id: uuid.UUID) -> None:
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
    sf, record_tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    bot_id = uuid.uuid4()
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
                    :id, :rt, :ws, :slug, 'web',
                    't1-prefix bot', '', false,
                    now(), now(), CAST(:opts AS jsonb),
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                """,
            ),
            {
                "id": bot_id,
                "rt": record_tenant_id,
                "ws": f"ws-{bot_id.hex[:8]}",
                "slug": f"t1-prefix-{bot_id.hex[:12]}",
                "opts": json.dumps({}),
            },
        )
        await session.commit()
    return bot_id


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


async def test_enriched_prefix_prepended_to_content(session_factory, settings):
    """When persist flag ON (default) — content column starts with the prefix.

    Without LLM, ``enrich_chunks`` falls back to a deterministic template
    ``"Tài liệu: <title>. Đoạn <pos>."`` so we can grep for it directly.
    """
    bot = await _seed_bot(session_factory)
    try:
        cfg = _StubConfig({
            # Force CR off (no API key in test env) so we exercise legacy enrich.
            "contextual_retrieval_enabled": False,
            "enrichment_enabled": False,
            "enriched_prefix_persist_in_content": True,
            "ingestion_validation_enabled": False,
            "vi_compound_segmentation_ingest_enabled": False,
            "whole_doc_enabled": False,
            "parent_child_enabled": False,
        })
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
            config_service=cfg,
        )
        result = await svc.ingest(
            record_bot_id=bot,
            title="prefix-doc",
            content=(
                "Đây là đoạn nội dung số 1. " * 60
                + "\n\nĐây là đoạn nội dung số 2 hoàn toàn khác. " * 60
            ),
        )
        assert result.chunks >= 1
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT content, metadata_json FROM document_chunks "
                        "WHERE record_document_id = :did ORDER BY chunk_index"
                    ),
                    {"did": result.document_id},
                )
            ).fetchall()
        assert rows, "no chunks inserted"
        # Template prefix from `_fallback_prefix` lives at the head of every chunk.
        for content_col, _ in rows:
            assert content_col.startswith("Tài liệu: prefix-doc."), (
                f"content missing prefix: {content_col[:80]!r}"
            )
    finally:
        await _cleanup(session_factory, bot)


async def test_bm25_search_finds_prefix_words(session_factory, settings):
    """search_vector tokenises the persisted prefix → BM25 matches prefix words."""
    bot = await _seed_bot(
        session_factory,
        record_tenant_id=uuid.UUID("00000000-0000-0000-0000-000000099101"),
    )
    try:
        cfg = _StubConfig({
            "contextual_retrieval_enabled": False,
            "enrichment_enabled": False,
            "enriched_prefix_persist_in_content": True,
            "ingestion_validation_enabled": False,
            "vi_compound_segmentation_ingest_enabled": False,
            "whole_doc_enabled": False,
            "parent_child_enabled": False,
        })
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
            config_service=cfg,
        )
        result = await svc.ingest(
            record_bot_id=bot,
            title="bm25prefixprobe",
            content="A" * 800 + "\n\n" + "B" * 800,
        )
        # The fallback prefix injects the title — ``bm25prefixprobe`` should
        # appear in search_vector for every chunk.
        async with session_factory() as session:
            r = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM document_chunks "
                        "WHERE record_document_id = :did "
                        "AND search_vector @@ to_tsquery('simple', 'bm25prefixprobe')"
                    ),
                    {"did": result.document_id},
                )
            ).scalar_one()
        assert r >= 1, "BM25 should match a token from the persisted prefix"
    finally:
        await _cleanup(session_factory, bot)


async def test_disabled_when_flag_off(session_factory, settings):
    """When ``enriched_prefix_persist_in_content`` = False — content == raw chunk."""
    bot = await _seed_bot(
        session_factory,
        record_tenant_id=uuid.UUID("00000000-0000-0000-0000-000000099102"),
    )
    try:
        cfg = _StubConfig({
            "contextual_retrieval_enabled": False,
            "enrichment_enabled": False,
            "enriched_prefix_persist_in_content": False,  # OFF
            "ingestion_validation_enabled": False,
            "vi_compound_segmentation_ingest_enabled": False,
            "whole_doc_enabled": False,
            "parent_child_enabled": False,
        })
        svc = DocumentService(
            session_factory=session_factory,
            embedder=_StubEmbedder(),
            settings=settings,
            config_service=cfg,
        )
        result = await svc.ingest(
            record_bot_id=bot,
            title="no-prefix-doc",
            content=("Plain chunk content without prefix. " * 60),
        )
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT content FROM document_chunks "
                        "WHERE record_document_id = :did ORDER BY chunk_index LIMIT 1"
                    ),
                    {"did": result.document_id},
                )
            ).scalar_one()
        # Off-mode: content does NOT start with template prefix.
        assert not row.startswith("Tài liệu: no-prefix-doc."), (
            f"prefix leaked into content while flag was OFF: {row[:80]!r}"
        )
    finally:
        await _cleanup(session_factory, bot)
