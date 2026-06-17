"""Unit tests — ``/api/ragbot/admin/rate-limits/inspect`` route.

Partner-facing inspection endpoint (case study 2026-05-16). Covers:

- ``_read_bucket``: Redis read with degrade-open semantics. Diagnostic
  endpoint never 5xx-es because a Redis blip — partner gets 0 instead.
- Key derivation matches the middleware's :func:`_make_redis_key`
  contract — otherwise the consumption number reported is wrong.

Auth / RBAC gating is enforced by ``require_min_level(request, 60)``
and FastAPI dependency wiring; that path is covered by the existing
RBAC test matrix.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from ragbot.interfaces.http.routes.admin_rate_limits import (
    _BOT_RL_PREFIX,
    _read_bucket,
)


# ---------------------------------------------------------------------
# _read_bucket — key derivation + degrade-open contract.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_bucket_returns_int_on_redis_hit() -> None:
    """Redis returns bytes ``b"42"`` → endpoint returns ``int(42)``."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"42")
    n = await _read_bucket(
        redis,
        record_tenant_id="t-A", workspace_id="ws-prod",
        bot_id="support", channel_type="web",
        window_s=60,
    )
    assert n == 42


@pytest.mark.asyncio
async def test_read_bucket_returns_zero_on_missing_key() -> None:
    """Empty window (no traffic yet) → key absent → 0."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    n = await _read_bucket(
        redis,
        record_tenant_id="t", workspace_id="w", bot_id="b", channel_type="c",
        window_s=60,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_read_bucket_degrades_open_on_redis_error() -> None:
    """Diagnostic endpoint MUST NOT 5xx on Redis blip — return 0 so the
    partner sees ``current_count=0`` rather than the whole inspect call
    failing."""
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=RedisError("connection reset"))
    n = await _read_bucket(
        redis,
        record_tenant_id="t", workspace_id="w", bot_id="b", channel_type="c",
        window_s=60,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_read_bucket_degrades_open_on_timeout() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=asyncio.TimeoutError())
    assert (
        await _read_bucket(
            redis,
            record_tenant_id="t", workspace_id="w", bot_id="b", channel_type="c",
            window_s=60,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_read_bucket_returns_zero_on_garbage_redis_value() -> None:
    """Redis returns non-integer bytes (manual tampering / version drift)
    → degrade open + return 0 rather than crashing the endpoint."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"not-an-int")
    n = await _read_bucket(
        redis,
        record_tenant_id="t", workspace_id="w", bot_id="b", channel_type="c",
        window_s=60,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_read_bucket_uses_4key_namespaced_key() -> None:
    """Key composed of 4-key tuple + bucket — must match the prefix the
    middleware writes to (``ragbot:rl:bot:``)."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"7")
    await _read_bucket(
        redis,
        record_tenant_id="t1", workspace_id="ws", bot_id="bot1", channel_type="web",
        window_s=60,
    )
    called_key = redis.get.await_args.args[0]
    assert called_key.startswith(_BOT_RL_PREFIX)
    # All 4 identity dimensions present in the key
    for part in ("t1", "ws", "bot1", "web"):
        assert f":{part}:" in called_key or called_key.endswith(f":{part}") or f"{part}:" in called_key
