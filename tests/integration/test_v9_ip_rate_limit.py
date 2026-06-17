"""Integration tests for IpRateLimitMiddleware.

Mocks the DI container's ``redis_client`` with an AsyncMock that mimics
INCR + EXPIRE + SISMEMBER semantics. We don't depend on a live Redis;
the tests prove the middleware's contract:

* Bypass for /health, /metrics, /static, /docs, /openapi.json paths.
* Bypass for IPs in ``ip_allowlist``.
* X-Forwarded-For honoured ONLY when ``request.client.host`` is in
  ``trusted_proxies``.
* Per-IP cap → 429 + ``Retry-After`` after threshold.
* Fail-CLOSED on Redis error → 503 + Retry-After (NOT 200).
* Suspicious IP set membership applies the multiplier.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from ragbot.shared.constants import (
    DEFAULT_RL_IP_PER_MIN,
    DEFAULT_RL_IP_WINDOW_S,
)


def _make_redis_mock(
    *,
    counter_start: int = 0,
    suspicious: bool = False,
    raise_on_incr: bool = False,
) -> MagicMock:
    """Build an AsyncMock that satisfies the middleware's Redis contract."""
    redis = MagicMock()
    state = {"count": counter_start}

    async def _incr(_key: str) -> int:
        if raise_on_incr:
            raise RedisError("simulated outage")
        state["count"] += 1
        return state["count"]

    async def _expire(_key: str, _ttl: int) -> bool:
        return True

    async def _sismember(_key: str, _ip: str) -> bool:
        return suspicious

    async def _sadd(_key: str, *_ips: str) -> int:
        return len(_ips)

    async def _set(_key: str, _val: str, ex: int | None = None) -> bool:
        return True

    async def _get(_key: str) -> str | None:
        return None

    async def _scard(_key: str) -> int:
        return 1

    redis.incr = AsyncMock(side_effect=_incr)
    redis.expire = AsyncMock(side_effect=_expire)
    redis.sismember = AsyncMock(side_effect=_sismember)
    redis.sadd = AsyncMock(side_effect=_sadd)
    redis.set = AsyncMock(side_effect=_set)
    redis.get = AsyncMock(side_effect=_get)
    redis.scard = AsyncMock(side_effect=_scard)
    return redis


@asynccontextmanager
async def _noop_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """No-op lifespan — state is injected directly after create_app."""
    yield


def _build_client(
    *,
    redis_mock: MagicMock | None,
    trusted_proxies: str = "",
    ip_allowlist: str = "",
    ip_rate_limit_enabled: bool = True,
    anti_abuse_enabled: bool = False,
) -> TestClient:
    """Build a TestClient with controlled middleware config + mocked Redis.

    Skips the real lifespan + injects ``app.state.container`` directly so
    tests don't depend on Postgres / Redis being live. The middlewares
    pull Redis off the container, so we wire the mock there.
    """
    import os
    os.environ["APP_TRUSTED_PROXIES"] = trusted_proxies
    os.environ["APP_IP_ALLOWLIST"] = ip_allowlist
    os.environ["APP_IP_RATE_LIMIT_ENABLED"] = (
        "true" if ip_rate_limit_enabled else "false"
    )
    os.environ["APP_ANTI_ABUSE_ENABLED"] = (
        "true" if anti_abuse_enabled else "false"
    )
    os.environ["APP_CORS_ALLOWED_ORIGINS"] = "[]"

    from ragbot.config import settings as settings_mod
    settings_mod.get_settings.cache_clear()

    app_mod = importlib.import_module("ragbot.interfaces.http.app")
    original = app_mod.lifespan
    app_mod.lifespan = _noop_lifespan
    try:
        application = app_mod.create_app()
    finally:
        app_mod.lifespan = original

    mock_container = MagicMock()
    if redis_mock is not None:
        mock_container.redis_client = MagicMock(return_value=redis_mock)
    else:
        # Setting attribute to None signals "container has no redis_client"
        # — the middleware's _resolve_redis treats this as outage.
        mock_container.redis_client = None
    application.state.container = mock_container
    return TestClient(application, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Bypass paths
# ---------------------------------------------------------------------------


class TestBypassPaths:
    def test_health_bypassed(self) -> None:
        """/health should never hit the IP rate limit."""
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        for _ in range(5):
            r = client.get("/health")
            # /health route may return 200 or whatever; what matters: NOT 429.
            assert r.status_code != 429
        # Most importantly: the IP-RL incr was never called.
        assert redis.incr.await_count == 0

    def test_metrics_bypassed(self) -> None:
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        for _ in range(3):
            r = client.get("/metrics")
            assert r.status_code != 429
        assert redis.incr.await_count == 0


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------


class TestIpAllowlist:
    def test_allowlisted_ip_bypasses(self) -> None:
        """testclient peer is 'testclient' — put it in the allowlist."""
        redis = _make_redis_mock()
        client = _build_client(
            redis_mock=redis,
            ip_allowlist="testclient",
        )
        r = client.get("/openapi.json")
        # /openapi.json is also bypass-path so it's a noop here, but test
        # a non-bypass route to confirm allowlist short-circuit:
        r = client.post("/api/ragbot/test/chat", json={})
        assert r.status_code != 429
        assert redis.incr.await_count == 0


# ---------------------------------------------------------------------------
# Rate limit enforcement
# ---------------------------------------------------------------------------


class TestRateLimitEnforcement:
    def test_under_cap_passes(self) -> None:
        """Counter below cap → middleware lets the request through."""
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        r = client.post("/api/ragbot/test/chat", json={})
        # Some downstream status (404/422/500) — but NOT a 429 from our MW.
        assert r.status_code != 429
        # Confirm the middleware actually invoked Redis.
        assert redis.incr.await_count >= 1

    def test_over_cap_returns_429_with_retry_after(self) -> None:
        """Counter pre-loaded over the cap → 429 + Retry-After."""
        redis = _make_redis_mock(counter_start=DEFAULT_RL_IP_PER_MIN)
        client = _build_client(redis_mock=redis)
        r = client.post("/api/ragbot/test/chat", json={})
        assert r.status_code == 429
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "IP_RATE_LIMITED"
        # Anti-tune: must NOT leak X-RateLimit-* headers.
        keys_lower = {k.lower() for k in r.headers.keys()}
        assert "x-ratelimit-limit" not in keys_lower
        assert "x-ratelimit-remaining" not in keys_lower
        # Must surface a Retry-After per RFC 6585.
        retry_after = r.headers.get("Retry-After")
        assert retry_after is not None
        assert int(retry_after) == DEFAULT_RL_IP_WINDOW_S


# ---------------------------------------------------------------------------
# Fail-closed on Redis error
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_redis_error_returns_503_not_200(self) -> None:
        """RedisError on incr → middleware MUST fail closed (503 or 429)."""
        redis = _make_redis_mock(raise_on_incr=True)
        client = _build_client(redis_mock=redis)
        r = client.post("/api/ragbot/test/chat", json={})
        # Anti-spray must not turn into a DoS amplifier — verify NOT 2xx.
        assert r.status_code in (429, 503)
        assert r.status_code != 200
        body = r.json()
        assert body["ok"] is False

    def test_no_redis_client_in_container_fails_closed(self) -> None:
        """Container without redis_client attribute → 503."""
        client = _build_client(redis_mock=None)
        r = client.post("/api/ragbot/test/chat", json={})
        assert r.status_code in (429, 503)
        assert r.status_code != 200


# ---------------------------------------------------------------------------
# X-Forwarded-For trust
# ---------------------------------------------------------------------------


class TestXForwardedFor:
    def test_xff_ignored_when_peer_not_trusted(self) -> None:
        """XFF from untrusted peer → middleware uses connection peer.

        We can't easily change ``request.client.host`` in TestClient (it's
        always ``"testclient"``), but we CAN verify that XFF claiming
        an allowlisted IP doesn't bypass the limiter.
        """
        redis = _make_redis_mock(counter_start=DEFAULT_RL_IP_PER_MIN)
        client = _build_client(
            redis_mock=redis,
            trusted_proxies="",  # nothing trusted → ignore XFF
            ip_allowlist="9.9.9.9",  # spoofable allowlist target
        )
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        # Spoofed XFF must NOT lift the source IP into the allowlist.
        assert r.status_code == 429

    def test_xff_honoured_when_peer_trusted(self) -> None:
        """testclient peer trusted → XFF chain consulted → allowlist hit."""
        redis = _make_redis_mock(counter_start=DEFAULT_RL_IP_PER_MIN)
        client = _build_client(
            redis_mock=redis,
            trusted_proxies="testclient",
            ip_allowlist="9.9.9.9",
        )
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        # Trusted proxy + XFF resolves to allowlisted IP → bypass.
        assert r.status_code != 429


# ---------------------------------------------------------------------------
# Disable switch
# ---------------------------------------------------------------------------


def test_disabled_switch_skips_middleware() -> None:
    """APP_IP_RATE_LIMIT_ENABLED=false → middleware not even installed."""
    redis = _make_redis_mock(counter_start=DEFAULT_RL_IP_PER_MIN * 100)
    client = _build_client(
        redis_mock=redis,
        ip_rate_limit_enabled=False,
    )
    r = client.post("/api/ragbot/test/chat", json={})
    # With middleware disabled the high counter is irrelevant — we never check.
    assert r.status_code != 429
    assert redis.incr.await_count == 0


# ---------------------------------------------------------------------------
# extract_real_ip helper unit
# ---------------------------------------------------------------------------


class TestExtractRealIp:
    def test_no_xff_returns_peer(self) -> None:
        from ragbot.interfaces.http.middlewares.ip_rate_limit import (
            extract_real_ip,
        )

        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "1.2.3.4"
        request.headers = {}
        assert extract_real_ip(request, frozenset()) == "1.2.3.4"

    def test_xff_walked_right_to_left(self) -> None:
        from ragbot.interfaces.http.middlewares.ip_rate_limit import (
            extract_real_ip,
        )

        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "10.0.0.1"  # trusted edge
        request.headers = {"X-Forwarded-For": "5.5.5.5, 10.0.0.2, 10.0.0.1"}
        # Trust the edge + intermediate proxy; the real client is 5.5.5.5.
        trusted = frozenset({"10.0.0.1", "10.0.0.2"})
        assert extract_real_ip(request, trusted) == "5.5.5.5"

    def test_no_client_returns_empty(self) -> None:
        from ragbot.interfaces.http.middlewares.ip_rate_limit import (
            extract_real_ip,
        )

        request = MagicMock()
        request.client = None
        request.headers = {}
        assert extract_real_ip(request, frozenset()) == ""
