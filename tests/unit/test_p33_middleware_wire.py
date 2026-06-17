"""Middleware Layer-1 wire integration unit tests.

The TenantContextMiddleware invokes ``TenantRateLimiter.check`` after the
JWT bypass-resolution block but before the per-service-token counter.
These tests exercise that decision matrix without touching the real
FastAPI surface — we drive the dispatch directly with a stubbed
container + Request/Response so the assertion is on the 429 vs 200
boundary rather than the limiter internals (those are covered in
``tests/unit/test_tenant_rate_limiter.py``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from ragbot.application.services.tenant_rate_limiter import (
    TenantRateLimitDecision,
)
from ragbot.interfaces.http.middlewares.tenant_context import (
    TenantContextMiddleware,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubRedis:
    """Minimal Redis stub — supports the get/set used by the middleware."""

    def __init__(self, *, bot_payload: dict | None = None) -> None:
        self._bot_payload = bot_payload
        self._sys: dict[str, str] = {}

    async def get(self, key: str) -> Any:
        if key.startswith("ragbot:bot:") and self._bot_payload:
            return json.dumps(self._bot_payload)
        return self._sys.get(key)

    async def incr(self, key: str) -> int:  # pragma: no cover - unused
        return 1

    async def expire(self, *_args: Any, **_kw: Any) -> bool:  # pragma: no cover
        return True


class _StubLimiter:
    """Records calls + returns canned decisions."""

    def __init__(self, decision: TenantRateLimitDecision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    async def check(self, **kwargs: Any) -> TenantRateLimitDecision:
        self.calls.append(kwargs)
        return self.decision


class _StubCfgCache:
    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg

    async def get(self, _tid: int) -> Any:
        return self._cfg


class _StubJwtSvc:
    def __init__(self, payload: dict | None) -> None:
        self._payload = payload

    async def verify_token(self, *_a: Any, **_kw: Any) -> dict | None:
        return self._payload


class _StubContainer:
    def __init__(
        self,
        *,
        redis: Any,
        limiter: Any | None,
        cfg_cache: Any | None,
        session_factory: Any | None = None,
    ) -> None:
        self._redis = redis
        self._limiter = limiter
        self._cfg_cache = cfg_cache
        self._sf = session_factory or (lambda: SimpleNamespace())

    def redis_client(self) -> Any:
        return self._redis

    def session_factory(self) -> Any:
        return self._sf

    def tenant_rate_limiter(self) -> Any:
        if self._limiter is None:
            raise AttributeError("not wired")
        return self._limiter

    def tenant_config_cache(self) -> Any:
        return self._cfg_cache

    def jwt_verifier(self) -> Any:
        raise AttributeError("not used in service-jwt path tests")


def _build_request(body: dict) -> Any:
    """Mock ``starlette.requests.Request`` with just the bits middleware uses."""

    class _Body:
        def __init__(self, payload: dict) -> None:
            self._b = json.dumps(payload).encode()

        async def __call__(self) -> bytes:
            return self._b

    state = SimpleNamespace(trace_id="t-1")
    headers = {"Authorization": "Bearer fake"}
    url = SimpleNamespace(path="/api/ragbot/test/chat")
    request = SimpleNamespace(
        url=url,
        headers=headers,
        state=state,
        body=_Body(body),
    )
    return request


async def _dispatch(
    middleware: TenantContextMiddleware,
    request: Any,
    container: Any,
    settings: Any,
) -> Any:
    request.app = SimpleNamespace(
        state=SimpleNamespace(
            container=container,
            settings=settings,
            dev_jwt_secret="dev-secret",  # noqa: S106
        ),
    )
    called = {"called": False}

    async def call_next(_req: Any) -> Any:
        called["called"] = True
        return SimpleNamespace(status_code=200, _called=True)

    response = await middleware.dispatch(request, call_next)
    return response, called["called"]


@pytest.fixture()
def settings() -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(api_token="dev-secret"),  # noqa: S106
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer1_blocks_when_limiter_returns_not_allowed(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """Limiter says blocked → middleware returns 429 + tenant_rate_limit_exceeded."""
    redis = _StubRedis()
    decision = TenantRateLimitDecision(
        allowed=False, bypass=False, source="tenant",
        limit=5, window_s=60, used=6,
    )
    limiter = _StubLimiter(decision)
    cfg_cache = _StubCfgCache(SimpleNamespace(
        bypass_rate_limit=False, rate_limit_per_min=5, monthly_token_cap=None,
    ))
    container = _StubContainer(redis=redis, limiter=limiter, cfg_cache=cfg_cache)

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.tenant_context.JwtTokenService",
        lambda **_: _StubJwtSvc({
            "sub": "svc-a", "role": "service", "tenant_id": 42,
            "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
            "rl_val": 999, "rl_win": 60,
        }),
    )

    request = _build_request({"bot_id": "b1", "tenant_id": 42})
    middleware = TenantContextMiddleware(app=lambda *_a, **_kw: None)
    response, called = await _dispatch(middleware, request, container, settings)
    assert called is False
    assert response.status_code == 429
    expected_uuid = UUID("c2f66cb2-9911-5d34-a46e-a4a6da068e23")
    assert limiter.calls and limiter.calls[0]["record_tenant_id"] == expected_uuid


@pytest.mark.asyncio
async def test_layer1_allows_when_limiter_allows(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """Allowed decision → request proceeds to handler."""
    redis = _StubRedis()
    decision = TenantRateLimitDecision(
        allowed=True, bypass=False, source="tenant",
        limit=10, window_s=60, used=1,
    )
    limiter = _StubLimiter(decision)
    cfg_cache = _StubCfgCache(SimpleNamespace(
        bypass_rate_limit=False, rate_limit_per_min=10, monthly_token_cap=None,
    ))
    container = _StubContainer(redis=redis, limiter=limiter, cfg_cache=cfg_cache)

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.tenant_context.JwtTokenService",
        lambda **_: _StubJwtSvc({
            "sub": "svc-a", "role": "service", "tenant_id": 42,
            "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
            "rl_val": 0, "rl_win": 60,  # owner-level — skips L1.5
        }),
    )

    request = _build_request({"bot_id": "b1", "tenant_id": 42})
    middleware = TenantContextMiddleware(app=lambda *_a, **_kw: None)
    response, called = await _dispatch(middleware, request, container, settings)
    assert called is True
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tenant_bypass_forwards_to_limiter_for_observability(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """tenant.bypass_rate_limit=True still calls limiter.check
    so the Redis counter increments (VIP visibility). The limiter then
    returns ``bypass=True, allowed=True`` and the request continues.
    """
    redis = _StubRedis()
    decision = TenantRateLimitDecision(
        allowed=True, bypass=True, source="tenant_bypass",
        limit=0, window_s=60, used=1,
    )
    limiter = _StubLimiter(decision)
    cfg_cache = _StubCfgCache(SimpleNamespace(
        bypass_rate_limit=True, rate_limit_per_min=1, monthly_token_cap=None,
    ))
    container = _StubContainer(redis=redis, limiter=limiter, cfg_cache=cfg_cache)

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.tenant_context.JwtTokenService",
        lambda **_: _StubJwtSvc({
            "sub": "svc-a", "role": "service", "tenant_id": 42,
            "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
            "rl_val": 0, "rl_win": 60,
        }),
    )

    request = _build_request({"bot_id": "b1", "tenant_id": 42})
    middleware = TenantContextMiddleware(app=lambda *_a, **_kw: None)
    response, called = await _dispatch(middleware, request, container, settings)
    assert called is True
    assert response.status_code == 200
    # limiter MUST be called and must receive tenant_bypass=True.
    assert len(limiter.calls) == 1
    assert limiter.calls[0]["tenant_bypass"] is True
    assert limiter.calls[0]["bot_bypass"] is False


@pytest.mark.asyncio
async def test_bot_bypass_forwards_to_limiter_for_observability(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """bot.bypass_rate_limit=True still calls limiter.check so
    the counter records the bypass event. limiter receives bot_bypass=True.
    """
    bot_payload = {"tenant_id": 42, "bypass_rate_limit": True}
    redis = _StubRedis(bot_payload=bot_payload)
    decision = TenantRateLimitDecision(
        allowed=True, bypass=True, source="bot_bypass",
        limit=0, window_s=60, used=1,
    )
    limiter = _StubLimiter(decision)
    cfg_cache = _StubCfgCache(SimpleNamespace(
        bypass_rate_limit=False, rate_limit_per_min=1, monthly_token_cap=None,
    ))
    container = _StubContainer(redis=redis, limiter=limiter, cfg_cache=cfg_cache)

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.tenant_context.JwtTokenService",
        lambda **_: _StubJwtSvc({
            "sub": "svc-a", "role": "service", "tenant_id": 42,
            "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
            "rl_val": 0, "rl_win": 60,
        }),
    )

    request = _build_request({"bot_id": "b1", "tenant_id": 42})
    middleware = TenantContextMiddleware(app=lambda *_a, **_kw: None)
    response, called = await _dispatch(middleware, request, container, settings)
    assert called is True
    assert response.status_code == 200
    # limiter MUST be called and must receive bot_bypass=True.
    assert len(limiter.calls) == 1
    assert limiter.calls[0]["bot_bypass"] is True
    assert limiter.calls[0]["tenant_bypass"] is False


@pytest.mark.asyncio
async def test_layer1_passes_tenant_limit_from_cfg_cache(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """Limit value loaded from TenantConfigCache flows into limiter.check kwargs."""
    redis = _StubRedis()
    decision = TenantRateLimitDecision(
        allowed=True, bypass=False, source="tenant",
        limit=77, window_s=60, used=1,
    )
    limiter = _StubLimiter(decision)
    cfg_cache = _StubCfgCache(SimpleNamespace(
        bypass_rate_limit=False, rate_limit_per_min=77, monthly_token_cap=10_000,
    ))
    container = _StubContainer(redis=redis, limiter=limiter, cfg_cache=cfg_cache)

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.tenant_context.JwtTokenService",
        lambda **_: _StubJwtSvc({
            "sub": "svc-a", "role": "service", "tenant_id": 42,
            "record_tenant_id": "c2f66cb2-9911-5d34-a46e-a4a6da068e23",
            "rl_val": 0, "rl_win": 60,
        }),
    )

    request = _build_request({"bot_id": "b1", "tenant_id": 42})
    middleware = TenantContextMiddleware(app=lambda *_a, **_kw: None)
    await _dispatch(middleware, request, container, settings)
    assert limiter.calls
    call = limiter.calls[0]
    assert call["tenant_limit"] == 77
    assert call["tenant_bypass"] is False
    assert call["bot_bypass"] is False


@pytest.mark.asyncio
async def test_unscoped_non_owner_token_rejected_at_gate(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """Z3-P1 fix: legacy non-owner token without tenant_id MUST be 401-rejected
    at the middleware gate. Previously emitted only a warning and proceeded —
    that bypassed the entire cross-tenant isolation contract for legacy tokens.

    Owner / super_admin remain allowed without tenant_id (covered by sister test
    test_unscoped_service_jwt_rejected.py)."""
    redis = _StubRedis()
    decision = TenantRateLimitDecision(
        allowed=False, bypass=False, source="fallback",
        limit=1, window_s=60, used=99,
    )
    limiter = _StubLimiter(decision)
    cfg_cache = _StubCfgCache(None)
    container = _StubContainer(redis=redis, limiter=limiter, cfg_cache=cfg_cache)

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.tenant_context.JwtTokenService",
        lambda **_: _StubJwtSvc({
            "sub": "svc-a", "role": "service",
            "rl_val": 0, "rl_win": 60,
        }),
    )

    request = _build_request({"bot_id": "b1"})
    middleware = TenantContextMiddleware(app=lambda *_a, **_kw: None)
    response, called = await _dispatch(middleware, request, container, settings)
    # Downstream `call_next` MUST NOT be invoked.
    assert called is False
    assert response.status_code == 401
    # Layer-1 rate limit MUST NOT be consulted — request rejected before then.
    assert limiter.calls == []
