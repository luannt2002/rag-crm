"""Unit tests — :class:`SourceRateLimitMiddleware` (per-source-tag RL).

Phase 5 case study (2026-05-18). Scope key is the pair
``(record_tenant_id, source_tag)`` so KMS-A inside tenant T flooding
ingest cannot starve KMS-B inside the same tenant.

These tests cover the pure logic surfaces — tag resolve / truncate, key
derivation, path scope gate, header gate, tenant gate — plus dispatch
flow with a stub Redis client (under-cap pass-through, over-cap 429,
Redis-error degrade-open).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from redis.exceptions import RedisError
from starlette.responses import JSONResponse, Response

from ragbot.interfaces.http.middlewares.source_rate_limit import (
    SourceRateLimitMiddleware,
    _make_redis_key,
    _resolve_source_tag,
    _resolve_tenant,
)
from ragbot.config.settings import get_settings
from ragbot.shared.constants import (
    DEFAULT_SOURCE_RL_PER_MIN,
    DEFAULT_SOURCE_RL_WINDOW_S,
    SOURCE_RL_INGEST_PATH_SUFFIX,
    SOURCE_RL_TAG_MAX_LEN,
)

# Resolved at import time — mirrors the runtime default the middleware
# builds inside __init__ when path_prefix=None.
SOURCE_RL_INGEST_PATH_PREFIX = (
    f"{get_settings().app.api_base_path}{SOURCE_RL_INGEST_PATH_SUFFIX}"
)


# ---------------------------------------------------------------------
# Helpers — lightweight Request + Redis stand-ins.
# ---------------------------------------------------------------------


def _mock_request(
    *,
    path: str = SOURCE_RL_INGEST_PATH_PREFIX,
    state: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    redis_client: Any | None = None,
    container_attr: bool = True,
) -> MagicMock:
    """Build a request stand-in covering every field the middleware reads."""
    req = MagicMock()
    req.url.path = path
    req.headers = headers or {}
    state_ns = SimpleNamespace(**(state or {}))
    req.state = state_ns
    if container_attr:
        container = SimpleNamespace(redis_client=lambda: redis_client) if redis_client is not None else None
        req.app.state = SimpleNamespace(container=container)
    else:
        req.app.state = SimpleNamespace()
    return req


class _FakeRedis:
    """Minimal async Redis stub: in-process INCR counter + EXPIRE noop."""

    def __init__(self, *, raise_on_incr: Exception | None = None) -> None:
        self._counters: dict[str, int] = {}
        self._raise = raise_on_incr

    async def incr(self, key: str) -> int:
        if self._raise is not None:
            raise self._raise
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
        return True


async def _call_next_ok(request: Any) -> Response:  # noqa: ARG001
    return Response(status_code=200)


def _run(coro: Any) -> Any:
    """Synchronously drive an async dispatch under pytest."""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------------------------------------------------------------
# _resolve_source_tag — header extract + strip + truncate.
# ---------------------------------------------------------------------


def test_resolve_source_tag_strips_whitespace() -> None:
    """Leading/trailing whitespace stripped before bucket key build."""
    req = _mock_request(headers={"X-Source-Tag": "  kms-a  "})
    assert _resolve_source_tag(req) == "kms-a"


def test_resolve_source_tag_truncates_at_max_len() -> None:
    """Tag longer than :data:`SOURCE_RL_TAG_MAX_LEN` truncated to cap."""
    long_tag = "a" * (SOURCE_RL_TAG_MAX_LEN + 50)
    req = _mock_request(headers={"X-Source-Tag": long_tag})
    out = _resolve_source_tag(req)
    assert out is not None
    assert len(out) == SOURCE_RL_TAG_MAX_LEN


def test_resolve_source_tag_empty_returns_none() -> None:
    """Empty / whitespace-only header → None (caller bypasses)."""
    assert _resolve_source_tag(_mock_request(headers={"X-Source-Tag": ""})) is None
    assert _resolve_source_tag(_mock_request(headers={"X-Source-Tag": "   "})) is None


def test_resolve_source_tag_missing_returns_none() -> None:
    """Missing header → None."""
    assert _resolve_source_tag(_mock_request(headers={})) is None


# ---------------------------------------------------------------------
# _resolve_tenant — request.state lift.
# ---------------------------------------------------------------------


def test_resolve_tenant_from_uuid_state() -> None:
    """UUID instance in state coerced to str (Redis key is text)."""
    tenant_uuid = UUID("00000000-0000-0000-0000-0000000000aa")
    req = _mock_request(state={"record_tenant_id": tenant_uuid})
    assert _resolve_tenant(req) == str(tenant_uuid)


def test_resolve_tenant_missing_returns_none() -> None:
    """Pre-auth path (no state.record_tenant_id) → None."""
    assert _resolve_tenant(_mock_request(state={})) is None


# ---------------------------------------------------------------------
# _make_redis_key — 2-key isolation contract.
# ---------------------------------------------------------------------


def test_make_redis_key_shape() -> None:
    """Key encodes tenant + source_tag + bucket window."""
    key = _make_redis_key("tenant-A", "kms-a", 12345)
    assert key == "ragbot:rl:source:tenant-A:kms-a:12345"


def test_make_redis_key_isolation_between_source_tags() -> None:
    """Same tenant, two source tags → different keys (KMS-A vs KMS-B)."""
    a = _make_redis_key("tenant-A", "kms-a", 100)
    b = _make_redis_key("tenant-A", "kms-b", 100)
    assert a != b


def test_make_redis_key_isolation_between_tenants() -> None:
    """Same source tag, two tenants → different keys."""
    a = _make_redis_key("tenant-A", "kms-a", 100)
    b = _make_redis_key("tenant-B", "kms-a", 100)
    assert a != b


# ---------------------------------------------------------------------
# Dispatch — path scope gate.
# ---------------------------------------------------------------------


def test_dispatch_bypasses_when_path_outside_prefix() -> None:
    """Non-ingest paths (chat, admin, health) bypass — no Redis call."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    redis = _FakeRedis()
    req = _mock_request(
        path="/api/ragbot/test/bots/support/web/chat",
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        redis_client=redis,
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200
    assert redis._counters == {}  # Redis untouched.


def test_dispatch_engages_on_ingest_prefix() -> None:
    """Path starting with the configured prefix engages the gate."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    redis = _FakeRedis()
    req = _mock_request(
        path=f"{SOURCE_RL_INGEST_PATH_PREFIX}/csv",
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        redis_client=redis,
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200
    assert len(redis._counters) == 1  # INCR happened.


# ---------------------------------------------------------------------
# Dispatch — bypass gates.
# ---------------------------------------------------------------------


def test_dispatch_bypasses_when_header_missing() -> None:
    """No X-Source-Tag → bypass (per-token + per-IP still gate)."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    redis = _FakeRedis()
    req = _mock_request(
        headers={},  # no source tag
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        redis_client=redis,
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200
    assert redis._counters == {}


def test_dispatch_bypasses_when_tenant_missing() -> None:
    """Pre-auth ingest path (no tenant context) → bypass."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    redis = _FakeRedis()
    req = _mock_request(
        headers={"X-Source-Tag": "kms-a"},
        state={},  # no record_tenant_id
        redis_client=redis,
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200
    assert redis._counters == {}


def test_dispatch_bypasses_when_redis_unwired() -> None:
    """DI container missing (test env / pre-lifespan) → bypass."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    req = _mock_request(
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        container_attr=False,  # no app.state.container
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200


# ---------------------------------------------------------------------
# Dispatch — under cap → INCR + remaining header.
# ---------------------------------------------------------------------


def test_dispatch_under_cap_adds_remaining_header() -> None:
    """Single request under cap returns 200 + X-RateLimit-Source-Remaining."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    redis = _FakeRedis()
    req = _mock_request(
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        redis_client=redis,
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200
    remaining = int(resp.headers["X-RateLimit-Source-Remaining"])
    assert remaining == DEFAULT_SOURCE_RL_PER_MIN - 1


# ---------------------------------------------------------------------
# Dispatch — over cap → 429 + Retry-After + JSON body shape.
# ---------------------------------------------------------------------


def test_dispatch_over_cap_returns_429() -> None:
    """Saturating the bucket triggers a 429 with the documented contract."""
    # Tight cap so the test runs fast.
    mw = SourceRateLimitMiddleware(app=MagicMock(), per_min=2, window_s=60)
    redis = _FakeRedis()
    req_factory = lambda: _mock_request(  # noqa: E731 — local helper
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        redis_client=redis,
    )

    # First 2 pass.
    r1 = asyncio.run(mw.dispatch(req_factory(), _call_next_ok))
    r2 = asyncio.run(mw.dispatch(req_factory(), _call_next_ok))
    assert r1.status_code == 200
    assert r2.status_code == 200

    # 3rd → 429.
    r3 = asyncio.run(mw.dispatch(req_factory(), _call_next_ok))
    assert isinstance(r3, JSONResponse)
    assert r3.status_code == 429
    assert r3.headers["Retry-After"].isdigit()
    assert r3.headers["X-RateLimit-Source-Limit"] == "2"
    assert r3.headers["X-RateLimit-Source-Window"] == "60"
    assert r3.headers["X-RateLimit-Source-Remaining"] == "0"
    # Body shape — code + details.
    body = r3.body
    assert b"SOURCE_RATE_LIMIT_EXCEEDED" in body
    assert b"kms-a" in body


# ---------------------------------------------------------------------
# Dispatch — Redis error → degrade open (pass-through, no 5xx).
# ---------------------------------------------------------------------


def test_dispatch_degrade_open_on_redis_error() -> None:
    """Redis INCR raising :class:`RedisError` → pass through with 200."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    redis = _FakeRedis(raise_on_incr=RedisError("connection refused"))
    req = _mock_request(
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000001")},
        redis_client=redis,
    )
    resp = asyncio.run(mw.dispatch(req, _call_next_ok))
    assert resp.status_code == 200  # degraded open, no 5xx surfaced.


# ---------------------------------------------------------------------
# Dispatch — 2-key isolation: same source_tag, different tenants → different buckets.
# ---------------------------------------------------------------------


def test_dispatch_isolates_buckets_per_tenant() -> None:
    """Same source_tag across two tenants must not share a counter."""
    mw = SourceRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    redis = _FakeRedis()

    tenant_a = UUID("00000000-0000-0000-0000-0000000000a1")
    tenant_b = UUID("00000000-0000-0000-0000-0000000000b2")

    # Tenant A burns its bucket of 1.
    req_a = _mock_request(
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": tenant_a},
        redis_client=redis,
    )
    r_a1 = asyncio.run(mw.dispatch(req_a, _call_next_ok))
    assert r_a1.status_code == 200

    # Tenant B's first call must NOT see tenant A's counter — still 200.
    req_b = _mock_request(
        headers={"X-Source-Tag": "kms-a"},
        state={"record_tenant_id": tenant_b},
        redis_client=redis,
    )
    r_b1 = asyncio.run(mw.dispatch(req_b, _call_next_ok))
    assert r_b1.status_code == 200

    # And Redis holds two distinct keys.
    assert len(redis._counters) == 2


# ---------------------------------------------------------------------
# Construction defaults.
# ---------------------------------------------------------------------


def test_middleware_init_defaults_from_constants() -> None:
    """Defaults wired from :mod:`shared.constants` (zero-hardcode policy)."""
    mw = SourceRateLimitMiddleware(app=MagicMock())
    assert mw._per_min == DEFAULT_SOURCE_RL_PER_MIN
    assert mw._window_s == DEFAULT_SOURCE_RL_WINDOW_S
    assert mw._path_prefix == SOURCE_RL_INGEST_PATH_PREFIX
