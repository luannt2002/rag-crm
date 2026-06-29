"""Unit tests — loadtest-bypass coverage on the 3 rate-limit middlewares.

The operator-issued loadtest token (loopback-only) already short-circuits
``IpRateLimitMiddleware`` + the anti-abuse 4xx counter. This suite locks
the same bypass into the three remaining rate-limit layers so an internal
load test running from loopback with the token never trips a 429/503:

- :class:`BotRateLimitMiddleware`  — per-4-key bot cap.
- :class:`SourceRateLimitMiddleware` — per-source-tag ingest cap.
- :class:`SlidingRateLimitMiddleware` — per-token sliding-window cap.

Contract under test (mirrors ``is_loadtest_bypass`` gates):

1. Token header matches ``RAGBOT_LOADTEST_BYPASS_TOKEN`` env + peer is
   loopback → the cap is skipped (request passes through with 200).
2. No token (or wrong token, or non-loopback peer) → the cap still
   fires (the layer is NOT disabled in production).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from starlette.responses import Response

from ragbot.application.ports.rate_limiter_port import RateLimiterPort
from ragbot.config.settings import get_settings
from ragbot.interfaces.http.middlewares.bot_rate_limit import (
    BotRateLimitMiddleware,
)
from ragbot.interfaces.http.middlewares.rate_limit import (
    SlidingRateLimitMiddleware,
)
from ragbot.interfaces.http.middlewares.source_rate_limit import (
    SourceRateLimitMiddleware,
)
from ragbot.shared.constants import (
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
    SOURCE_RL_INGEST_PATH_SUFFIX,
)

_TOKEN = "operator-loadtest-secret-not-real"  # noqa: S105 — fixture string, not a credential
_LOOPBACK = "127.0.0.1"
_PUBLIC = "203.0.113.7"  # TEST-NET-3 documentation range — never loopback

_INGEST_PREFIX = (
    f"{get_settings().app.api_base_path}{SOURCE_RL_INGEST_PATH_SUFFIX}"
)
_TENANT = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------
# Request stand-in + fakes.
# ---------------------------------------------------------------------


def _mock_request(
    *,
    path: str,
    peer: str = _LOOPBACK,
    bypass_token: str | None = None,
    state: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    redis_client: Any | None = None,
) -> MagicMock:
    """Build a request stand-in covering every field the bypass + caps read."""
    req = MagicMock()
    req.url.path = path
    req.method = "POST"
    hdrs = dict(headers or {})
    if bypass_token is not None:
        hdrs[RAGBOT_LOADTEST_BYPASS_HEADER] = bypass_token
    req.headers = hdrs
    req.state = SimpleNamespace(**(state or {}))
    client = MagicMock()
    client.host = peer
    req.client = client
    container = (
        SimpleNamespace(redis_client=lambda: redis_client)
        if redis_client is not None
        else None
    )
    req.app.state = SimpleNamespace(container=container)
    return req


class _SaturatedRedis:
    """Async Redis stub whose counter is already over any sane cap."""

    async def incr(self, key: str) -> int:  # noqa: ARG002
        return 10_000

    async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
        return True


class _DenyLimiter(RateLimiterPort):
    """Always-deny limiter — proves the sliding layer would 429 absent bypass."""

    async def check(self, key: str, **kwargs: Any) -> Any:  # noqa: ARG002
        return SimpleNamespace(
            allowed=False,
            limit=kwargs.get("limit", 1),
            remaining=0,
            reset_unix=0,
            retry_after_s=60,
            used=10_000,
            source="window",
        )


async def _call_next_ok(request: Any) -> Response:  # noqa: ARG001
    return Response(status_code=200)


# ---------------------------------------------------------------------
# BotRateLimitMiddleware.
# ---------------------------------------------------------------------


def _bot_request(*, peer: str = _LOOPBACK, bypass_token: str | None = None) -> MagicMock:
    return _mock_request(
        path="/api/ragbot/test/bots/support/web/chat",
        peer=peer,
        bypass_token=bypass_token,
        state={"record_tenant_id": _TENANT},
        redis_client=_SaturatedRedis(),
    )


def test_bot_rl_token_loopback_skips_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid token from loopback → over-cap counter ignored, 200 passes."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = BotRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(mw.dispatch(_bot_request(bypass_token=_TOKEN), _call_next_ok))
    assert resp.status_code == 200


def test_bot_rl_no_token_still_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → saturated bucket trips the documented 429."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = BotRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(mw.dispatch(_bot_request(), _call_next_ok))
    assert resp.status_code == 429


def test_bot_rl_token_non_loopback_still_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token presented from a public peer → bypass denied, 429 stands."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = BotRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(
        mw.dispatch(_bot_request(peer=_PUBLIC, bypass_token=_TOKEN), _call_next_ok),
    )
    assert resp.status_code == 429


def test_bot_rl_env_unset_token_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env unset (production default) → token header is inert, 429 stands."""
    monkeypatch.delenv(RAGBOT_LOADTEST_BYPASS_ENV, raising=False)
    mw = BotRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(mw.dispatch(_bot_request(bypass_token=_TOKEN), _call_next_ok))
    assert resp.status_code == 429


# ---------------------------------------------------------------------
# SourceRateLimitMiddleware.
# ---------------------------------------------------------------------


def _source_request(*, peer: str = _LOOPBACK, bypass_token: str | None = None) -> MagicMock:
    return _mock_request(
        path=_INGEST_PREFIX,
        peer=peer,
        bypass_token=bypass_token,
        state={"record_tenant_id": _TENANT},
        headers={"X-Source-Tag": "kms-a"},
        redis_client=_SaturatedRedis(),
    )


def test_source_rl_token_loopback_skips_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid token from loopback → over-cap counter ignored, 200 passes."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = SourceRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(
        mw.dispatch(_source_request(bypass_token=_TOKEN), _call_next_ok),
    )
    assert resp.status_code == 200


def test_source_rl_no_token_still_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → saturated bucket trips the documented 429."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = SourceRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(mw.dispatch(_source_request(), _call_next_ok))
    assert resp.status_code == 429


def test_source_rl_token_non_loopback_still_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token from a public peer → bypass denied, 429 stands."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = SourceRateLimitMiddleware(app=MagicMock(), per_min=1, window_s=60)
    resp = asyncio.run(
        mw.dispatch(_source_request(peer=_PUBLIC, bypass_token=_TOKEN), _call_next_ok),
    )
    assert resp.status_code == 429


# ---------------------------------------------------------------------
# SlidingRateLimitMiddleware (per-token).
# ---------------------------------------------------------------------


def _sliding_request(*, peer: str = _LOOPBACK, bypass_token: str | None = None) -> MagicMock:
    return _mock_request(
        path="/api/ragbot/chat",
        peer=peer,
        bypass_token=bypass_token,
        state={"record_tenant_id": _TENANT, "user_id": "user-1"},
    )


def test_sliding_rl_token_loopback_skips_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid token from loopback → deny-limiter never consulted, 200 passes."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = SlidingRateLimitMiddleware(app=MagicMock(), limiter=_DenyLimiter())
    resp = asyncio.run(
        mw.dispatch(_sliding_request(bypass_token=_TOKEN), _call_next_ok),
    )
    assert resp.status_code == 200


def test_sliding_rl_no_token_still_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → deny-limiter fires the 429."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = SlidingRateLimitMiddleware(app=MagicMock(), limiter=_DenyLimiter())
    resp = asyncio.run(mw.dispatch(_sliding_request(), _call_next_ok))
    assert resp.status_code == 429


def test_sliding_rl_token_non_loopback_still_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token from a public peer → bypass denied, deny-limiter 429 stands."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    mw = SlidingRateLimitMiddleware(app=MagicMock(), limiter=_DenyLimiter())
    resp = asyncio.run(
        mw.dispatch(_sliding_request(peer=_PUBLIC, bypass_token=_TOKEN), _call_next_ok),
    )
    assert resp.status_code == 429
