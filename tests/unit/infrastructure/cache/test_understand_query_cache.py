"""Unit tests for ``infrastructure.cache.understand_query_cache``.

S5 Pipeline-Opt: Redis-backed memo for the understand_query LLM call.
Tests pin the key-shape contract (prompt version + bot scope + sha256
prefix), the graceful-degradation behaviour (Redis errors / malformed
JSON → cache miss, never raise), and TTL pass-through.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from redis.exceptions import RedisError

from ragbot.infrastructure.cache.understand_query_cache import UnderstandQueryCache


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


def _expected_key(prompt_version: int, bot_id: str, query: str) -> str:
    h = hashlib.sha256(query[:300].encode("utf-8")).hexdigest()[:16]
    return f"ragbot:uq:v{prompt_version}:{bot_id}:{h}"


def test_key_shape_includes_prompt_version_and_bot_id() -> None:
    cache = UnderstandQueryCache(_FakeRedis(), prompt_version=1)
    key = cache._key("bot-A", "hello world")
    assert key == _expected_key(1, "bot-A", "hello world")


def test_key_changes_on_prompt_version_bump() -> None:
    cache_first = UnderstandQueryCache(_FakeRedis(), prompt_version=1)
    cache_next = UnderstandQueryCache(_FakeRedis(), prompt_version=2)
    assert cache_first._key("bot-A", "q") != cache_next._key("bot-A", "q")


def test_key_isolates_bots() -> None:
    cache = UnderstandQueryCache(_FakeRedis(), prompt_version=1)
    k_a = cache._key("bot-A", "same query")
    k_b = cache._key("bot-B", "same query")
    assert k_a != k_b
    # The bot id appears literally in the key body.
    assert ":bot-A:" in k_a and ":bot-B:" in k_b


def test_key_uses_first_300_chars_only() -> None:
    cache = UnderstandQueryCache(_FakeRedis(), prompt_version=1)
    long_q_a = "x" * 300 + "tail-A"
    long_q_b = "x" * 300 + "tail-B"
    # Bodies differ only after the 300-char prefix → same key.
    assert cache._key("bot", long_q_a) == cache._key("bot", long_q_b)
    # But a body that differs within the first 300 chars produces a
    # different key.
    long_q_c = "y" * 300 + "tail-A"
    assert cache._key("bot", long_q_a) != cache._key("bot", long_q_c)


async def test_get_returns_none_on_miss() -> None:
    cache = UnderstandQueryCache(_FakeRedis(), prompt_version=1)
    out = await cache.get("bot-A", "never written")
    assert out is None


async def test_set_then_get_round_trip_preserves_payload() -> None:
    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    payload = {"intent": "factual", "intent_confidence": 0.92, "query": "rewritten"}
    await cache.set("bot-A", "raw question", payload, ttl_s=3600)
    assert redis.last_setex_ttl == 3600
    out = await cache.get("bot-A", "raw question")
    assert out == payload


async def test_get_returns_none_on_redis_error() -> None:
    cache = UnderstandQueryCache(
        _FakeRedis(get_raises=RedisError("boom")), prompt_version=1,
    )
    out = await cache.get("bot-A", "any")
    assert out is None


async def test_set_swallows_redis_error_silent() -> None:
    redis = _FakeRedis(setex_raises=RedisError("boom"))
    cache = UnderstandQueryCache(redis, prompt_version=1)
    await cache.set("bot-A", "q", {"intent": "x"}, ttl_s=60)
    # No raise; nothing landed in store.
    assert redis.store == {}


async def test_get_returns_none_on_malformed_json() -> None:
    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    key = cache._key("bot-A", "q")
    redis.store[key] = "not-json{"
    assert await cache.get("bot-A", "q") is None


async def test_get_returns_none_on_non_dict_payload() -> None:
    # JSON-parseable but wrong shape (list, not dict) → defensively reject.
    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    key = cache._key("bot-A", "q")
    redis.store[key] = json.dumps(["intent", "confidence"])
    assert await cache.get("bot-A", "q") is None


async def test_set_skips_on_invalid_inputs() -> None:
    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    # Empty bot id, empty query, non-dict value, zero TTL — all no-op.
    await cache.set("", "q", {"intent": "x"}, ttl_s=60)
    await cache.set("bot", "", {"intent": "x"}, ttl_s=60)
    await cache.set("bot", "q", "not-a-dict", ttl_s=60)  # type: ignore[arg-type]
    await cache.set("bot", "q", {"intent": "x"}, ttl_s=0)
    assert redis.store == {}


async def test_get_returns_none_when_redis_client_is_none() -> None:
    cache = UnderstandQueryCache(None, prompt_version=1)
    assert await cache.get("bot-A", "q") is None
    # And set on a None client is a silent no-op (must not raise).
    await cache.set("bot-A", "q", {"intent": "x"}, ttl_s=60)


async def test_get_handles_bytes_payload() -> None:
    # redis-py with ``decode_responses=False`` returns bytes; ``get`` must
    # decode transparently.
    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    key = cache._key("bot-A", "q")
    redis.store[key] = json.dumps({"intent": "factual"}).encode("utf-8")  # type: ignore[assignment]
    out = await cache.get("bot-A", "q")
    assert out == {"intent": "factual"}


async def test_concurrent_writes_dont_race() -> None:
    # SETEX is atomic in Redis. Two writes against the same key must
    # leave the store in a consistent state (last writer wins, no
    # exception, no partial payload).
    import asyncio

    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    payload_a = {"intent": "a"}
    payload_b = {"intent": "b"}
    await asyncio.gather(
        cache.set("bot-A", "q", payload_a, ttl_s=60),
        cache.set("bot-A", "q", payload_b, ttl_s=60),
    )
    out = await cache.get("bot-A", "q")
    assert out in (payload_a, payload_b)


@pytest.mark.parametrize("ttl", [-1, 0])
async def test_set_skips_non_positive_ttl(ttl: int) -> None:
    redis = _FakeRedis()
    cache = UnderstandQueryCache(redis, prompt_version=1)
    await cache.set("bot-A", "q", {"intent": "x"}, ttl_s=ttl)
    assert redis.store == {}
