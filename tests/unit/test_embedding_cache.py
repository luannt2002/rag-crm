"""Unit tests for ``shared.embedding_cache``.

P18 lesson: cache key MUST scope by (model, dim) so a model swap can't
return stale vectors with mismatched dimension. These tests pin that
contract + the fail-soft behavior expected when Redis is misbehaving.
"""

from __future__ import annotations

import json

import pytest
from redis.exceptions import RedisError

from ragbot.shared.embedding_cache import (
    _cache_key,
    get_cached_embedding,
    set_cached_embedding,
)


class _FakeRedis:
    def __init__(self, *, get_raises: Exception | None = None,
                 set_raises: Exception | None = None) -> None:
        self.store: dict[str, str] = {}
        self.last_set_ttl: int | None = None
        self._get_raises = get_raises
        self._set_raises = set_raises

    async def get(self, key: str) -> str | None:
        if self._get_raises is not None:
            raise self._get_raises
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        if self._set_raises is not None:
            raise self._set_raises
        self.store[key] = value
        self.last_set_ttl = ex


def test_cache_key_scopes_model_and_dim() -> None:
    k_a = _cache_key("hello", model="model-a", dim=1024)
    k_b = _cache_key("hello", model="model-b", dim=1024)
    k_c = _cache_key("hello", model="model-a", dim=1536)
    same = _cache_key("hello", model="model-a", dim=1024)

    assert k_a != k_b, "different model must produce different key"
    assert k_a != k_c, "different dim must produce different key"
    assert k_a == same, "same inputs must be deterministic"
    assert k_a.startswith("ragbot:emb:")


def test_cache_key_sanitizes_model_separator() -> None:
    # Embedded ":" / spaces in model name must not break the key shape.
    safe = _cache_key("text", model="provider:model name", dim=512)
    # The model segment must not contain a stray colon between sub-parts.
    body = safe[len("ragbot:emb:") :]
    # Format: "<provider>:<model>:<dim>:<hash>" — exactly three ":" separators.
    assert body.count(":") == 3


async def test_get_set_round_trip_returns_same_vector() -> None:
    redis = _FakeRedis()
    vec = [0.1, 0.2, 0.3, 0.4]

    await set_cached_embedding(redis, "hello", vec, model="m", dim=4)
    out = await get_cached_embedding(redis, "hello", model="m", dim=4)

    assert out == vec
    assert redis.last_set_ttl == 30 * 24 * 3600


async def test_get_returns_none_on_redis_error() -> None:
    redis = _FakeRedis(get_raises=RedisError("boom"))
    # Pre-populate is irrelevant — error path must just return None.
    out = await get_cached_embedding(redis, "any", model="m", dim=1)
    assert out is None


async def test_set_swallows_redis_error_silently() -> None:
    redis = _FakeRedis(set_raises=RedisError("boom"))
    # Caller should not have to handle cache failures.
    await set_cached_embedding(redis, "x", [0.1, 0.2], model="m", dim=2)
    # Store stays empty on error.
    assert redis.store == {}


@pytest.mark.parametrize(
    "redis,vec",
    [
        (None, [0.1, 0.2]),               # no redis client at all
        (_FakeRedis(), []),                # empty embedding (skip)
    ],
)
async def test_set_no_op_paths(redis, vec) -> None:
    # Should never raise; if redis present, store remains empty.
    await set_cached_embedding(redis, "k", vec, model="m", dim=2)
    if redis is not None:
        assert redis.store == {}


async def test_get_returns_none_when_redis_is_none() -> None:
    out = await get_cached_embedding(None, "hello", model="m", dim=1)
    assert out is None


async def test_set_falls_back_to_len_when_dim_zero() -> None:
    # dim=0 + 3-element vec -> stored under dim=3 key. Get with dim=3 hits.
    redis = _FakeRedis()
    vec = [0.1, 0.2, 0.3]
    await set_cached_embedding(redis, "x", vec, model="m", dim=0)
    out = await get_cached_embedding(redis, "x", model="m", dim=3)
    assert out == vec


async def test_corrupted_payload_returns_none() -> None:
    # Whatever ends up in Redis must be JSON-parsable. A broken value
    # (manual write / partial flush) must NOT raise to the caller.
    redis = _FakeRedis()
    key = _cache_key("corrupt", model="m", dim=2)
    redis.store[key] = "not-json{"
    out = await get_cached_embedding(redis, "corrupt", model="m", dim=2)
    assert out is None


async def test_round_trip_uses_json_payload() -> None:
    # Pin the on-the-wire format so cross-language consumers can rely on it.
    redis = _FakeRedis()
    vec = [0.1, 0.5]
    await set_cached_embedding(redis, "y", vec, model="m", dim=2)
    raw = next(iter(redis.store.values()))
    assert json.loads(raw) == vec
