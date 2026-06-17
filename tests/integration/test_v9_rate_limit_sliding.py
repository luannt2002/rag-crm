"""Sliding-window per-token rate limit tests.

Coverage matrix
---------------
1. Steady-state: 60 req/min limit → first 60 PASS, 61st → 429.
2. Burst: 60/min steady + 2.0 burst factor + 10s burst window → first
   120 in 10s PASS, 121st → 429.
3. Per-endpoint isolation: ``/chat`` and ``/admin`` policies do not
   share counters even for the same caller key.
4. Cross-tenant isolation: tenant A and tenant B keys keep independent
   counters under the same endpoint.
5. 429 response carries ``X-RateLimit-Limit / -Remaining / -Reset`` and
   ``Retry-After`` per W3C draft.
6. Successful response carries ``X-RateLimit-*`` headers (W3C draft).
7. Soft-unlimited (``limit=0``) never throttles.
8. Fail-mode 'closed' returns 503 when the limiter raises a backend
   error; fail-mode 'open' lets the request through.
9. Policy table — ``/health`` returns ``None`` (unlimited), default
   policy fires for unknown paths.
10. Preflight OPTIONS bypasses rate limit (handled by CORS).

Test doubles
------------
* ``InMemorySlidingWindow`` is the production-shape limiter — used here
  directly so tests need no Redis.
* Hand-built ``Request`` / ``call_next`` wraps the middleware in
  isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from starlette.responses import PlainTextResponse, Response

from ragbot.application.ports.rate_limiter_port import (
    RateLimiterDecision,
    RateLimiterPort,
)
from ragbot.infrastructure.rate_limiter.in_memory import InMemorySlidingWindow
from ragbot.infrastructure.rate_limiter.registry import (
    build_rate_limiter,
    list_providers,
)
from ragbot.interfaces.http.middlewares.rate_limit import (
    SlidingRateLimitMiddleware,
)
from ragbot.shared.constants import (
    DEFAULT_RL_ADMIN_PER_MIN,
    DEFAULT_RL_CHAT_PER_MIN,
)
from ragbot.shared.rate_limit_policy import (
    RateLimitPolicy,
    list_policies,
    resolve_policy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    method: str,
    path: str,
    record_tenant_id: Any,
    user_id: str | None,
    bearer: str | None = None,
) -> Any:
    headers: dict[str, str] = {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"

    class _Headers(dict):
        def get(self, k: str, default: str = "") -> str:  # type: ignore[override]
            return super().get(k.lower(), default)

    h = _Headers({k.lower(): v for k, v in headers.items()})
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=h,
        state=SimpleNamespace(
            record_tenant_id=record_tenant_id,
            user_id=user_id,
        ),
        app=SimpleNamespace(state=SimpleNamespace(container=None)),
    )


async def _ok_call_next(_request: Any) -> Response:
    return PlainTextResponse("ok")


# ---------------------------------------------------------------------------
# 1. Sliding-window steady-state
# ---------------------------------------------------------------------------


class TestSteadyState:
    @pytest.mark.asyncio
    async def test_under_limit_passes(self) -> None:
        limiter = InMemorySlidingWindow()
        for i in range(5):
            d = await limiter.check(
                "k1", limit=10, window_s=60, burst_factor=1.0, burst_window_s=0,
            )
            assert d.allowed, f"req {i} blocked unexpectedly"
            assert d.remaining == 10 - (i + 1)

    @pytest.mark.asyncio
    async def test_at_limit_then_429(self) -> None:
        limiter = InMemorySlidingWindow()
        # Use small numbers — proves the algorithm without timing out.
        limit = 5
        for i in range(limit):
            d = await limiter.check(
                "k1", limit=limit, window_s=60,
                burst_factor=1.0, burst_window_s=0,
            )
            assert d.allowed, f"req {i} blocked early"
        d = await limiter.check(
            "k1", limit=limit, window_s=60, burst_factor=1.0, burst_window_s=0,
        )
        assert not d.allowed
        assert d.remaining == 0
        assert d.retry_after_s >= 1
        assert d.reset_unix > 0


# ---------------------------------------------------------------------------
# 2. Burst allowance
# ---------------------------------------------------------------------------


class TestBurst:
    @pytest.mark.asyncio
    async def test_burst_factor_lifts_ceiling(self) -> None:
        # Steady=5, burst factor 2 → 10 allowed in burst window.
        limiter = InMemorySlidingWindow()
        for i in range(10):
            d = await limiter.check(
                "k1", limit=5, window_s=60,
                burst_factor=2.0, burst_window_s=10,
            )
            assert d.allowed, f"burst {i} unexpectedly blocked"
        # 11th should be rejected (steady-state ceiling reached).
        d = await limiter.check(
            "k1", limit=5, window_s=60,
            burst_factor=2.0, burst_window_s=10,
        )
        assert not d.allowed

    @pytest.mark.asyncio
    async def test_burst_source_label(self) -> None:
        limiter = InMemorySlidingWindow()
        d = await limiter.check(
            "k1", limit=5, window_s=60,
            burst_factor=2.0, burst_window_s=10,
        )
        # First call → burst sub-window applies.
        assert d.source == "burst"


# ---------------------------------------------------------------------------
# 3. Per-endpoint policy resolution
# ---------------------------------------------------------------------------


class TestPolicyTable:
    def test_health_unlimited(self) -> None:
        assert resolve_policy("/health") is None
        assert resolve_policy("/health/models") is None
        assert resolve_policy("/metrics") is None

    def test_chat_uses_chat_policy(self) -> None:
        p = resolve_policy("/api/ragbot/test/chat")
        assert p is not None
        assert p.limit == DEFAULT_RL_CHAT_PER_MIN
        assert p.burst_factor > 1.0

    def test_admin_uses_admin_policy(self) -> None:
        p = resolve_policy("/api/ragbot/admin/tenants")
        assert p is not None
        assert p.limit == DEFAULT_RL_ADMIN_PER_MIN
        assert p.burst_factor == 1.0

    def test_unknown_falls_back_to_default(self) -> None:
        p = resolve_policy("/api/ragbot/unknown/path")
        assert p is not None
        assert isinstance(p, RateLimitPolicy)

    def test_list_policies_includes_default_marker(self) -> None:
        rows = list_policies()
        # Last entry is the synthetic <default> marker.
        assert rows[-1][0] == "<default>"


# ---------------------------------------------------------------------------
# 4. Per-endpoint counter isolation through middleware
# ---------------------------------------------------------------------------


class TestPerEndpointIsolation:
    @pytest.mark.asyncio
    async def test_chat_and_admin_counters_independent(self) -> None:
        limiter = InMemorySlidingWindow()
        mw = SlidingRateLimitMiddleware(
            app=lambda *_: None, limiter=limiter,
        )
        tid = uuid4()
        # Saturate /chat path.
        chat_path = "/api/ragbot/test/chat"
        # Drive enough to exceed steady-state without exhausting burst.
        for _ in range(DEFAULT_RL_CHAT_PER_MIN + 1):
            req = _make_request(
                method="POST", path=chat_path,
                record_tenant_id=tid, user_id="u1",
            )
            await mw.dispatch(req, _ok_call_next)
        # /admin path with same caller should still pass — different
        # endpoint key, different counter.
        admin_path = "/api/ragbot/admin/tenants"
        req = _make_request(
            method="GET", path=admin_path,
            record_tenant_id=tid, user_id="u1",
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. Cross-tenant isolation through middleware
# ---------------------------------------------------------------------------


class TestCrossTenantIsolation:
    @pytest.mark.asyncio
    async def test_two_tenants_independent_counters(self) -> None:
        limiter = InMemorySlidingWindow()
        mw = SlidingRateLimitMiddleware(
            app=lambda *_: None, limiter=limiter,
        )
        tid_a = uuid4()
        tid_b = uuid4()
        path = "/api/ragbot/admin/tenants"
        # Drain tenant A's admin allowance.
        for _ in range(DEFAULT_RL_ADMIN_PER_MIN):
            req = _make_request(
                method="GET", path=path,
                record_tenant_id=tid_a, user_id="ops",
            )
            await mw.dispatch(req, _ok_call_next)
        # Tenant A is now at limit — next call 429.
        req_a = _make_request(
            method="GET", path=path,
            record_tenant_id=tid_a, user_id="ops",
        )
        resp_a = await mw.dispatch(req_a, _ok_call_next)
        assert resp_a.status_code == 429
        # Tenant B with same user_id slug has its own counter — passes.
        req_b = _make_request(
            method="GET", path=path,
            record_tenant_id=tid_b, user_id="ops",
        )
        resp_b = await mw.dispatch(req_b, _ok_call_next)
        assert resp_b.status_code == 200


# ---------------------------------------------------------------------------
# 6. 429 response headers compliance
# ---------------------------------------------------------------------------


class TestResponseHeaders:
    @pytest.mark.asyncio
    async def test_429_carries_w3c_headers(self) -> None:
        limiter = InMemorySlidingWindow()
        mw = SlidingRateLimitMiddleware(
            app=lambda *_: None, limiter=limiter,
        )
        tid = uuid4()
        path = "/api/ragbot/admin/tenants"
        for _ in range(DEFAULT_RL_ADMIN_PER_MIN):
            req = _make_request(
                method="GET", path=path,
                record_tenant_id=tid, user_id="ops",
            )
            await mw.dispatch(req, _ok_call_next)
        req = _make_request(
            method="GET", path=path,
            record_tenant_id=tid, user_id="ops",
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 429
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers
        assert "Retry-After" in resp.headers
        assert int(resp.headers["X-RateLimit-Remaining"]) == 0
        assert int(resp.headers["Retry-After"]) >= 1

    @pytest.mark.asyncio
    async def test_success_response_carries_headers(self) -> None:
        limiter = InMemorySlidingWindow()
        mw = SlidingRateLimitMiddleware(
            app=lambda *_: None, limiter=limiter, emit_headers=True,
        )
        tid = uuid4()
        path = "/api/ragbot/admin/tenants"
        req = _make_request(
            method="GET", path=path,
            record_tenant_id=tid, user_id="ops",
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert int(resp.headers["X-RateLimit-Limit"]) > 0


# ---------------------------------------------------------------------------
# 7. Preflight OPTIONS bypass + unlimited paths bypass
# ---------------------------------------------------------------------------


class TestBypass:
    @pytest.mark.asyncio
    async def test_options_passes_through(self) -> None:
        limiter = InMemorySlidingWindow()
        mw = SlidingRateLimitMiddleware(app=lambda *_: None, limiter=limiter)
        for _ in range(200):
            req = _make_request(
                method="OPTIONS", path="/api/ragbot/test/chat",
                record_tenant_id=uuid4(), user_id="u1",
            )
            resp = await mw.dispatch(req, _ok_call_next)
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_path_passes_through(self) -> None:
        limiter = InMemorySlidingWindow()
        mw = SlidingRateLimitMiddleware(app=lambda *_: None, limiter=limiter)
        for _ in range(500):
            req = _make_request(
                method="GET", path="/health",
                record_tenant_id=None, user_id=None,
            )
            resp = await mw.dispatch(req, _ok_call_next)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 8. Fail-mode behaviour (closed vs open)
# ---------------------------------------------------------------------------


class _ExplodingLimiter(RateLimiterPort):
    """Limiter that always raises — drives fail-mode branch."""

    async def check(self, key: str, **_kw: Any) -> RateLimiterDecision:  # noqa: D401
        raise RuntimeError("backend down")


class TestFailMode:
    @pytest.mark.asyncio
    async def test_fail_closed_returns_503(self) -> None:
        mw = SlidingRateLimitMiddleware(
            app=lambda *_: None,
            limiter=_ExplodingLimiter(),
            fail_mode="closed",
        )
        req = _make_request(
            method="GET", path="/api/ragbot/admin/tenants",
            record_tenant_id=uuid4(), user_id="u1",
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_fail_open_passes_through(self) -> None:
        mw = SlidingRateLimitMiddleware(
            app=lambda *_: None,
            limiter=_ExplodingLimiter(),
            fail_mode="open",
        )
        req = _make_request(
            method="GET", path="/api/ragbot/admin/tenants",
            record_tenant_id=uuid4(), user_id="u1",
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 9. Soft-unlimited
# ---------------------------------------------------------------------------


class TestSoftUnlimited:
    @pytest.mark.asyncio
    async def test_zero_limit_never_throttles(self) -> None:
        limiter = InMemorySlidingWindow()
        for _ in range(1000):
            d = await limiter.check(
                "k1", limit=0, window_s=60,
                burst_factor=1.0, burst_window_s=0,
            )
            assert d.allowed
            assert d.source == "unlimited"


# ---------------------------------------------------------------------------
# 10. Registry — Strategy/DI surface
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_in_memory_provider(self) -> None:
        impl = build_rate_limiter("in_memory")
        assert isinstance(impl, InMemorySlidingWindow)

    def test_unknown_provider_falls_back(self) -> None:
        impl = build_rate_limiter("does_not_exist")
        # Falls back to in_memory with warn log.
        assert isinstance(impl, InMemorySlidingWindow)

    def test_list_providers_includes_redis(self) -> None:
        providers = list_providers()
        assert "redis_sliding" in providers
        assert "in_memory" in providers
