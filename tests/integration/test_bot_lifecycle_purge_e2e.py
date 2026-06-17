"""Integration gate G1/G3 — bot purge saga 0-orphan e2e (ADR-W1-D4 §6).

Real Postgres. Seeds an isolated tenant + bot + documents + chunks +
semantic_cache row, soft-deletes, purges, and asserts ZERO orphans in
every child table plus the audit/outbox rows. Redis side-effects are
exercised through an in-memory recorder (the Redis steps' arg contracts
are unit-tested; what ONLY a real DB can prove is the FK CASCADE wipe,
the RLS-scoped DELETE, and the audit/outbox atomicity — that is the
gate here). Skips when ``DATABASE_URL`` is absent.

All seeded rows use fresh UUIDs and are removed by the purge itself /
the cleanup fixture — the test never touches pre-existing data.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.services.bot_lifecycle_service import (
    SKIP_EMBEDDING_CACHE,
    BotLifecycleService,
    BotNotPurgeableError,
)
from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.infrastructure.repositories.tenant_repository import TenantRepository
from ragbot.shared.constants import SUBJECT_BOT_PURGED

pytestmark = pytest.mark.integration


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL env var required for integration tests")
    engine = create_async_engine(dsn, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


class _RecorderRedis:
    """In-memory scan/unlink recorder standing in for the uq-key bust."""

    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def scan(self, cursor: int = 0, match: str = "", count: int = 0):  # noqa: ARG002
        import fnmatch  # noqa: PLC0415 — test-local stdlib helper

        return 0, [k for k in self.keys if fnmatch.fnmatch(k, match)]

    async def unlink(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.keys:
                self.keys.discard(k)
                n += 1
        return n


class _RecorderRegistry:
    def __init__(self) -> None:
        self.invalidations: list[tuple] = []

    async def invalidate(self, *args: Any) -> None:
        self.invalidations.append(args)


class _RecorderCorpus:
    def __init__(self) -> None:
        self.invalidations: list[tuple] = []

    async def invalidate(self, *args: Any) -> None:
        self.invalidations.append(args)


async def _seed(sf: Any) -> dict[str, Any]:
    """Seed tenant → bot (soft-deletable) → 2 docs → 3 chunks → 1
    semantic_cache row. Returns the ids."""
    tenant_id = uuid.uuid4()
    bot_uuid = uuid.uuid4()
    doc_ids = [uuid.uuid4(), uuid.uuid4()]
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, "
                "bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now())",
            ),
            {"id": tenant_id, "name": f"purge-e2e-{str(tenant_id)[:8]}"},
        )
        await session.execute(
            text(
                """
                INSERT INTO bots (
                    id, bot_id, channel_type, workspace_id,
                    record_tenant_id, bot_name, system_prompt,
                    setting_options, custom_vocabulary, max_documents,
                    plan_limits, bypass_token_limit, bypass_rate_limit,
                    language, is_deleted, deleted_at, created_at, updated_at
                ) VALUES (
                    :id, :bot_id, 'web', :ws, :tid, 'purge-e2e-bot', '',
                    '{}'::jsonb, '{}'::jsonb, 0,
                    '{}'::jsonb, false, false,
                    'vi', true, now(), now(), now()
                )
                """,
            ),
            {
                "id": bot_uuid,
                "bot_id": f"purge-e2e-{str(bot_uuid)[:8]}",
                "ws": "purge-e2e-ws",
                "tid": tenant_id,
            },
        )
        for doc_id in doc_ids:
            await session.execute(
                text(
                    """
                    INSERT INTO documents (
                        id, record_tenant_id, workspace_id, record_bot_id,
                        source_url, document_name, tool_name, mime_type,
                        language, state, version, content_hash, acl,
                        metadata_json, created_at, updated_at
                    ) VALUES (
                        :id, :tid, 'purge-e2e-ws', :bid,
                        :src, 'purge-e2e-doc', :tool, 'text/plain',
                        'vi', 'active', 1, :ch, '{}',
                        '{}'::jsonb, now(), now()
                    )
                    """,
                ),
                {
                    "id": doc_id, "tid": tenant_id, "bid": bot_uuid,
                    "src": f"https://example.test/purge-e2e/{doc_id}",
                    "ch": uuid.uuid4().hex,
                    # uq_doc_tool: (tenant, bot, tool_name) unique.
                    "tool": f"purge-e2e-{str(doc_id)[:8]}",
                },
            )
        for i, doc_id in enumerate([doc_ids[0], doc_ids[0], doc_ids[1]]):
            await session.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        id, record_document_id, record_bot_id,
                        chunk_index, content, content_hash, created_at
                    ) VALUES (
                        :id, :doc, :bid, :idx, 'purge e2e chunk', :ch, now()
                    )
                    """,
                ),
                {
                    "id": uuid.uuid4(), "doc": doc_id,
                    "bid": bot_uuid, "idx": i,
                    "ch": uuid.uuid4().hex,
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO semantic_cache (
                    id, record_tenant_id, record_bot_id, workspace_id,
                    query_hash, answer, created_at
                ) VALUES (
                    :id, :tid, :bid, 'purge-e2e-ws', :qh, 'a', now()
                )
                """,
            ),
            {
                "id": uuid.uuid4(), "tid": tenant_id, "bid": bot_uuid,
                "qh": uuid.uuid4().hex,
            },
        )
        await session.commit()
    return {"tenant_id": tenant_id, "bot_uuid": bot_uuid, "doc_ids": doc_ids}


async def _cleanup(sf: Any, tenant_id: uuid.UUID, bot_uuid: uuid.UUID) -> None:
    """Remove anything the test left behind (idempotent).

    ``audit_log`` is append-only by DB trigger and FK-RESTRICTs the
    tenant row (the exact D11 rationale the ADR defers to) — when the
    test wrote audit rows the tenant row is soft-deleted instead of
    hard-deleted, mirroring production semantics.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415 — cleanup-only

    async with sf() as session:
        await session.execute(
            text("DELETE FROM bots WHERE id = :b"), {"b": bot_uuid},
        )
        await session.execute(
            text("DELETE FROM outbox WHERE record_tenant_id = :t"),
            {"t": tenant_id},
        )
        await session.commit()
    async with sf() as session:
        try:
            await session.execute(
                text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id},
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await session.execute(
                text(
                    "UPDATE tenants SET deleted_at = now() WHERE id = :t",
                ),
                {"t": tenant_id},
            )
            await session.commit()


async def _count(sf: Any, sql: str, params: dict[str, Any]) -> int:
    async with sf() as session:
        result = await session.execute(text(sql), params)
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_purge_bot_zero_orphans_e2e(session_factory: Any) -> None:
    sf = session_factory
    seeded = await _seed(sf)
    tenant_id, bot_uuid = seeded["tenant_id"], seeded["bot_uuid"]

    redis = _RecorderRedis()
    uq_key = f"ragbot:uq:v1:{bot_uuid}:deadbeef"
    emb_key = "ragbot:emb:model:8:cafe"  # shared cache — must SURVIVE
    redis.keys.update({uq_key, emb_key})
    registry = _RecorderRegistry()
    corpus = _RecorderCorpus()

    svc = BotLifecycleService(
        session_factory=sf,
        registry=registry,  # type: ignore[arg-type]
        corpus_version_service=corpus,  # type: ignore[arg-type]
        redis_client=redis,
        tenant_session=session_with_tenant,
        audit_writer=insert_audit_row,
        tenant_repository_factory=TenantRepository,
    )

    try:
        report = await svc.purge_bot(
            bot_uuid,
            record_tenant_id=tenant_id,
            actor_user_id="e2e-admin",
            trace_id="purge-e2e",
        )

        # ── G1: zero orphans across the CASCADE family ──────────────
        assert report.purged is True
        assert report.db_rows_bots == 1
        for table in ("documents", "document_chunks", "semantic_cache",
                      "conversations"):
            orphans = await _count(
                sf,
                f"SELECT count(*) FROM {table} WHERE record_bot_id = :b",  # noqa: S608
                {"b": bot_uuid},
            )
            assert orphans == 0, f"{table} left {orphans} orphan rows"
        assert await _count(
            sf, "SELECT count(*) FROM bots WHERE id = :b", {"b": bot_uuid},
        ) == 0

        # Audit row — action 'purge', chained + tenant-scoped.
        assert await _count(
            sf,
            "SELECT count(*) FROM audit_log WHERE record_tenant_id = :t "
            "AND action = 'purge' AND resource_id = :r",
            {"t": tenant_id, "r": str(bot_uuid)},
        ) == 1
        # Outbox row — bot.purged.v1, same tx as the DELETE.
        assert await _count(
            sf,
            "SELECT count(*) FROM outbox WHERE record_tenant_id = :t "
            "AND subject = :s",
            {"t": tenant_id, "s": SUBJECT_BOT_PURGED},
        ) == 1

        # Redis: uq key gone, SHARED embedding key intact.
        assert uq_key not in redis.keys
        assert emb_key in redis.keys
        assert SKIP_EMBEDDING_CACHE in report.skipped
        assert report.redis_uq_keys == 1
        assert registry.invalidations and registry.invalidations[0][0] == tenant_id
        assert corpus.invalidations == [(tenant_id, bot_uuid)]

        # ── G3: idempotent re-run ───────────────────────────────────
        rerun = await svc.purge_bot(
            bot_uuid,
            record_tenant_id=tenant_id,
            actor_user_id="e2e-admin",
        )
        assert rerun.purged is False
        assert rerun.db_rows_bots == 0
        # Still exactly one audit purge row + one outbox row (no dup).
        assert await _count(
            sf,
            "SELECT count(*) FROM outbox WHERE record_tenant_id = :t "
            "AND subject = :s",
            {"t": tenant_id, "s": SUBJECT_BOT_PURGED},
        ) == 1
    finally:
        await _cleanup(sf, tenant_id, bot_uuid)


@pytest.mark.asyncio
async def test_purge_refuses_live_bot_e2e(session_factory: Any) -> None:
    """Guard on a real row: bot NOT soft-deleted → raise, row survives."""
    sf = session_factory
    seeded = await _seed(sf)
    tenant_id, bot_uuid = seeded["tenant_id"], seeded["bot_uuid"]
    async with sf() as session:
        await session.execute(
            text(
                "UPDATE bots SET is_deleted = false, deleted_at = NULL "
                "WHERE id = :b",
            ),
            {"b": bot_uuid},
        )
        await session.commit()

    svc = BotLifecycleService(
        session_factory=sf,
        registry=_RecorderRegistry(),  # type: ignore[arg-type]
        corpus_version_service=_RecorderCorpus(),  # type: ignore[arg-type]
        redis_client=_RecorderRedis(),
        tenant_session=session_with_tenant,
        audit_writer=insert_audit_row,
        tenant_repository_factory=TenantRepository,
    )
    try:
        with pytest.raises(BotNotPurgeableError):
            await svc.purge_bot(
                bot_uuid, record_tenant_id=tenant_id, actor_user_id="e2e",
            )
        assert await _count(
            sf, "SELECT count(*) FROM bots WHERE id = :b", {"b": bot_uuid},
        ) == 1
    finally:
        await _cleanup(sf, tenant_id, bot_uuid)


@pytest.mark.asyncio
async def test_purge_is_tenant_scoped_e2e(session_factory: Any) -> None:
    """R2/T6 — purging with the WRONG tenant id must delete NOTHING."""
    sf = session_factory
    seeded = await _seed(sf)
    tenant_id, bot_uuid = seeded["tenant_id"], seeded["bot_uuid"]
    other_tenant = uuid.uuid4()
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, "
                "bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now())",
            ),
            {"id": other_tenant, "name": f"purge-e2e-x-{str(other_tenant)[:8]}"},
        )
        await session.commit()

    svc = BotLifecycleService(
        session_factory=sf,
        registry=_RecorderRegistry(),  # type: ignore[arg-type]
        corpus_version_service=_RecorderCorpus(),  # type: ignore[arg-type]
        redis_client=_RecorderRedis(),
        tenant_session=session_with_tenant,
        audit_writer=insert_audit_row,
        tenant_repository_factory=TenantRepository,
    )
    try:
        report = await svc.purge_bot(
            bot_uuid,
            record_tenant_id=other_tenant,  # WRONG tenant
            actor_user_id="e2e",
        )
        assert report.purged is False
        assert report.db_rows_bots == 0
        assert await _count(
            sf, "SELECT count(*) FROM bots WHERE id = :b", {"b": bot_uuid},
        ) == 1  # victim bot untouched
    finally:
        await _cleanup(sf, tenant_id, bot_uuid)
        async with sf() as session:
            await session.execute(
                text("DELETE FROM tenants WHERE id = :t"), {"t": other_tenant},
            )
            await session.commit()
