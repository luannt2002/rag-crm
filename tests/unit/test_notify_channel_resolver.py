"""Unit tests for ``NotifyChannelResolver``.

Covers the full resolution chain — DB row → env fallback → none —
plus Redis cache hit / miss / invalidate semantics. The DB + Redis +
Settings layers are stubbed so the suite runs without infrastructure.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ragbot.application.dto.notify_channel import NotifyChannelConfig
from ragbot.application.services.notify_channel_resolver import (
    NotifyChannelResolver,
)
from ragbot.shared.constants import NOTIFY_CHANNEL_CONFIG_KEY


_VALID_DICT = {
    "method": "POST",
    "domain": "https://example.com",
    "path_template": "/hooks/{conversation_id}/in",
    "conversation_id": "conv-1",
    "webhook_key": "whk_unit_test_key_12345",
    "enabled": True,
}


class _FakeRedis:
    """Minimal async Redis double — GET / SET / DELETE only."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.delete_calls: list[str] = []

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.delete_calls.append(key)
        return 1 if self._store.pop(key, None) is not None else 0


class _FakeSystemConfigService:
    def __init__(self, value=None) -> None:
        self.value = value
        self.get_calls: list[str] = []

    async def get(self, key: str, default=None):
        self.get_calls.append(key)
        if self.value is None:
            return default
        return self.value


@pytest.mark.asyncio
async def test_resolver_db_first_returns_db_source():
    redis = _FakeRedis()
    scs = _FakeSystemConfigService(value=_VALID_DICT)
    env = SimpleNamespace(notify_channel_config=None)
    resolver = NotifyChannelResolver(
        system_config_service=scs, redis_client=redis, env_settings=env,
    )

    cfg, source = await resolver.resolve()

    assert isinstance(cfg, NotifyChannelConfig)
    assert source == "db"
    assert scs.get_calls == [NOTIFY_CHANNEL_CONFIG_KEY]
    assert cfg.conversation_id == "conv-1"


@pytest.mark.asyncio
async def test_resolver_falls_back_to_env_when_db_absent():
    redis = _FakeRedis()
    scs = _FakeSystemConfigService(value=None)
    env = SimpleNamespace(notify_channel_config=_VALID_DICT)
    resolver = NotifyChannelResolver(
        system_config_service=scs, redis_client=redis, env_settings=env,
    )

    cfg, source = await resolver.resolve()

    assert source == "env"
    assert cfg is not None
    assert cfg.webhook_key.startswith("whk_unit_test")


@pytest.mark.asyncio
async def test_resolver_returns_none_when_no_source():
    redis = _FakeRedis()
    scs = _FakeSystemConfigService(value=None)
    env = SimpleNamespace(notify_channel_config=None)
    resolver = NotifyChannelResolver(
        system_config_service=scs, redis_client=redis, env_settings=env,
    )

    cfg, source = await resolver.resolve()

    assert cfg is None
    assert source == "none"


@pytest.mark.asyncio
async def test_resolver_uses_redis_cache_on_second_call():
    redis = _FakeRedis()
    scs = _FakeSystemConfigService(value=_VALID_DICT)
    env = SimpleNamespace(notify_channel_config=None)
    resolver = NotifyChannelResolver(
        system_config_service=scs, redis_client=redis, env_settings=env,
    )

    # First call hits DB; second call must hit Redis cache.
    await resolver.resolve()
    cfg2, source2 = await resolver.resolve()

    assert source2 == "db"
    assert cfg2 is not None
    # Only the first call should reach the system_config service.
    assert len(scs.get_calls) == 1


@pytest.mark.asyncio
async def test_resolver_invalidate_drops_cache():
    redis = _FakeRedis()
    scs = _FakeSystemConfigService(value=_VALID_DICT)
    env = SimpleNamespace(notify_channel_config=None)
    resolver = NotifyChannelResolver(
        system_config_service=scs, redis_client=redis, env_settings=env,
    )

    await resolver.resolve()  # populates cache
    await resolver.invalidate()

    # After invalidate Redis should not hold the key any more.
    assert "ragbot:notify_channel:config" not in redis._store
    # Next resolve hits DB again (call count increments).
    await resolver.resolve()
    assert len(scs.get_calls) == 2


@pytest.mark.asyncio
async def test_resolver_skips_disabled_db_value_falls_back_to_env():
    """When the DB row is invalid (missing required field), env wins."""
    redis = _FakeRedis()
    bad_db = dict(_VALID_DICT)
    del bad_db["webhook_key"]  # invalid — missing required secret
    scs = _FakeSystemConfigService(value=bad_db)
    env = SimpleNamespace(notify_channel_config=_VALID_DICT)
    resolver = NotifyChannelResolver(
        system_config_service=scs, redis_client=redis, env_settings=env,
    )

    cfg, source = await resolver.resolve()

    assert cfg is not None
    assert source == "env"
