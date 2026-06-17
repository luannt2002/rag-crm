"""DB stampede single-flight integration tests.

Coverage matrix (all three single-flight caches: ``rbac``, ``bot_registry``,
``tenant_config``):

1. Cold cache + N concurrent identical lookups → exactly 1 backing-store
   query fires (the single-flight contract).
2. Cold cache + N concurrent **distinct** lookups → N backing-store
   queries fire (no false coalescing across keys).
3. Warm cache reads → 0 backing-store queries.
4. Backing-store error → lock releases, next caller can re-attempt
   (no deadlock).
5. Lock cleanup — bounded LRU eviction stops unbounded growth under a
   tenant explosion.

Each cache exercises the ``AsyncSingleFlight`` helper through its real
public API (``BotRegistryService.lookup``, ``TenantConfigCache.get``,
``rbac._load_permissions``). The backing store is mocked with a counter
so tests directly assert "DB query count = 1" rather than indirect
proxies.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request
from starlette.types import Scope

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.application.services.tenant_config_cache import (
    TenantConfigCache,
    TenantRuntimeConfig,
)
from ragbot.interfaces.http.middlewares import rbac as rbac_mod
from ragbot.shared.constants import DEFAULT_SINGLE_FLIGHT_MAX_LOCKS
from ragbot.shared.single_flight import AsyncSingleFlight
from tests.conftest import TEST_TENANT_UUID, TEST_TENANT_2_UUID


# --------------------------------------------------------------------------- #
# Shared in-memory fakes — kept tiny so tests stay focused on the lock logic. #
# --------------------------------------------------------------------------- #


class FakeRedis:
    """Async in-memory Redis stand-in — covers the API surface used here."""

    def __init__(self) -> None:
        self._kv: dict[str, bytes] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> bytes | None:
        return self._kv.get(key)

    async def set(
        self,
        key: str,
        value: str | bytes,
        ex: int | None = None,
    ) -> None:
        del ex
        self._kv[key] = value if isinstance(value, bytes) else value.encode()

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._kv.pop(k, None)
            self._sets.pop(k, None)

    async def sadd(self, key: str, *members: str) -> None:
        self._sets.setdefault(key, set()).update(members)

    async def srem(self, key: str, *members: str) -> None:
        if key in self._sets:
            self._sets[key] -= set(members)

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())


# --------------------------------------------------------------------------- #
# 1) BotRegistryService                                                       #
# --------------------------------------------------------------------------- #


def _make_bot_cfg(
    bot_id: str = "bot-a",
    channel_type: str = "web",
    record_tenant_id: UUID = TEST_TENANT_UUID,
) -> BotConfig:
    return BotConfig(
        id=uuid4(),
        bot_id=bot_id,
        channel_type=channel_type,
        bot_name=f"Bot {bot_id}",
        record_tenant_id=record_tenant_id,
        workspace_id=str(record_tenant_id),
    )


def _make_bot_registry(
    *,
    db_call_counter: dict[str, int],
    db_delay_s: float = 0.0,
    raise_first: bool = False,
    cfg_factory: Any = None,
) -> BotRegistryService:
    redis = FakeRedis()
    repo = MagicMock()

    cfg_factory = cfg_factory or _make_bot_cfg

    async def find_by_4key(
        record_tenant_id, workspace_id, bot_id, channel_type,
    ):
        db_call_counter["count"] = db_call_counter.get("count", 0) + 1
        if raise_first and db_call_counter["count"] == 1:
            raise RuntimeError("simulated DB blip")
        if db_delay_s:
            await asyncio.sleep(db_delay_s)
        return cfg_factory(
            bot_id=bot_id,
            channel_type=channel_type,
            record_tenant_id=record_tenant_id,
        )

    repo.find_by_4key = AsyncMock(side_effect=find_by_4key)
    return BotRegistryService(repo=repo, redis_client=redis)


# Workspace slug used by every lookup below; mirrors the resolver fallback
# (``str(record_tenant_id)``) so fixture rows don't depend on a specific
# tenant-supplied literal.
_WS = str(TEST_TENANT_UUID)


@pytest.mark.asyncio
async def test_bot_registry_concurrent_same_key_one_db_query() -> None:
    """50 concurrent cold misses on the same identity tuple collapse to a
    single DB query under the registry's single-flight guard.
    """
    counter: dict[str, int] = {}
    svc = _make_bot_registry(db_call_counter=counter, db_delay_s=0.05)

    results = await asyncio.gather(*[
        svc.lookup(TEST_TENANT_UUID, _WS, "bot-a", "web") for _ in range(50)
    ])

    assert all(r is not None for r in results)
    assert all(r.bot_id == "bot-a" for r in results)
    assert counter["count"] == 1, f"expected 1 DB query, got {counter['count']}"


@pytest.mark.asyncio
async def test_bot_registry_concurrent_distinct_keys_no_false_coalesce() -> None:
    """50 concurrent cold misses across 50 distinct identity tuples must
    issue one DB query each — single-flight does not collapse unrelated
    keys.
    """
    counter: dict[str, int] = {}
    svc = _make_bot_registry(db_call_counter=counter)

    results = await asyncio.gather(*[
        svc.lookup(TEST_TENANT_UUID, _WS, f"bot-{i}", "web") for i in range(50)
    ])

    assert all(r is not None for r in results)
    assert counter["count"] == 50


@pytest.mark.asyncio
async def test_bot_registry_warm_cache_no_db_query() -> None:
    """Warm-cache reads issue zero DB queries."""
    counter: dict[str, int] = {}
    svc = _make_bot_registry(db_call_counter=counter)

    # First call warms the cache.
    await svc.lookup(TEST_TENANT_UUID, _WS, "bot-a", "web")
    assert counter["count"] == 1

    # 20 follow-up reads must all come from Redis.
    results = await asyncio.gather(*[
        svc.lookup(TEST_TENANT_UUID, _WS, "bot-a", "web") for _ in range(20)
    ])
    assert all(r is not None for r in results)
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_bot_registry_db_error_releases_lock_for_retry() -> None:
    """A DB error must release the lock so the next caller can retry."""
    counter: dict[str, int] = {}
    svc = _make_bot_registry(db_call_counter=counter, raise_first=True)

    with pytest.raises(RuntimeError, match="simulated DB blip"):
        await svc.lookup(TEST_TENANT_UUID, _WS, "bot-a", "web")

    # Second call must succeed — proves the lock released after the raise.
    cfg = await svc.lookup(TEST_TENANT_UUID, _WS, "bot-a", "web")
    assert cfg is not None
    assert counter["count"] == 2


# --------------------------------------------------------------------------- #
# 2) TenantConfigCache                                                        #
# --------------------------------------------------------------------------- #


def _make_tenant_config_cache(
    *,
    db_call_counter: dict[str, int],
    db_delay_s: float = 0.0,
    raise_first: bool = False,
    cfg: TenantRuntimeConfig | None = None,
) -> TenantConfigCache:
    """Wire the cache against fakes; patch ``_load_from_db`` directly."""
    redis = FakeRedis()
    cache = TenantConfigCache(
        session_factory=AsyncMock(),  # never invoked — _load_from_db patched
        redis_client=redis,
    )

    cfg = cfg or TenantRuntimeConfig(
        bypass_rate_limit=False,
        rate_limit_per_min=60,
        monthly_token_cap=None,
    )

    async def fake_load(record_tenant_id):  # type: ignore[no-untyped-def]
        db_call_counter["count"] = db_call_counter.get("count", 0) + 1
        if raise_first and db_call_counter["count"] == 1:
            raise RuntimeError("simulated DB blip")
        if db_delay_s:
            await asyncio.sleep(db_delay_s)
        return cfg

    cache._load_from_db = fake_load  # type: ignore[assignment]
    return cache


@pytest.mark.asyncio
async def test_tenant_cfg_concurrent_same_tenant_one_db_query() -> None:
    counter: dict[str, int] = {}
    cache = _make_tenant_config_cache(
        db_call_counter=counter, db_delay_s=0.05,
    )

    results = await asyncio.gather(*[
        cache.get(TEST_TENANT_UUID) for _ in range(50)
    ])

    assert all(r is not None for r in results)
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_tenant_cfg_distinct_tenants_no_false_coalesce() -> None:
    counter: dict[str, int] = {}
    cache = _make_tenant_config_cache(db_call_counter=counter)

    # 2 distinct tenants × multiple repeated calls should produce 2 DB queries.
    await asyncio.gather(
        cache.get(TEST_TENANT_UUID),
        cache.get(TEST_TENANT_2_UUID),
        cache.get(TEST_TENANT_UUID),  # warm cache after first
        cache.get(TEST_TENANT_2_UUID),
    )
    assert counter["count"] == 2


@pytest.mark.asyncio
async def test_tenant_cfg_warm_cache_no_db_query() -> None:
    counter: dict[str, int] = {}
    cache = _make_tenant_config_cache(db_call_counter=counter)

    await cache.get(TEST_TENANT_UUID)
    assert counter["count"] == 1

    results = await asyncio.gather(*[
        cache.get(TEST_TENANT_UUID) for _ in range(20)
    ])
    assert all(r is not None for r in results)
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_tenant_cfg_db_error_releases_lock_for_retry() -> None:
    counter: dict[str, int] = {}
    cache = _make_tenant_config_cache(
        db_call_counter=counter, raise_first=True,
    )

    with pytest.raises(RuntimeError, match="simulated DB blip"):
        await cache.get(TEST_TENANT_UUID)

    cfg = await cache.get(TEST_TENANT_UUID)
    assert cfg is not None
    assert counter["count"] == 2


# --------------------------------------------------------------------------- #
# 3) RBAC permissions cache                                                   #
# --------------------------------------------------------------------------- #


def _make_request_with_redis(redis: FakeRedis, session_factory: Any) -> Request:
    """Hand-build a ``starlette.Request`` with the deps RBAC reads."""
    container = MagicMock()
    container.redis_client = MagicMock(return_value=redis)
    container.session_factory = MagicMock(return_value=session_factory)

    app = MagicMock()
    app.state.container = container

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "headers": [],
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "app": app,
    }
    req = Request(scope)
    req._send = AsyncMock()  # type: ignore[attr-defined]
    return req


class _FakeSessionFactory:
    """Async-context yielding a session whose ``execute`` returns DB rows."""

    def __init__(self, rows: list[tuple[str, str, int]], counter: dict[str, int]):
        self._rows = rows
        self._counter = counter

    def __call__(self):  # session_factory() returns the cm
        return self

    async def __aenter__(self):
        self._counter["count"] = self._counter.get("count", 0) + 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_args, **_kwargs):
        result = MagicMock()
        result.fetchall = MagicMock(return_value=list(self._rows))
        return result


@pytest.fixture(autouse=True)
def _reset_rbac_singleflight() -> None:
    """Reset the module-level singleton between tests for isolation."""
    rbac_mod._RBAC_SINGLE_FLIGHT = AsyncSingleFlight("rbac")


@pytest.mark.asyncio
async def test_rbac_concurrent_cold_cache_one_db_query() -> None:
    redis = FakeRedis()
    counter: dict[str, int] = {}
    sf = _FakeSessionFactory(
        rows=[("bot", "create", 60), ("bot", "delete", 80)],
        counter=counter,
    )
    requests = [_make_request_with_redis(redis, sf) for _ in range(50)]

    results = await asyncio.gather(*[
        rbac_mod._load_permissions(r) for r in requests
    ])
    assert all(isinstance(r, dict) for r in results)
    assert all(r.get("bot:create") == 60 for r in results)
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_rbac_warm_cache_no_db_query() -> None:
    redis = FakeRedis()
    counter: dict[str, int] = {}
    sf = _FakeSessionFactory(
        rows=[("bot", "create", 60)],
        counter=counter,
    )
    req = _make_request_with_redis(redis, sf)
    await rbac_mod._load_permissions(req)
    assert counter["count"] == 1

    # 20 follow-up reads — Redis hit, no DB.
    results = await asyncio.gather(*[
        rbac_mod._load_permissions(req) for _ in range(20)
    ])
    assert all(isinstance(r, dict) for r in results)
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_rbac_db_error_releases_lock_for_retry() -> None:
    redis = FakeRedis()
    counter: dict[str, int] = {}

    class FailingFactory(_FakeSessionFactory):
        async def execute(self, *_args, **_kwargs):
            if self._counter["count"] == 1:
                raise RuntimeError("simulated DB blip")
            return await super().execute()

    sf = FailingFactory(
        rows=[("bot", "create", 60)],
        counter=counter,
    )
    req = _make_request_with_redis(redis, sf)

    with pytest.raises(RuntimeError, match="simulated DB blip"):
        await rbac_mod._load_permissions(req)

    perms = await rbac_mod._load_permissions(req)
    assert perms.get("bot:create") == 60
    assert counter["count"] == 2


# --------------------------------------------------------------------------- #
# 4) Lock-cleanup contract                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_single_flight_lock_dict_bounded_under_burst() -> None:
    """LRU sweep keeps the lock dict at or under the configured cap."""
    sf = AsyncSingleFlight("burst_test", max_locks=8)

    # Insert > cap distinct keys; helper must stay bounded.
    for i in range(100):
        lock = await sf.get_lock(f"key-{i}")
        # Don't hold it — the LRU sweep is allowed to reclaim it.
        assert not lock.locked()

    assert len(sf) <= 8, f"expected ≤ 8 locks, got {len(sf)}"


@pytest.mark.asyncio
async def test_single_flight_locked_entries_not_evicted() -> None:
    """Locked entries (active waiter) must survive the LRU sweep."""
    sf = AsyncSingleFlight("locked_safe", max_locks=4)

    # Hold one lock; flood with other keys that should evict around it.
    held = await sf.get_lock("hot")
    await held.acquire()
    try:
        for i in range(50):
            await sf.get_lock(f"cold-{i}")
        # The "hot" key must still be present.
        assert "hot" in sf._locks
    finally:
        held.release()


@pytest.mark.asyncio
async def test_single_flight_default_max_locks_constant() -> None:
    """Helper picks up the constant default — guard against accidental change."""
    sf = AsyncSingleFlight("default_check")
    assert sf._max_locks == DEFAULT_SINGLE_FLIGHT_MAX_LOCKS


@pytest.mark.asyncio
async def test_single_flight_rejects_empty_label() -> None:
    """Empty cache_label is a programmer error; the helper rejects it."""
    with pytest.raises(ValueError, match="cache_label"):
        AsyncSingleFlight("")
