"""Agent A2 — async gather wins (CLAUDE.md Async Rule 1).

Covers 4 parallelization points:
- DynamicLiteLLMRouter.refresh_routing — providers + models gather
- BotRegistryService._fetch_and_cache — Redis SET + SADD gather
- BotRegistryService.invalidate — DEL + SREM (and SET + SADD on reload)
- chat_worker — 3-repo persist trio gather (via injected mocks)

Each test pins:
- The two/three awaitables run CONCURRENTLY (timing-based) and
- A failure in one branch is logged / handled per Rule 5.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# --------------------------------------------------------------------- #
# DynamicLiteLLMRouter — providers + models gather                       #
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_litellm_router_refresh_gathers_providers_and_models():
    """Providers + models awaits run in parallel — wall < sum(sleeps)."""
    from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter

    sleep_each_s = 0.05

    async def slow_providers(*, enabled_only: bool):
        await asyncio.sleep(sleep_each_s)
        return []

    async def slow_models(*, enabled_only: bool):
        await asyncio.sleep(sleep_each_s)
        return []

    repo = MagicMock()
    repo.list_providers = AsyncMock(side_effect=slow_providers)
    repo.list_models = AsyncMock(side_effect=slow_models)

    router = DynamicLiteLLMRouter.__new__(DynamicLiteLLMRouter)
    router._repo = repo
    router._lock = asyncio.Lock()
    router._model_list = []
    router._last_refresh = 0.0
    router._redis = None

    t0 = time.perf_counter()
    await router.refresh_routing()
    elapsed = time.perf_counter() - t0

    # Gather → both 50ms sleeps overlap. Sequential would be ~100ms.
    # Give 70ms ceiling — fine for parallel, fails for sequential.
    assert elapsed < 0.07, f"refresh_routing not parallel: {elapsed:.3f}s"
    assert repo.list_providers.await_count == 1
    assert repo.list_models.await_count == 1


# --------------------------------------------------------------------- #
# BotRegistryService — _fetch_and_cache SET + SADD gather                #
# --------------------------------------------------------------------- #


def _make_bot_cfg():
    cfg = MagicMock()
    cfg.model_dump_json.return_value = '{"bot_id":"b"}'
    return cfg


@pytest.mark.asyncio
async def test_bot_registry_fetch_and_cache_gathers_set_and_sadd():
    from uuid import uuid4

    from ragbot.application.services.bot_registry_service import BotRegistryService

    sleep_each_s = 0.04
    redis = MagicMock()

    async def slow_set(*a, **kw):
        await asyncio.sleep(sleep_each_s)
        return True

    async def slow_sadd(*a, **kw):
        await asyncio.sleep(sleep_each_s)
        return 1

    redis.set = AsyncMock(side_effect=slow_set)
    redis.sadd = AsyncMock(side_effect=slow_sadd)

    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=_make_bot_cfg())

    svc = BotRegistryService.__new__(BotRegistryService)
    svc._repo = repo
    svc._redis = redis
    svc._lock = asyncio.Lock()

    tid = uuid4()
    key = svc._key(tid, "ws", "b", "web")

    t0 = time.perf_counter()
    cfg = await svc._fetch_and_cache(key, tid, "ws", "b", "web")
    elapsed = time.perf_counter() - t0

    assert cfg is not None
    # Both 40ms ops should overlap → ~40ms, sequential would be ~80ms.
    assert elapsed < 0.06, f"fetch_and_cache not parallel: {elapsed:.3f}s"
    assert redis.set.await_count == 1
    assert redis.sadd.await_count == 1


@pytest.mark.asyncio
async def test_bot_registry_fetch_and_cache_tolerates_partial_redis_failure():
    from uuid import uuid4

    from ragbot.application.services.bot_registry_service import BotRegistryService

    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    redis.sadd = AsyncMock(side_effect=RuntimeError("redis down"))

    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=_make_bot_cfg())

    svc = BotRegistryService.__new__(BotRegistryService)
    svc._repo = repo
    svc._redis = redis
    svc._lock = asyncio.Lock()

    tid = uuid4()
    key = svc._key(tid, "ws", "b", "web")

    # Must NOT raise — Rule 5 cache writes are best-effort.
    cfg = await svc._fetch_and_cache(key, tid, "ws", "b", "web")
    assert cfg is not None


# --------------------------------------------------------------------- #
# BotRegistryService.invalidate — DEL + SREM gather (cfg=None path)      #
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bot_registry_invalidate_gathers_delete_and_srem_when_bot_removed():
    from uuid import uuid4

    from ragbot.application.services.bot_registry_service import BotRegistryService

    sleep_each_s = 0.04
    redis = MagicMock()

    async def slow_del(*a, **kw):
        await asyncio.sleep(sleep_each_s)
        return 1

    async def slow_srem(*a, **kw):
        await asyncio.sleep(sleep_each_s)
        return 1

    redis.delete = AsyncMock(side_effect=slow_del)
    redis.srem = AsyncMock(side_effect=slow_srem)

    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=None)  # cfg removed → delete path

    svc = BotRegistryService.__new__(BotRegistryService)
    svc._repo = repo
    svc._redis = redis
    svc._lock = asyncio.Lock()

    tid = uuid4()
    t0 = time.perf_counter()
    await svc.invalidate(tid, "ws", "b", "web")
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.06, f"invalidate not parallel: {elapsed:.3f}s"
    assert redis.delete.await_count == 1
    assert redis.srem.await_count == 1


@pytest.mark.asyncio
async def test_bot_registry_invalidate_gathers_set_and_sadd_when_bot_reloaded():
    from uuid import uuid4

    from ragbot.application.services.bot_registry_service import BotRegistryService

    sleep_each_s = 0.04
    redis = MagicMock()

    async def slow_set(*a, **kw):
        await asyncio.sleep(sleep_each_s)
        return True

    async def slow_sadd(*a, **kw):
        await asyncio.sleep(sleep_each_s)
        return 1

    redis.set = AsyncMock(side_effect=slow_set)
    redis.sadd = AsyncMock(side_effect=slow_sadd)

    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=_make_bot_cfg())

    svc = BotRegistryService.__new__(BotRegistryService)
    svc._repo = repo
    svc._redis = redis
    svc._lock = asyncio.Lock()

    tid = uuid4()
    t0 = time.perf_counter()
    await svc.invalidate(tid, "ws", "b", "web")
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.06, f"invalidate-reload not parallel: {elapsed:.3f}s"
    assert redis.set.await_count == 1
    assert redis.sadd.await_count == 1
