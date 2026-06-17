"""P25-L6 — rate-limit fail-closed on Redis backend error.

Pre-P25 the ``_check_rate_limit`` helper caught every Redis exception and
returned ``False`` (not limited). This silently disabled rate limiting
whenever Redis hiccuped — an adversary who briefly spiked Redis latency
could push unlimited RPS through the app.

These tests lock the new behaviour:

1. Redis error ⇒ helper raises ``RateLimitBackendUnavailable``.
2. Happy path under the limit ⇒ helper returns ``False``.
3. Happy path over the limit ⇒ helper returns ``True``.
4. Callsite policy — middleware rejects non-owner traffic with HTTP 503
   when the backend is down; owner tokens (rl_val == 0) never enter the
   helper and keep their previous behaviour.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.interfaces.http.middlewares.tenant_context import (
    RateLimitBackendUnavailable,
    TenantContextMiddleware,
    _check_rate_limit,
)


class _CountingRedis:
    """In-memory redis stub — enough surface for ``_check_rate_limit``.

    Tracks INCR counts per key. ``expire`` is a no-op. No TTL simulation
    needed because each test uses a single bucket.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True


class _ErroringRedis:
    """Redis stub whose ``incr`` always raises — simulates Redis down."""

    async def incr(self, key: str) -> int:  # noqa: ARG002
        raise ConnectionError("redis down")

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True


# ---------------------------------------------------------------------------
# Unit tests — the helper itself.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_backend_unavailable_raised_on_redis_error() -> None:
    """Redis failure must surface as ``RateLimitBackendUnavailable``.

    Regression guard for the fail-open bug: ``except: return False`` is
    GONE; callers must see the exception so they can decide the policy.
    """
    redis = _ErroringRedis()
    with pytest.raises(RateLimitBackendUnavailable):
        await _check_rate_limit(redis, "svc", limit=10, window=60)


@pytest.mark.asyncio
async def test_rate_limit_returns_false_when_under_limit() -> None:
    """Calls under the limit must never report ``limited``."""
    redis = _CountingRedis()
    results = []
    for _ in range(5):
        results.append(await _check_rate_limit(redis, "svc", limit=10, window=60))
    assert results == [False, False, False, False, False]


@pytest.mark.asyncio
async def test_rate_limit_returns_true_when_over_limit() -> None:
    """Once count exceeds the limit, subsequent calls must be flagged."""
    redis = _CountingRedis()
    r1 = await _check_rate_limit(redis, "svc", limit=2, window=60)
    r2 = await _check_rate_limit(redis, "svc", limit=2, window=60)
    r3 = await _check_rate_limit(redis, "svc", limit=2, window=60)
    assert r1 is False
    assert r2 is False
    assert r3 is True  # third call = count 3, exceeds limit 2


@pytest.mark.asyncio
async def test_rate_limit_metric_incremented_on_redis_error() -> None:
    """The backend-error counter must tick on every Redis failure.

    Operators rely on this counter firing a Grafana alert the moment the
    limiter starts rejecting traffic fail-closed.
    """
    from ragbot.infrastructure.observability.metrics import (
        rate_limit_backend_error_total,
    )

    before = rate_limit_backend_error_total.labels(reason="redis_error")._value.get()
    redis = _ErroringRedis()
    with pytest.raises(RateLimitBackendUnavailable):
        await _check_rate_limit(redis, "svc", limit=10, window=60)
    after = rate_limit_backend_error_total.labels(reason="redis_error")._value.get()
    assert after == before + 1


# ---------------------------------------------------------------------------
# Integration-style test — middleware returns 503 when Redis is down for a
# non-owner token. Owner tokens (rl_val == 0) skip the whole block so their
# path is unaffected; we prove that by giving them a working request too.
# ---------------------------------------------------------------------------


class _FakeJwtOk:
    """Service-JWT verifier stub that always accepts the token."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def verify_token(self, token: str, redis_client: Any = None) -> dict[str, Any]:  # noqa: ARG002
        return self._payload


def _build_request(body: bytes = b"") -> Any:
    """Minimal Starlette Request facade — only the middleware-used bits."""

    async def _body() -> bytes:
        return body

    req = MagicMock()
    req.url.path = "/api/ragbot/chat"
    req.headers = {"Authorization": "Bearer tok"}
    req.body = _body
    req.state = MagicMock()
    # Attach app.state.container / settings / dev_jwt_secret surface.
    req.app = MagicMock()
    req.app.state = MagicMock()
    container = MagicMock()
    container.session_factory.return_value = MagicMock()
    req.app.state.container = container
    req.app.state.settings = MagicMock()
    req.app.state.settings.app.api_token = "dev"
    req.app.state.dev_jwt_secret = "dev"
    return req, container


@pytest.mark.asyncio
async def test_middleware_fails_closed_503_when_redis_down_for_non_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner token + Redis down ⇒ JSON 503 RATE_LIMIT_UNAVAILABLE.

    This is the full end-to-end policy: helper raises → middleware catches
    → fail-closed response. No JSONResponse leakage to downstream handler.
    """
    from ragbot.interfaces.http.middlewares import tenant_context as mod

    # Non-owner token: rl_val > 0 so it enters the rate-limit block.
    payload = {
        "sub": "svc-A",
        "role": "service",
        "rl_val": 120,
        "rl_win": 60,
        "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
    }
    monkeypatch.setattr(
        mod, "JwtTokenService", lambda **kw: _FakeJwtOk(payload),  # noqa: ARG005
    )

    req, container = _build_request(body=b"")

    # Redis: incr → raises (simulating Redis down). get() returns None so
    # the bot-bypass cache-lookup doesn't skip the RL block.
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.incr = AsyncMock(side_effect=ConnectionError("redis down"))
    redis_mock.expire = AsyncMock()
    container.redis_client.return_value = redis_mock

    middleware = TenantContextMiddleware(app=MagicMock())
    call_next = AsyncMock()  # must NOT be reached

    response = await middleware.dispatch(req, call_next)

    assert response.status_code == 503
    call_next.assert_not_called()
    body = json.loads(response.body.decode())
    assert body["ok"] is False
    assert body["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_middleware_owner_token_unaffected_when_redis_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner tokens (rl_val == 0) skip the RL block entirely.

    Backwards-compat lock: P25-L6 must not regress the owner path. Redis
    may be down and the owner request should still flow to ``call_next``.
    No 503, no fail-closed metric. The per-user Layer 2 also skips because
    the request body has no ``connect_id``.
    """
    from ragbot.interfaces.http.middlewares import tenant_context as mod

    # Owner: rl_val == 0 → skip both RL layers' Redis calls entirely.
    payload = {
        "sub": "owner-A",
        "role": "owner",
        "rl_val": 0,
        "rl_win": 60,
        "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
    }
    monkeypatch.setattr(
        mod, "JwtTokenService", lambda **kw: _FakeJwtOk(payload),  # noqa: ARG005
    )

    req, container = _build_request(body=b"")

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.incr = AsyncMock(side_effect=ConnectionError("redis down"))
    redis_mock.expire = AsyncMock()
    container.redis_client.return_value = redis_mock

    middleware = TenantContextMiddleware(app=MagicMock())
    sentinel_response = MagicMock()
    sentinel_response.status_code = 200
    call_next = AsyncMock(return_value=sentinel_response)

    response = await middleware.dispatch(req, call_next)

    # Owner request flowed through; RL Redis errors never bubbled up.
    call_next.assert_called_once()
    assert response is sentinel_response
    redis_mock.incr.assert_not_called()
