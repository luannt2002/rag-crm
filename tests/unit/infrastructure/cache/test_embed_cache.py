"""Unit tests for ``infrastructure.cache.embed_cache``.

S5 Pipeline-Opt: class-based, model-scoped Redis cache for query
embeddings. Key contract: ``ragbot:embed:{model}:{sha256(query)[:16]}``.
No bot scope (same text + same model = identical embedding across bots).
"""

from __future__ import annotations

import hashlib
import json

import pytest
from redis.exceptions import RedisError

from ragbot.infrastructure.cache.embed_cache import EmbedCache


class _FakeRedis:
    def __init__(
        self,
        *,
        get_raises: Exception | None = None,
        setex_raises: Exception | None = None,
    ) -> None:
        self.store: dict[str, str] = {}
        self.last_setex_ttl: int | None = None
        self._get_raises = get_raises
        self._setex_raises = setex_raises

    async def get(self, key: str):
        if self._get_raises is not None:
            raise self._get_raises
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self._setex_raises is not None:
            raise self._setex_raises
        self.store[key] = value
        self.last_setex_ttl = int(ttl)


def _expected_key(model: str, query: str) -> str:
    safe = model.replace(":", "_").replace(" ", "_") or "unknown"
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return f"ragbot:embed:{safe}:{h}"


def test_key_shape_includes_model_and_query_hash() -> None:
    cache = EmbedCache(_FakeRedis())
    assert cache._key("hello", model="model-a") == _expected_key(
        "model-a", "hello",
    )


def test_key_changes_on_model_swap() -> None:
    cache = EmbedCache(_FakeRedis())
    assert cache._key("hello", model="model-a") != cache._key(
        "hello", model="model-b",
    )


def test_key_sanitizes_model_separators() -> None:
    # Embedded ':' / spaces in the model id must not corrupt key shape.
    cache = EmbedCache(_FakeRedis())
    key = cache._key("hello", model="provider:model name")
    body = key[len("ragbot:embed:") :]
    # Format: "<safe_model>:<hash>" — exactly one ':' separator.
    assert body.count(":") == 1


def test_key_no_bot_scope_so_cross_bot_reuse_works() -> None:
    # Embeddings for the same text + model are identical regardless of
    # which bot issues the query. Reusing the same key across bots is
    # the intended optimisation.
    cache = EmbedCache(_FakeRedis())
    k_same_model = cache._key("same text", model="m")
    k_again = cache._key("same text", model="m")
    assert k_same_model == k_again


async def test_get_returns_none_on_miss() -> None:
    cache = EmbedCache(_FakeRedis())
    assert await cache.get("never written", model="m") is None


async def test_set_then_get_round_trip() -> None:
    redis = _FakeRedis()
    cache = EmbedCache(redis)
    vec = [0.1, 0.2, 0.3]
    await cache.set("hello", vec, model="m", ttl_s=3600)
    assert redis.last_setex_ttl == 3600
    out = await cache.get("hello", model="m")
    assert out == vec


async def test_get_returns_none_on_redis_error() -> None:
    cache = EmbedCache(_FakeRedis(get_raises=RedisError("boom")))
    assert await cache.get("q", model="m") is None


async def test_set_swallows_redis_error_silent() -> None:
    redis = _FakeRedis(setex_raises=RedisError("boom"))
    cache = EmbedCache(redis)
    await cache.set("q", [0.1, 0.2], model="m", ttl_s=60)
    assert redis.store == {}


async def test_get_returns_none_on_malformed_json() -> None:
    redis = _FakeRedis()
    cache = EmbedCache(redis)
    redis.store[cache._key("q", model="m")] = "not-json["
    assert await cache.get("q", model="m") is None


async def test_get_returns_none_on_non_list_payload() -> None:
    # JSON-parseable but wrong shape → defensively reject.
    redis = _FakeRedis()
    cache = EmbedCache(redis)
    redis.store[cache._key("q", model="m")] = json.dumps({"not": "a list"})
    assert await cache.get("q", model="m") is None


async def test_set_skips_on_invalid_inputs() -> None:
    redis = _FakeRedis()
    cache = EmbedCache(redis)
    await cache.set("", [0.1, 0.2], model="m", ttl_s=60)  # empty query
    await cache.set("q", [], model="m", ttl_s=60)  # empty vec
    await cache.set("q", [0.1], model="m", ttl_s=0)  # zero ttl
    assert redis.store == {}


async def test_get_handles_bytes_payload() -> None:
    redis = _FakeRedis()
    cache = EmbedCache(redis)
    redis.store[cache._key("q", model="m")] = json.dumps([0.5, 0.6]).encode(
        "utf-8",
    )  # type: ignore[assignment]
    out = await cache.get("q", model="m")
    assert out == [0.5, 0.6]


async def test_get_returns_none_when_redis_is_none() -> None:
    cache = EmbedCache(None)
    assert await cache.get("q", model="m") is None
    # And set on a None client is a silent no-op.
    await cache.set("q", [0.1], model="m", ttl_s=60)


@pytest.mark.parametrize("ttl", [-1, 0])
async def test_set_skips_non_positive_ttl(ttl: int) -> None:
    redis = _FakeRedis()
    cache = EmbedCache(redis)
    await cache.set("q", [0.1], model="m", ttl_s=ttl)
    assert redis.store == {}
