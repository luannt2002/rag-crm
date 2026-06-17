"""Test BotRegistryService cache + lookup + invalidate (Redis-backed)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_registry_service import BotRegistryService
from tests.conftest import TEST_TENANT_UUID, TEST_TENANT_2_UUID


class FakeRedis:
    """In-memory fake Redis for testing."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: str | bytes, ex: int | None = None) -> None:
        # fake ignores TTL — tests don't exercise expiry
        del ex
        self._store[key] = value if isinstance(value, bytes) else value.encode()

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._store.pop(k, None)
            self._sets.pop(k, None)

    async def sadd(self, key: str, *members: str) -> None:
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].update(members)

    async def srem(self, key: str, *members: str) -> None:
        if key in self._sets:
            self._sets[key] -= set(members)

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    def pipeline(self) -> FakeRedisPipeline:
        return FakeRedisPipeline(self)


class FakeRedisPipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def set(self, key: str, value: str | bytes, ex: int | None = None) -> FakeRedisPipeline:
        self._ops.append(("set", key, value, ex))
        return self

    def sadd(self, key: str, *members: str) -> FakeRedisPipeline:
        self._ops.append(("sadd", key, *members))
        return self

    async def execute(self) -> None:
        for op in self._ops:
            if op[0] == "set":
                # op = ("set", key, value, ex)
                await self._redis.set(op[1], op[2], ex=op[3])
            elif op[0] == "sadd":
                await self._redis.sadd(op[1], *op[2:])


def _make_cfg(bot_id: str, channel_type: str = "api", **kw) -> BotConfig:
    # ``bots.record_tenant_id`` is UUID NOT NULL — default to a stable value
    # so bootstrap reads a complete row. workspace_id mirrors the resolver
    # fallback so fixtures don't depend on any specific literal value.
    rt = kw.get("record_tenant_id", TEST_TENANT_UUID)
    return BotConfig(
        id=uuid4(), bot_id=bot_id, channel_type=channel_type,
        bot_name=kw.get("bot_name", f"Bot {bot_id}"),
        record_tenant_id=rt,
        workspace_id=kw.get("workspace_id", str(rt)),
    )


def _make_service(rows: list[BotConfig]) -> BotRegistryService:
    repo = MagicMock()
    repo.list_active = AsyncMock(return_value=rows)
    repo.find_by_4key = AsyncMock(return_value=None)
    return BotRegistryService(repo=repo, redis_client=FakeRedis())


def test_bootstrap_loads_active_bots():
    rows = [_make_cfg("A"), _make_cfg("B"), _make_cfg("A", "zalo")]
    svc = _make_service(rows)
    count = asyncio.run(svc.bootstrap_cache())
    assert count == 3


def _ws(tenant=TEST_TENANT_UUID) -> str:
    """Default workspace slug used by ``_make_cfg`` matches the tenant UUID."""
    return str(tenant)


def test_lookup_hit():
    cfg = _make_cfg("BOT_X", "zalo", record_tenant_id=TEST_TENANT_UUID)
    svc = _make_service([cfg])
    asyncio.run(svc.bootstrap_cache())
    result = asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "BOT_X", "zalo"))
    assert result is not None
    assert result.bot_id == "BOT_X"


def test_lookup_miss_returns_none():
    svc = _make_service([_make_cfg("A", record_tenant_id=TEST_TENANT_UUID)])
    asyncio.run(svc.bootstrap_cache())
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "NOPE", "api")) is None


def test_lookup_empty_input_returns_none():
    svc = _make_service([_make_cfg("A", record_tenant_id=TEST_TENANT_UUID)])
    asyncio.run(svc.bootstrap_cache())
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "", "api")) is None
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "A", "")) is None
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "  ", "  ")) is None


def test_lookup_trims_whitespace():
    svc = _make_service([_make_cfg("BOT_Y", "api", record_tenant_id=TEST_TENANT_UUID)])
    asyncio.run(svc.bootstrap_cache())
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "BOT_Y ", " api")) is not None


def test_invalidate_removes_deleted_bot():
    cfg = _make_cfg("DEL_ME", record_tenant_id=TEST_TENANT_UUID)
    svc = _make_service([cfg])
    asyncio.run(svc.bootstrap_cache())
    svc._repo.find_by_4key = AsyncMock(return_value=None)
    asyncio.run(svc.invalidate(TEST_TENANT_UUID, _ws(), "DEL_ME", "api"))
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "DEL_ME", "api")) is None


def test_invalidate_reloads_updated_bot():
    old = _make_cfg("CHANGE_ME", bot_name="Old", record_tenant_id=TEST_TENANT_UUID)
    new = BotConfig(id=old.id, bot_id="CHANGE_ME", channel_type="api",
                    bot_name="New", record_tenant_id=TEST_TENANT_UUID,
                    workspace_id=str(TEST_TENANT_UUID))
    svc = _make_service([old])
    asyncio.run(svc.bootstrap_cache())
    svc._repo.find_by_4key = AsyncMock(return_value=new)
    asyncio.run(svc.invalidate(TEST_TENANT_UUID, _ws(), "CHANGE_ME", "api"))
    got = asyncio.run(svc.lookup(TEST_TENANT_UUID, _ws(), "CHANGE_ME", "api"))
    assert got.bot_name == "New"


def test_cache_status_returns_telemetry():
    svc = _make_service([_make_cfg("A"), _make_cfg("B")])
    asyncio.run(svc.bootstrap_cache())
    status = asyncio.run(svc.cache_status())
    assert status["size"] == 2
    assert "last_bootstrap_at" in status
    assert len(status["keys_sample"]) == 2


# ``BotConfig.record_tenant_id`` is UUID REQUIRED — Pydantic rejects null
# tenants before the repo layer ever sees them. Coverage lives in
# ``test_bot_config_record_tenant_id_required_uuid`` (test_bot_config_pydantic.py).


def test_lookup_returns_cached_row_for_tenant():
    """Cache key includes record_tenant_id so two tenants sharing a bot_id
    don't collide.
    """
    cfg_a = _make_cfg("SHARED", record_tenant_id=TEST_TENANT_UUID)
    cfg_b = _make_cfg("SHARED", record_tenant_id=TEST_TENANT_2_UUID)
    svc = _make_service([cfg_a, cfg_b])
    asyncio.run(svc.bootstrap_cache())
    got_a = asyncio.run(svc.lookup(
        TEST_TENANT_UUID, str(TEST_TENANT_UUID), "SHARED", "api",
    ))
    got_b = asyncio.run(svc.lookup(
        TEST_TENANT_2_UUID, str(TEST_TENANT_2_UUID), "SHARED", "api",
    ))
    assert got_a is not None and got_a.record_tenant_id == TEST_TENANT_UUID
    assert got_b is not None and got_b.record_tenant_id == TEST_TENANT_2_UUID
