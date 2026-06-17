"""Unit tests for RerankerResolver — per-bot reranker resolution.

Tests cover:
  1. No DB binding → NullReranker
  2. Jina binding → JinaReranker built via registry
  3. Redis cache HIT → skips DB (session_factory not called)
  4. Redis cache MISS → DB queried, result cached
  5. Empty API key → NullReranker (env var unset)
  6. DB query filters active=true + enabled=true only
  7. Redis WRITE failure → logs warning, returns resolver result
  8. Provider build failure → NullReranker
  9. Negative cache (empty dict) → NullReranker without hitting DB
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.reranker_resolver import (
    REDIS_KEY_PREFIX,
    RerankerResolver,
)
from ragbot.infrastructure.reranker.null_reranker import NullReranker
from ragbot.shared.constants import DEFAULT_RERANK_CONFIG_TTL_S


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_BOT_ID = UUID("cbc3b275-bb09-4765-b583-0b70253e5de5")
_JINA_CONFIG = {
    "model_name": "jina-reranker-v3",
    "provider_code": "jina",
    "api_key_ref": "RERANKER_JINA_API_KEY",
    "api_key_encrypted": None,
    "base_url": "https://api.jina.ai/v1",
    "model_meta": {"top_n": 5},
}


class FakeRedis:
    """Minimal in-memory fake Redis for resolver tests."""

    def __init__(self, initial: dict | None = None) -> None:
        self._store: dict[str, bytes] = {}
        if initial:
            for k, v in initial.items():
                self._store[k] = v.encode() if isinstance(v, str) else v
        self.setex_calls: list[tuple] = []
        self.get_calls: list[str] = []

    async def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self._store[key] = value.encode() if isinstance(value, str) else value


class BrokenRedis:
    """Redis that always raises RedisError on get/setex."""

    async def get(self, key: str) -> None:
        from redis.exceptions import RedisError
        raise RedisError("connection refused")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        from redis.exceptions import RedisError
        raise RedisError("connection refused")


def _make_session_factory(
    row: dict | None,
    platform_default_cfg: dict | None = None,
    platform_default_model_row: dict | None = None,
):
    """Build async session_factory mock that returns ``row`` as DB result.

    When ``row`` is None the resolver falls through to platform-default
    SQL: two extra ``session.execute`` calls, first returning the
    ``system_config`` rows and second returning the joined
    ``ai_models``/``ai_providers`` row.
    """
    session = AsyncMock()
    primary = MagicMock()
    primary.mappings.return_value.first.return_value = row

    cfg_result = MagicMock()
    cfg_pairs = list(platform_default_cfg.items()) if platform_default_cfg else []
    cfg_result.fetchall.return_value = cfg_pairs

    fallback_result = MagicMock()
    fallback_result.mappings.return_value.first.return_value = platform_default_model_row

    if row is None and platform_default_cfg is not None:
        session.execute = AsyncMock(
            side_effect=[primary, cfg_result, fallback_result],
        )
    else:
        session.execute = AsyncMock(return_value=primary)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory, session


# ---------------------------------------------------------------------------
# Test 1a — No binding + platform default DISABLED → NullReranker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_binding_and_platform_disabled_returns_null():
    """When neither bot has a binding nor system_config enables reranking,
    resolver must fall through to NullReranker — never raise.
    """
    factory, _ = _make_session_factory(
        None,
        platform_default_cfg={"reranker_enabled": "false"},
        platform_default_model_row=None,
    )
    resolver = RerankerResolver(session_factory=factory, redis_client=FakeRedis())
    result = await resolver.resolve_for_bot(_BOT_ID)
    assert isinstance(result, NullReranker), (
        "Platform default disabled must yield NullReranker"
    )


# ---------------------------------------------------------------------------
# Test 1b — No binding + platform default ENABLED → reranker from system_config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_binding_falls_back_to_platform_default():
    """REGRESSION GUARD (bug 2026-05-14, see memory:
    feedback-resolver-must-fallback-system-config).

    Bot without ``bot_model_bindings.purpose='rerank'`` MUST inherit the
    platform-default reranker from ``system_config`` + ``ai_models`` so
    every new tenant starts with the same retrieval quality. Never trip
    back to NullReranker when system_config has a valid model wired.
    """
    factory, session = _make_session_factory(
        None,
        platform_default_cfg={
            "reranker_enabled": "true",
            "reranker_model": "jina-reranker-v3",
            "reranker_provider": "jina",
        },
        platform_default_model_row=_JINA_CONFIG,
    )
    redis = FakeRedis()
    with patch.dict("os.environ", {"RERANKER_JINA_API_KEY": "fake-jina-key-for-test"}):
        resolver = RerankerResolver(session_factory=factory, redis_client=redis)
        result = await resolver.resolve_for_bot(_BOT_ID)

    from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
    assert isinstance(result, JinaReranker), (
        f"Platform default must build the configured reranker, "
        f"got {type(result).__name__}"
    )
    # 3 SQL hops: bindings probe + system_config read + ai_models join
    assert session.execute.call_count == 3


# ---------------------------------------------------------------------------
# Test 1c — Per-bot binding OVERRIDES platform default
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_bot_binding_overrides_platform_default():
    """Tenant that bought a different reranker tier must NOT be silently
    downgraded to the platform default — the explicit binding wins.
    """
    different_provider_cfg = {
        "model_name": "voyage-rerank-2",
        "provider_code": "voyage",
        "api_key_ref": "RERANKER_VOYAGE_API_KEY",
        "api_key_encrypted": None,
        "base_url": None,
        "model_meta": {},
    }
    # session_factory.execute only called ONCE (binding lookup) because
    # per-bot row is non-empty → platform default branch never runs.
    factory, session = _make_session_factory(different_provider_cfg)
    redis = FakeRedis()
    with patch.dict(
        "os.environ", {"RERANKER_VOYAGE_API_KEY": "fake-voyage-key"},
    ):
        resolver = RerankerResolver(session_factory=factory, redis_client=redis)
        result = await resolver.resolve_for_bot(_BOT_ID)

    assert not isinstance(result, NullReranker), (
        "Per-bot binding must build a real reranker, not NullReranker"
    )
    assert session.execute.call_count == 1, (
        "Per-bot binding found — must NOT fall through to platform-default SQL"
    )


# ---------------------------------------------------------------------------
# Test 2 — Jina binding → JinaReranker built via registry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_binding_with_jina_builds_jina_reranker():
    factory, _ = _make_session_factory(_JINA_CONFIG)
    redis = FakeRedis()
    with patch.dict("os.environ", {"RERANKER_JINA_API_KEY": "fake-jina-key-for-test"}):
        resolver = RerankerResolver(session_factory=factory, redis_client=redis)
        result = await resolver.resolve_for_bot(_BOT_ID)

    from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
    assert isinstance(result, JinaReranker), (
        f"Jina binding must yield JinaReranker, got {type(result).__name__}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Redis cache HIT → DB NOT called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_cache_hit_skips_db():
    cache_key = f"{REDIS_KEY_PREFIX}{_BOT_ID}"
    redis = FakeRedis({cache_key: json.dumps(_JINA_CONFIG)})

    # session_factory must NOT be called if cache hits
    factory = MagicMock(side_effect=AssertionError("DB should not be called on cache hit"))

    with patch.dict("os.environ", {"RERANKER_JINA_API_KEY": "fake-jina-key"}):
        resolver = RerankerResolver(session_factory=factory, redis_client=redis)
        result = await resolver.resolve_for_bot(_BOT_ID)

    assert cache_key in [c for c in redis.get_calls], "Cache key must be checked"
    # factory would raise if called — absence of exception means DB was skipped
    from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
    assert isinstance(result, JinaReranker)


# ---------------------------------------------------------------------------
# Test 4 — Redis cache MISS → DB queried + result cached
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_cache_miss_queries_db():
    redis = FakeRedis()  # empty cache
    factory, session = _make_session_factory(_JINA_CONFIG)

    with patch.dict("os.environ", {"RERANKER_JINA_API_KEY": "fake-jina-key"}):
        resolver = RerankerResolver(session_factory=factory, redis_client=redis)
        await resolver.resolve_for_bot(_BOT_ID)

    # DB was called
    session.execute.assert_called_once()
    # Redis setex was called to cache the result
    assert len(redis.setex_calls) == 1, "Result must be written to Redis cache"
    key, ttl, payload = redis.setex_calls[0]
    assert key == f"{REDIS_KEY_PREFIX}{_BOT_ID}"
    assert ttl == DEFAULT_RERANK_CONFIG_TTL_S
    cached = json.loads(payload)
    assert cached["provider_code"] == "jina"


# ---------------------------------------------------------------------------
# Test 5 — Empty API key → NullReranker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_api_key_falls_back_null():
    """Even with a valid binding, missing API key falls back to NullReranker
    instead of raising — keeps the query path resilient when an operator
    rotates keys.
    """
    factory, _ = _make_session_factory(_JINA_CONFIG)
    redis = FakeRedis()
    # Ensure env var is NOT set
    with patch.dict("os.environ", {}, clear=True):
        import os
        os.environ.pop("RERANKER_JINA_API_KEY", None)
        resolver = RerankerResolver(session_factory=factory, redis_client=redis)
        result = await resolver.resolve_for_bot(_BOT_ID)

    assert isinstance(result, NullReranker), (
        "Missing API key must yield NullReranker, not raise"
    )


# ---------------------------------------------------------------------------
# Test 6 — DB query must filter active=true + enabled=true
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_query_filters_active_and_enabled():
    """The per-bot binding lookup (first SQL the resolver runs) must scope
    to active rows on enabled models. After fix 2026-05-14 the resolver
    falls through to system_config when the binding lookup is empty, so we
    inspect the FIRST execute call (the bindings SQL), not the latest one.
    """
    factory, session = _make_session_factory(None)
    resolver = RerankerResolver(session_factory=factory, redis_client=FakeRedis())
    await resolver.resolve_for_bot(_BOT_ID)

    assert session.execute.called
    # First SQL run = bot_model_bindings probe. side_effect mock returns
    # only one result, so call_args_list[0] is what we want.
    first_call = session.execute.call_args_list[0]
    sql_text = str(first_call[0][0])
    assert "active" in sql_text.lower(), "Bindings SQL must filter on active"
    assert "enabled" in sql_text.lower(), "Bindings SQL must filter on enabled"
    assert "rerank" in sql_text.lower(), "Bindings SQL must filter on purpose='rerank'"


# ---------------------------------------------------------------------------
# Test 7 — Redis WRITE failure → logs warning, still returns resolver result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_write_failure_does_not_crash():
    factory, _ = _make_session_factory(None)

    class WriteFailRedis(FakeRedis):
        async def setex(self, key, ttl, value):  # type: ignore[override]
            from redis.exceptions import RedisError
            raise RedisError("write failed")

    redis = WriteFailRedis()
    resolver = RerankerResolver(session_factory=factory, redis_client=redis)
    # Must not raise despite Redis write failure
    result = await resolver.resolve_for_bot(_BOT_ID)
    assert isinstance(result, NullReranker), "Write failure must still return NullReranker for no-binding case"


# ---------------------------------------------------------------------------
# Test 8 — Provider build failure → NullReranker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_failure_falls_back_null():
    factory, _ = _make_session_factory(_JINA_CONFIG)
    redis = FakeRedis()

    with patch.dict("os.environ", {"RERANKER_JINA_API_KEY": "fake-key"}):
        with patch(
            "ragbot.application.services.reranker_resolver.build_reranker",
            side_effect=ValueError("provider install error"),
        ):
            resolver = RerankerResolver(session_factory=factory, redis_client=redis)
            result = await resolver.resolve_for_bot(_BOT_ID)

    assert isinstance(result, NullReranker), (
        "build_reranker failure must fall back to NullReranker"
    )


# ---------------------------------------------------------------------------
# Test 8b — resolver forwards the multi-key pool factory to build_reranker so
# the per-bot reranker gets N-key BPM round-robin, not a single env key.
# (Regression: 429 could not rotate → degraded straight to RRF.)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolver_forwards_key_pool_factory_to_build_reranker():
    factory, _ = _make_session_factory(_JINA_CONFIG)
    redis = FakeRedis()
    sentinel_pool_factory = MagicMock(name="ApiKeyPoolFactory")
    captured: dict = {}

    def _spy_build(**kwargs):
        captured.update(kwargs)
        return NullReranker()

    with patch.dict("os.environ", {"RERANKER_JINA_API_KEY": "fake-key"}):
        with patch(
            "ragbot.application.services.reranker_resolver.build_reranker",
            side_effect=_spy_build,
        ):
            resolver = RerankerResolver(
                session_factory=factory,
                redis_client=redis,
                key_pool_factory=sentinel_pool_factory,
            )
            await resolver.resolve_for_bot(_BOT_ID)

    assert captured.get("key_pool_factory") is sentinel_pool_factory, (
        "resolver must pass its key_pool_factory through to build_reranker — "
        "otherwise the per-bot reranker runs single-key and a 429 cannot rotate"
    )


# ---------------------------------------------------------------------------
# Test 9 — Negative cache (empty dict stored) → NullReranker, no DB hit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_negative_cache_marker_skips_db():
    cache_key = f"{REDIS_KEY_PREFIX}{_BOT_ID}"
    # Empty dict = negative cache marker written when no binding was found
    redis = FakeRedis({cache_key: json.dumps({})})

    factory = MagicMock(side_effect=AssertionError("DB must not be called on negative cache hit"))
    resolver = RerankerResolver(session_factory=factory, redis_client=redis)
    result = await resolver.resolve_for_bot(_BOT_ID)

    assert isinstance(result, NullReranker), (
        "Negative cache marker must yield NullReranker without DB query"
    )


# ---------------------------------------------------------------------------
# Test 10 — Redis GET failure → falls through to DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_read_failure_falls_through_to_db():
    factory, session = _make_session_factory(None)
    resolver = RerankerResolver(session_factory=factory, redis_client=BrokenRedis())
    result = await resolver.resolve_for_bot(_BOT_ID)
    assert isinstance(result, NullReranker)
    # DB was still queried despite Redis failure
    session.execute.assert_called_once()
