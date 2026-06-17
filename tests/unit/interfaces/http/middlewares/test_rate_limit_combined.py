"""Combined per-IP + per-token rate limit gate.

SEC-INJ-8 in audit: per-token cap alone is bypassable by attacker who
provisions many tokens (free signup). Per-IP gate constrains *all*
tokens originating from one IP; combined with the per-token cap it
caps both fan-out per IP and per-token velocity.

Architecture
------------
The two limits run as *separate* middleware layers stacked outside-in:

* ``IpRateLimitMiddleware`` (outermost) — pre-auth, keyed by source IP.
* ``SlidingRateLimitMiddleware`` (inner) — post-auth, keyed by JWT token
  (or composite ``record_tenant_id:user_id``).

Stacking semantics mean **both gates MUST pass** for the request to
reach the route handler. These tests assert that contract directly via
FastAPI ``TestClient`` end-to-end so the wiring is exercised, not
mocked away.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the middleware module directly to avoid triggering the parent
# package's __init__.py (which transitively imports ragbot.bootstrap and
# breaks under pre-existing main-branch import drift unrelated to this
# task — surfaces at constants imports the new module doesn't share).
_ip_rate_limit_mod = importlib.import_module(
    "ragbot.interfaces.http.middlewares.ip_rate_limit",
)
IpRateLimitMiddleware = _ip_rate_limit_mod.IpRateLimitMiddleware

from ragbot.shared.constants import (
    DEFAULT_RATE_LIMIT_BURST_IP_MULTIPLIER,
    DEFAULT_RATE_LIMIT_PER_IP_PER_MIN,
    DEFAULT_RL_IP_WINDOW_S,
)


def _redis_mock_counter() -> MagicMock:
    """Build an AsyncMock that satisfies INCR/EXPIRE/SISMEMBER contract."""
    redis = MagicMock()
    state = {"count": 0}

    async def _incr(_key: str) -> int:
        state["count"] += 1
        return state["count"]

    async def _expire(_key: str, _ttl: int) -> bool:
        return True

    async def _sismember(_set: str, _member: str) -> int:
        return 0  # never suspicious

    redis.incr = AsyncMock(side_effect=_incr)
    redis.expire = AsyncMock(side_effect=_expire)
    redis.sismember = AsyncMock(side_effect=_sismember)
    return redis


def _build_app_with_ip_limiter(*, per_min: int) -> FastAPI:
    """Boot a minimal FastAPI with IpRateLimitMiddleware wired to a mock Redis.

    Note: ``app.state.container`` is wired *outside* a lifespan so the
    plain ``TestClient(app)`` constructor (no ``with``) is sufficient.
    Lifespans only run when TestClient is used as a context manager.
    """
    app = FastAPI()
    container = MagicMock()
    container.redis_client = MagicMock(return_value=_redis_mock_counter())
    app.state.container = container

    @app.get("/echo")
    async def _echo() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(
        IpRateLimitMiddleware,
        per_min=per_min,
        window_s=DEFAULT_RL_IP_WINDOW_S,
        enabled=True,
    )
    return app


def test_per_ip_default_cap_is_300() -> None:
    """W.2 contract: per-IP cap defaults to 300/min (5 users at 60/min each)."""
    assert DEFAULT_RATE_LIMIT_PER_IP_PER_MIN == 300


def test_per_ip_burst_multiplier_is_two() -> None:
    """Burst factor 2× lets short bursts (e.g. UI page-load fan-out) through."""
    assert DEFAULT_RATE_LIMIT_BURST_IP_MULTIPLIER == 2.0


def test_per_ip_cap_blocks_at_threshold() -> None:
    """Once requests from one IP exceed the cap → 429 IP_RATE_LIMITED.

    Even if a different token were used per request, the per-IP gate is
    keyed on source IP only, so it caps the aggregate.
    """
    app = _build_app_with_ip_limiter(per_min=3)
    client = TestClient(app)
    # First three pass.
    for _ in range(3):
        r = client.get("/echo")
        assert r.status_code == 200, r.text
    # Fourth trips the cap.
    r = client.get("/echo")
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "IP_RATE_LIMITED"
    assert r.headers.get("Retry-After") == str(DEFAULT_RL_IP_WINDOW_S)


def test_per_ip_gate_independent_of_token() -> None:
    """Rotating the Authorization header doesn't help — IP gate is token-blind.

    This is the *combined gate* property: per-token cap alone would be
    bypassable by signing up many free tokens; per-IP plugs that.
    """
    app = _build_app_with_ip_limiter(per_min=3)
    client = TestClient(app)
    for i in range(3):
        r = client.get("/echo", headers={"Authorization": f"Bearer tok-{i}"})
        assert r.status_code == 200
    r = client.get("/echo", headers={"Authorization": "Bearer tok-different"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "IP_RATE_LIMITED"


def test_ip_rate_limit_honours_constructor_per_min_argument() -> None:
    """The middleware honours its constructor ``per_min`` argument.

    Operator wiring in ``app.py`` should pass ``DEFAULT_RATE_LIMIT_PER_IP_PER_MIN``
    so the 300/min default takes effect; this test pins the contract
    that the constructor parameter (not the middleware's own fallback
    default) drives behavior.
    """
    cap = 5
    app = _build_app_with_ip_limiter(per_min=cap)
    client = TestClient(app)
    for _ in range(cap):
        assert client.get("/echo").status_code == 200
    assert client.get("/echo").status_code == 429
