"""asyncpg compat regression — pgvector cast must NOT use ``:p::vector``.

Bug surfaced 2026-04-29 by load test: every uncached query fell back to
a fresh LLM call because the cache lookup raised
``asyncpg.exceptions.SyntaxOrAccessError`` (or sqlalchemy
``ProgrammingError``) on ``:emb::vector``. The fix in semantic_cache.py
replaces the PostgreSQL ``::`` cast operator with explicit
``CAST(:emb AS vector)`` — asyncpg's parameter parser is happy with
``CAST(...)`` but trips on ``:p::T`` because it sees the second colon
as the start of a new placeholder.

These tests assert:
1. The lookup query executes without a ``ProgrammingError`` /
   ``SyntaxError`` and returns ``None`` on a cold cache.
2. After ``store()``, a subsequent ``find_similar_with_text`` returns
   the stored row (covers exact-hash + cosine paths).

Run requires DATABASE_URL.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.ports.cache_port import CachedResponse
from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache
from ragbot.shared.types import BotId, BotVersion, CorpusVersion, TenantId

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
    record_tenant_id: uuid.UUID,
) -> None:
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
                    'asyncpg-cast test', '', false,
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
                "bot_id": f"asyncpg-cast-{record_bot_id.hex[:12]}",
                "opts": json.dumps({}),
            },
        )
        await session.commit()


async def _cleanup(sf: Any, record_bot_id: uuid.UUID) -> None:
    async with sf() as session:
        await session.execute(
            text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :id"),
            {"id": record_bot_id},
        )
        await session.commit()


def _embedding(seed: int = 1, dim: int = 1536) -> list[float]:
    """Deterministic 1536-d vector — small magnitude so cosine works."""
    return [0.001 * ((i + seed) % 7 + 1) for i in range(dim)]


async def test_cache_query_no_programming_error(session_factory: Any) -> None:
    """find_similar / find_similar_with_text must NOT raise asyncpg
    ``ProgrammingError`` from ``:emb::vector`` parameter-parser confusion.
    A cold-cache miss returns ``None`` cleanly.
    """
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
    await _seed_bot(session_factory, record_bot_id, record_tenant_id=record_tenant_id)
    try:
        cache = PgSemanticCache(session_factory)
        # Cold cache → None, no exception.
        emb = _embedding(seed=1)
        out = await cache.find_similar(
            query_embedding=emb,
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            bot_version=BotVersion("1"),
            corpus_version=CorpusVersion("1"),
        )
        assert out is None

        out2 = await cache.find_similar_with_text(
            query_embedding=emb,
            query_text="bảng giá dịch vụ",
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            bot_version=BotVersion("1"),
            corpus_version=CorpusVersion("1"),
        )
        assert out2 is None
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_cache_hit_returns_row(session_factory: Any) -> None:
    """store() then find_similar_with_text() must return the cached row
    via the exact-hash path (no ::vector cast used here, but the query
    executes against the same DB so this is end-to-end coverage)."""
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    await _seed_bot(session_factory, record_bot_id, record_tenant_id=record_tenant_id)
    try:
        cache = PgSemanticCache(session_factory)
        emb = _embedding(seed=2)
        query_text = "bảng giá dịch vụ B"
        await cache.store(
            query=query_text,
            query_embedding=emb,
            response=CachedResponse(
                answer="Đây là câu trả lời mẫu",
                citations=[],
                model_name="test-model",
                cached_at_ts=int(time.time()),
            ),
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            workspace_id="ws-cache-compat",
            bot_version=BotVersion("1"),
            corpus_version=CorpusVersion("1"),
            ttl_s=600,
        )
        # exact-hash path
        hit = await cache.find_similar_with_text(
            query_embedding=emb,
            query_text=query_text,
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            bot_version=BotVersion("1"),
            corpus_version=CorpusVersion("1"),
        )
        assert hit is not None
        assert hit.answer == "Đây là câu trả lời mẫu"
        assert hit.model_name == "test-model"

        # cosine path: same embedding, different text → bypasses hash, must
        # exercise the CAST(:emb AS vector) branch and find the row.
        cosine_hit = await cache.find_similar(
            query_embedding=emb,
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            bot_version=BotVersion("1"),
            corpus_version=CorpusVersion("1"),
            threshold=0.5,
        )
        assert cosine_hit is not None
        assert cosine_hit.answer == "Đây là câu trả lời mẫu"
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_cache_miss_inserts_row(session_factory: Any) -> None:
    """store() commits a row reachable by direct SQL — proves the INSERT
    statement (which uses ``CAST(:emb AS vector)``) runs without a
    ProgrammingError under asyncpg."""
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000012")
    await _seed_bot(session_factory, record_bot_id, record_tenant_id=record_tenant_id)
    try:
        cache = PgSemanticCache(session_factory)
        await cache.store(
            query="kiểm tra ghi cache",
            query_embedding=_embedding(seed=3),
            response=CachedResponse(
                answer="đã ghi",
                citations=[],
                model_name="m1",
                cached_at_ts=int(time.time()),
            ),
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            workspace_id="ws-cache-miss",
            bot_version=BotVersion("1"),
            corpus_version=CorpusVersion("1"),
            ttl_s=600,
        )
        async with session_factory() as session:
            r = await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM semantic_cache
                    WHERE record_bot_id = :bid
                      AND query_embedding IS NOT NULL
                    """,
                ),
                {"bid": record_bot_id},
            )
            assert int(r.scalar_one()) == 1
    finally:
        await _cleanup(session_factory, record_bot_id)
