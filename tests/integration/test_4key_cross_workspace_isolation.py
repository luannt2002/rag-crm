"""Cross-workspace isolation red-team.

A single tenant may legitimately host two workspaces that re-use the
same external ``(bot_id, channel_type)`` slug. The 4-key resolver must
keep them distinct end-to-end:

1. Two ``bots`` rows under the same tenant + same slug but different
   workspace slugs both INSERT cleanly (the unique constraint covers
   the 4-tuple, not the narrower 3-tuple).
2. ``BotRegistryService.lookup`` returns each row by its own workspace
   slug — never the other workspace's row.
3. A lookup with an unregistered workspace slug returns ``None`` (no
   silent fallback to one of the two registered rows).
4. The Redis cache key shape includes the workspace slug, so the two
   rows occupy distinct cache entries and a hit on one cannot serve
   the other's payload.

These are real Postgres + Redis tests. They are gated on the
``integration`` marker and read DSNs from the environment (set by the
test harness or ``set -a && source .env && set +a``); when the env is
missing the suite skips so unit-only runs stay green.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.services.bot_registry_service import (
    REDIS_PREFIX,
    BotRegistryService,
)
from ragbot.infrastructure.cache.redis_cache import create_redis_client
from ragbot.infrastructure.repositories.bot_repository import (
    SqlAlchemyBotRepository,
)


pytestmark = pytest.mark.integration


# ── DB / Redis fixtures (real services) ────────────────────────────────────


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


def _redis_url() -> str | None:
    return os.environ.get("REDIS_URL")


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    dsn = _database_url()
    if not dsn:
        pytest.skip("DATABASE_URL env var required for integration tests")
    engine = create_async_engine(dsn, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


@pytest.fixture()
async def redis_client() -> AsyncIterator[Any]:
    dsn = _redis_url()
    if not dsn:
        pytest.skip("REDIS_URL env var required for integration tests")
    client = create_redis_client(dsn)
    try:
        yield client
    finally:
        await client.close()


# ── Helpers ────────────────────────────────────────────────────────────────


async def _resolve_real_tenant_uuid(sf: Any) -> uuid.UUID:
    """Pick any existing tenants.id so the FK is satisfied.

    The integration env is expected to have at least one tenant row
    seeded; fall back to a fresh INSERT if not.
    """
    async with sf() as session:
        result = await session.execute(
            text("SELECT id FROM tenants ORDER BY created_at LIMIT 1"),
        )
        row = result.first()
        if row is not None:
            return row[0]
        new_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, "
                "bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING",
            ),
            {"id": new_id, "name": f"test-{str(new_id)[:8]}"},
        )
        await session.commit()
        return new_id


async def _insert_bot(
    sf: Any,
    *,
    record_tenant_id: uuid.UUID,
    workspace_id: str,
    bot_id: str,
    channel_type: str,
    bot_name: str = "isolation test bot",
) -> uuid.UUID:
    record_bot_id = uuid.uuid4()
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
                    :id, :rt, :ws, :bot_id, :channel_type,
                    :bot_name, '', false,
                    now(), now(), CAST(:opts AS jsonb),
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                """,
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": workspace_id,
                "bot_id": bot_id,
                "channel_type": channel_type,
                "bot_name": bot_name,
                "opts": json.dumps({}),
            },
        )
        await session.commit()
    return record_bot_id


async def _delete_bots(sf: Any, ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    async with sf() as session:
        await session.execute(
            text("DELETE FROM bots WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await session.commit()


def _unique_slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_workspaces_same_slug_resolve_isolated(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """Same tenant, same ``(bot_id, channel_type)``, two distinct
    workspaces. Each lookup must hit its own row — the second workspace
    cannot inherit the first's bot config or system prompt.
    """
    record_tenant_id = await _resolve_real_tenant_uuid(session_factory)
    bot_slug = _unique_slug("support")
    channel_type = "web"
    ws_a = _unique_slug("sales")
    ws_b = _unique_slug("marketing")

    bot_a = await _insert_bot(
        session_factory,
        record_tenant_id=record_tenant_id, workspace_id=ws_a,
        bot_id=bot_slug, channel_type=channel_type, bot_name="bot-A",
    )
    bot_b = await _insert_bot(
        session_factory,
        record_tenant_id=record_tenant_id, workspace_id=ws_b,
        bot_id=bot_slug, channel_type=channel_type, bot_name="bot-B",
    )
    assert bot_a != bot_b, "two inserts must yield distinct PKs"

    inserted = [bot_a, bot_b]
    keys_to_clean: list[str] = []
    try:
        repo = SqlAlchemyBotRepository(session_factory=session_factory)
        registry = BotRegistryService(repo=repo, redis_client=redis_client)

        cfg_a = await registry.lookup(
            record_tenant_id, ws_a, bot_slug, channel_type,
        )
        cfg_b = await registry.lookup(
            record_tenant_id, ws_b, bot_slug, channel_type,
        )
        assert cfg_a is not None
        assert cfg_b is not None
        assert cfg_a.id == bot_a
        assert cfg_b.id == bot_b
        assert cfg_a.id != cfg_b.id, (
            "the resolver must NOT collapse two workspace rows into one"
        )
        assert cfg_a.workspace_id == ws_a
        assert cfg_b.workspace_id == ws_b
        assert cfg_a.bot_name == "bot-A"
        assert cfg_b.bot_name == "bot-B"

        # Unregistered workspace must not fall through to either row.
        cfg_missing = await registry.lookup(
            record_tenant_id, _unique_slug("nowhere"),
            bot_slug, channel_type,
        )
        assert cfg_missing is None

        # Cache keys must be distinct per workspace; capture them for
        # cleanup AND to verify the registry actually wrote both.
        key_a = registry._key(record_tenant_id, ws_a, bot_slug, channel_type)
        key_b = registry._key(record_tenant_id, ws_b, bot_slug, channel_type)
        assert key_a != key_b
        cached_a = await redis_client.get(key_a)
        cached_b = await redis_client.get(key_b)
        assert cached_a is not None
        assert cached_b is not None
        assert cached_a != cached_b, (
            "two workspaces must occupy distinct cache payloads"
        )
        keys_to_clean = [key_a, key_b]
    finally:
        for k in keys_to_clean:
            try:
                await redis_client.delete(k)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                pytest.fail(f"redis cleanup failed unexpectedly: {exc!r}")
        await _delete_bots(session_factory, inserted)


@pytest.mark.asyncio
async def test_cache_key_carries_workspace_slug(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """Defence-in-depth: a stripped or reordered cache key would
    silently fall back across workspaces. Pin the key shape against
    the live registry instance.
    """
    record_tenant_id = await _resolve_real_tenant_uuid(session_factory)
    bot_slug = _unique_slug("ops")
    channel_type = "web"
    ws = _unique_slug("ops-team")
    bot_id = await _insert_bot(
        session_factory,
        record_tenant_id=record_tenant_id, workspace_id=ws,
        bot_id=bot_slug, channel_type=channel_type,
    )
    key: str | None = None
    try:
        repo = SqlAlchemyBotRepository(session_factory=session_factory)
        registry = BotRegistryService(repo=repo, redis_client=redis_client)
        await registry.lookup(record_tenant_id, ws, bot_slug, channel_type)
        key = registry._key(record_tenant_id, ws, bot_slug, channel_type)
        assert key.startswith(f"{REDIS_PREFIX}:")
        # 4 colon-separated segments after the prefix; workspace slug in
        # the second slot.
        suffix = key[len(REDIS_PREFIX) + 1:]
        parts = suffix.split(":")
        assert parts[0] == str(record_tenant_id)
        assert parts[1] == ws
        assert parts[2] == bot_slug
        assert parts[3] == channel_type
        # Cache must hold a payload at this exact key.
        raw = await redis_client.get(key)
        assert raw is not None
    finally:
        if key is not None:
            try:
                await redis_client.delete(key)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                pytest.fail(f"redis cleanup failed unexpectedly: {exc!r}")
        await _delete_bots(session_factory, [bot_id])
