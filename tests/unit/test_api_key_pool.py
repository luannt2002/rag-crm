"""Unit tests for the provider-agnostic active-passive API key pool.

Covers the four resolver paths (neither cooled / primary cooled / secondary
cooled / both cooled), Redis-down fail-soft semantics, and the
no-secondary degenerate case. Parameterised across two ``provider_code``
values (``"jina"``, ``"openai"``) to prove the pool is brand-free at the
class boundary. The factory tests cover lazy construction, caching, and
absent-provider semantics.
"""

from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from ragbot.shared.api_key_pool import ApiKeyEntry, ApiKeyPool, ApiKeyPoolFactory
from ragbot.shared.constants import (
    API_KEY_COOLDOWN_REDIS_PREFIX,
    DEFAULT_API_KEY_COOLDOWN_S,
    DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S,
)


class _FakeRedis:
    """Minimal async Redis double — GET / SET (with EX) only."""

    def __init__(self, *, fail: bool = False) -> None:
        self._store: dict[str, str] = {}
        self._ttl: dict[str, int] = {}
        self._fail = fail
        self.set_calls: list[tuple[str, str, int]] = []
        self.get_calls: list[str] = []

    async def get(self, key: str):
        if self._fail:
            raise RedisError("redis down")
        self.get_calls.append(key)
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        if self._fail:
            raise RedisError("redis down")
        self.set_calls.append((key, value, int(ex or 0)))
        self._store[key] = value
        if ex is not None:
            self._ttl[key] = int(ex)
        return True


_PRIMARY_KEY = "key-primary-aaaaaa"
_SECONDARY_KEY = "key-secondary-bbbbbb"


# ---------------------------------------------------------------------------
# get_active resolver paths — parameterised over provider_code to prove the
# pool is brand-agnostic.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_code", ["jina", "openai"])
@pytest.mark.asyncio
async def test_pool_returns_primary_when_neither_cooled(provider_code: str) -> None:
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=_SECONDARY_KEY,
        redis_client=_FakeRedis(),
        provider_code=provider_code,
        purpose="embed",
    )
    entry = await pool.get_active()
    assert isinstance(entry, ApiKeyEntry)
    assert entry.label == "primary"
    assert entry.key == _PRIMARY_KEY
    assert pool.provider_code == provider_code


@pytest.mark.parametrize("provider_code", ["jina", "cohere"])
@pytest.mark.asyncio
async def test_pool_returns_secondary_when_primary_cooled(provider_code: str) -> None:
    redis = _FakeRedis()
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=_SECONDARY_KEY,
        redis_client=redis,
        provider_code=provider_code,
        purpose="embed",
    )
    primary_entry = await pool.get_active()
    await pool.mark_cooldown(primary_entry, reason="HTTP_403")
    # Cooldown SET must hit Redis with the configured TTL constant.
    assert len(redis.set_calls) == 1
    redis_key, _value, ex = redis.set_calls[0]
    assert redis_key.startswith(API_KEY_COOLDOWN_REDIS_PREFIX)
    # Redis key embeds provider_code AND purpose so different providers
    # don't collide on the same hash digest.
    assert provider_code in redis_key
    assert "embed" in redis_key
    assert ex == DEFAULT_API_KEY_COOLDOWN_S
    # Resolver now returns secondary.
    next_entry = await pool.get_active()
    assert next_entry.label == "secondary"
    assert next_entry.key == _SECONDARY_KEY


@pytest.mark.asyncio
async def test_pool_returns_primary_when_secondary_only_cooled() -> None:
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=_SECONDARY_KEY,
        redis_client=_FakeRedis(),
        provider_code="jina",
        purpose="rerank",
    )
    secondary_entry = ApiKeyEntry(key=_SECONDARY_KEY, label="secondary")
    await pool.mark_cooldown(secondary_entry, reason="HTTP_429")
    next_entry = await pool.get_active()
    assert next_entry.label == "primary"
    assert next_entry.key == _PRIMARY_KEY


@pytest.mark.asyncio
async def test_pool_returns_primary_when_both_cooled() -> None:
    """Fail-soft: both cooled still yields the primary so the caller can
    hit upstream and discover whether the cooldown ledger is stale."""
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=_SECONDARY_KEY,
        redis_client=_FakeRedis(),
        provider_code="jina",
        purpose="embed",
    )
    primary_entry = ApiKeyEntry(key=_PRIMARY_KEY, label="primary")
    secondary_entry = ApiKeyEntry(key=_SECONDARY_KEY, label="secondary")
    await pool.mark_cooldown(primary_entry, reason="HTTP_403")
    await pool.mark_cooldown(secondary_entry, reason="HTTP_403")
    next_entry = await pool.get_active()
    assert next_entry.label == "primary"


# ---------------------------------------------------------------------------
# Fail-soft on Redis outage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_redis_failure_does_not_crash() -> None:
    """RedisError on SET must be absorbed; resolver keeps returning a usable
    key because ``_is_cooled`` also fail-softs to False on Redis errors."""
    redis = _FakeRedis(fail=True)
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=_SECONDARY_KEY,
        redis_client=redis,
        provider_code="jina",
        purpose="rerank",
    )
    primary_entry = await pool.get_active()
    # mark_cooldown swallows the RedisError (no exception bubbles up).
    await pool.mark_cooldown(primary_entry, reason="HTTP_403")
    # Resolver continues to function — a valid key is returned (round-robin
    # fail-soft path; the exact label rotates so we only assert usability).
    next_entry = await pool.get_active()
    assert next_entry.label in {"primary", "secondary"}
    assert next_entry.key in {_PRIMARY_KEY, _SECONDARY_KEY}


# ---------------------------------------------------------------------------
# Degenerate case — only primary configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_secondary_pool_works_with_primary_only() -> None:
    """Without a secondary the pool always returns primary, even after
    mark_cooldown — the alternative would be an empty pool, and the
    caller still has to make the request to learn the truth."""
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=None,
        redis_client=_FakeRedis(),
        provider_code="openai",
        purpose="embed",
    )
    assert pool.has_secondary is False
    entry = await pool.get_active()
    assert entry.label == "primary"
    await pool.mark_cooldown(entry, reason="HTTP_403")
    # Still primary — no secondary to fall onto.
    fallback = await pool.get_active()
    assert fallback.label == "primary"
    assert fallback.key == _PRIMARY_KEY


# ---------------------------------------------------------------------------
# Constructor guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_rejects_empty_primary() -> None:
    with pytest.raises(ValueError):
        ApiKeyPool(
            primary="",
            secondary=_SECONDARY_KEY,
            redis_client=_FakeRedis(),
            provider_code="jina",
            purpose="embed",
        )


@pytest.mark.asyncio
async def test_pool_rejects_empty_provider_code() -> None:
    with pytest.raises(ValueError):
        ApiKeyPool(
            primary=_PRIMARY_KEY,
            secondary=None,
            redis_client=_FakeRedis(),
            provider_code="",
            purpose="embed",
        )


@pytest.mark.asyncio
async def test_pool_rejects_empty_purpose() -> None:
    with pytest.raises(ValueError):
        ApiKeyPool(
            primary=_PRIMARY_KEY,
            secondary=None,
            redis_client=_FakeRedis(),
            provider_code="jina",
            purpose="",
        )


# ---------------------------------------------------------------------------
# Redis ledger never holds the plaintext key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_ledger_does_not_leak_plaintext_key() -> None:
    redis = _FakeRedis()
    pool = ApiKeyPool(
        primary=_PRIMARY_KEY,
        secondary=_SECONDARY_KEY,
        redis_client=redis,
        provider_code="jina",
        purpose="embed",
    )
    entry = await pool.get_active()
    await pool.mark_cooldown(entry, reason="HTTP_403")
    assert redis.set_calls
    redis_key = redis.set_calls[0][0]
    # Hash prefix only — no plaintext credential in the ledger key.
    assert _PRIMARY_KEY not in redis_key
    assert _SECONDARY_KEY not in redis_key


# ---------------------------------------------------------------------------
# ApiKeyPoolFactory — lazy build, caching, absent-provider semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_returns_none_for_unknown_provider() -> None:
    """No keys configured for a provider_code → ``None`` so the adapter
    falls back to its legacy env-var path without a special case."""
    factory = ApiKeyPoolFactory(
        provider_keys={"jina": [_PRIMARY_KEY]},
        redis_client=_FakeRedis(),
    )
    assert factory.get("openai", "embed") is None
    assert factory.get("missing", "rerank") is None


@pytest.mark.asyncio
async def test_factory_returns_pool_for_known_provider() -> None:
    factory = ApiKeyPoolFactory(
        provider_keys={"jina": [_PRIMARY_KEY, _SECONDARY_KEY]},
        redis_client=_FakeRedis(),
    )
    pool = factory.get("jina", "embed")
    assert isinstance(pool, ApiKeyPool)
    assert pool.provider_code == "jina"
    assert pool.purpose == "embed"
    assert pool.has_secondary is True


@pytest.mark.asyncio
async def test_factory_caches_pool_per_purpose() -> None:
    """Repeat calls with the same (provider_code, purpose) return the
    same pool instance — keeps the Redis-cooldown ledger consistent."""
    factory = ApiKeyPoolFactory(
        provider_keys={"jina": [_PRIMARY_KEY, _SECONDARY_KEY]},
        redis_client=_FakeRedis(),
    )
    first = factory.get("jina", "embed")
    second = factory.get("jina", "embed")
    assert first is second


@pytest.mark.asyncio
async def test_factory_separate_pools_per_purpose() -> None:
    """``embed`` and ``rerank`` purposes get distinct pools so an outage
    on one surface doesn't poison the other's cooldown ledger."""
    factory = ApiKeyPoolFactory(
        provider_keys={"jina": [_PRIMARY_KEY, _SECONDARY_KEY]},
        redis_client=_FakeRedis(),
    )
    embed_pool = factory.get("jina", "embed")
    rerank_pool = factory.get("jina", "rerank")
    assert embed_pool is not None and rerank_pool is not None
    assert embed_pool is not rerank_pool
    assert embed_pool.purpose == "embed"
    assert rerank_pool.purpose == "rerank"


@pytest.mark.asyncio
async def test_factory_drops_empty_key_lists() -> None:
    """Empty list means no keys configured — same semantics as missing."""
    factory = ApiKeyPoolFactory(
        provider_keys={"jina": []},
        redis_client=_FakeRedis(),
    )
    assert factory.get("jina", "embed") is None


@pytest.mark.asyncio
async def test_factory_supports_primary_only_keys() -> None:
    factory = ApiKeyPoolFactory(
        provider_keys={"jina": [_PRIMARY_KEY]},
        redis_client=_FakeRedis(),
    )
    pool = factory.get("jina", "embed")
    assert pool is not None
    assert pool.has_secondary is False


@pytest.mark.asyncio
async def test_factory_isolates_keys_per_provider() -> None:
    """Different providers get independent pools."""
    factory = ApiKeyPoolFactory(
        provider_keys={
            "jina": [_PRIMARY_KEY],
            "openai": ["sk-aaaa", "sk-bbbb"],
        },
        redis_client=_FakeRedis(),
    )
    jina_pool = factory.get("jina", "embed")
    openai_pool = factory.get("openai", "embed")
    assert jina_pool is not None and openai_pool is not None
    assert jina_pool.provider_code == "jina"
    assert openai_pool.provider_code == "openai"
    assert openai_pool.has_secondary is True
    assert jina_pool.has_secondary is False


# ---------------------------------------------------------------------------
# N-key round-robin (BPM spreading across distinct upstream accounts)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_key_round_robin_spreads_load() -> None:
    """get_active rotates across all 3 keys so each sees ~1/3 the requests —
    this is what divides a per-minute (BPM) quota under a parallel load."""
    pool = ApiKeyPool(
        primary="k1", secondary="k2", extras=["k3"],
        redis_client=_FakeRedis(),
        provider_code="zeroentropy", purpose="rerank",
    )
    assert pool.key_count == 3
    seen = [(await pool.get_active()).key for _ in range(6)]
    # Two full rotations → each key used exactly twice.
    assert seen == ["k1", "k2", "k3", "k1", "k2", "k3"]


@pytest.mark.asyncio
async def test_round_robin_skips_cooled_key() -> None:
    """A cooled key is skipped; the rotation continues on the live keys."""
    redis = _FakeRedis()
    pool = ApiKeyPool(
        primary="k1", secondary="k2", extras=["k3"],
        redis_client=redis, provider_code="zeroentropy", purpose="rerank",
    )
    # Cool k2 (the second entry).
    await pool.mark_cooldown(ApiKeyEntry(key="k2", label="secondary"),
                             reason="HTTP_429")
    seen = [(await pool.get_active()).key for _ in range(4)]
    assert "k2" not in seen  # cooled key never returned
    assert set(seen) == {"k1", "k3"}


@pytest.mark.asyncio
async def test_single_key_pool_always_returns_it() -> None:
    pool = ApiKeyPool(
        primary="only", secondary=None,
        redis_client=_FakeRedis(),
        provider_code="zeroentropy", purpose="rerank",
    )
    assert pool.key_count == 1
    for _ in range(3):
        assert (await pool.get_active()).key == "only"


# ---------------------------------------------------------------------------
# Differentiated cooldown TTL — a transient 429 (BPM refill) cools briefly;
# a hard 403 keeps the long default so the key isn't retried every rotation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_cooldown_override_uses_short_ttl_for_ratelimit() -> None:
    redis = _FakeRedis()
    pool = ApiKeyPool(
        primary="k1", secondary="k2", extras=["k3"],
        redis_client=redis, provider_code="zeroentropy", purpose="rerank",
    )
    entry = await pool.get_active()
    await pool.mark_cooldown(
        entry, reason="HTTP_429", cooldown_s=DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S
    )
    assert redis.set_calls
    _key, _value, ex = redis.set_calls[0]
    assert ex == DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S
    # The short rate-limit TTL must be well under the long forbidden default
    # so a BPM-throttled key rejoins the rotation quickly.
    assert ex < DEFAULT_API_KEY_COOLDOWN_S


@pytest.mark.asyncio
async def test_mark_cooldown_defaults_to_long_ttl_without_override() -> None:
    redis = _FakeRedis()
    pool = ApiKeyPool(
        primary="k1", secondary="k2",
        redis_client=redis, provider_code="zeroentropy", purpose="rerank",
    )
    entry = await pool.get_active()
    await pool.mark_cooldown(entry, reason="HTTP_403")  # no override
    _key, _value, ex = redis.set_calls[0]
    assert ex == DEFAULT_API_KEY_COOLDOWN_S


@pytest.mark.asyncio
async def test_mark_cooldown_zero_override_falls_back_to_default() -> None:
    """A falsy override (0 / None) must not zero the TTL — fall back to the
    pool default rather than writing a non-expiring or instant cooldown."""
    redis = _FakeRedis()
    pool = ApiKeyPool(
        primary="k1", secondary="k2",
        redis_client=redis, provider_code="zeroentropy", purpose="rerank",
    )
    entry = await pool.get_active()
    await pool.mark_cooldown(entry, reason="HTTP_403", cooldown_s=0)
    _key, _value, ex = redis.set_calls[0]
    assert ex == DEFAULT_API_KEY_COOLDOWN_S
