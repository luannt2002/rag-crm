"""P33 — End-to-end Layer-1 wiring against a shared Redis double.

Exercises the resolved bypass chain (tenant > bot > none) + the
cross-tenant isolation contract through the actual ``TenantRateLimiter``
and ``TenantTokenMeter`` services that bootstrap.py wires up. Uses an
in-process Redis fake shared by both tenants so we can assert the bucket
keys never collide and a flip on one tenant doesn't move the other.
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.application.services.tenant_rate_limiter import TenantRateLimiter
from ragbot.application.services.tenant_token_meter import TenantTokenMeter


class _SharedFakeRedis:
    """Minimal Redis stub — INCR/EXPIRE + HINCRBY/HGETALL."""

    def __init__(self) -> None:
        self.kv: dict[str, int] = {}
        self.h: dict[str, dict[str, int]] = {}
        self.ttl: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.ttl[key] = ttl
        return True

    async def hincrby(self, key: str, field: str, n: int) -> int:
        h = self.h.setdefault(key, {})
        h[field] = h.get(field, 0) + int(n)
        return h[field]

    async def hgetall(self, key: str) -> dict[str, int]:
        return dict(self.h.get(key, {}))


@pytest.mark.asyncio
async def test_e2e_two_tenants_a_blocked_b_unaffected() -> None:
    """Tenant A spam → A blocked, B unaffected (cross-tenant isolation)."""
    redis = _SharedFakeRedis()
    limiter = TenantRateLimiter(redis)

    # Tenant A burns through its bucket
    for _ in range(3):
        d = await limiter.check(tenant_id=1, tenant_limit=3)
        assert d.allowed is True
    blocked = await limiter.check(tenant_id=1, tenant_limit=3)
    assert blocked.allowed is False
    assert blocked.source == "tenant"

    # Tenant B unaffected — fresh bucket
    d = await limiter.check(tenant_id=2, tenant_limit=3)
    assert d.allowed is True
    assert d.used == 1


@pytest.mark.asyncio
async def test_e2e_tenant_bypass_unblocks_a_does_not_grant_b_extra() -> None:
    """Flipping bypass on A unblocks A; B's counter is untouched."""
    redis = _SharedFakeRedis()
    limiter = TenantRateLimiter(redis)
    # Pre-fill A
    for _ in range(3):
        await limiter.check(tenant_id=1, tenant_limit=3)
    # A bypass → unblocked even with limit=3
    out = await limiter.check(
        tenant_id=1, tenant_limit=3, tenant_bypass=True,
    )
    assert out.allowed is True
    assert out.bypass is True
    # B still independent
    out_b = await limiter.check(tenant_id=2, tenant_limit=3)
    assert out_b.allowed is True


@pytest.mark.asyncio
async def test_e2e_meter_increments_attribute_to_correct_tenant() -> None:
    """Meter.increment_tokens for tenant A doesn't leak into B's bucket."""
    redis = _SharedFakeRedis()
    meter = TenantTokenMeter(redis)
    await meter.increment_tokens(1, prompt_tokens=100, completion_tokens=50)
    await meter.increment_tokens(2, prompt_tokens=10, completion_tokens=5)
    a = await meter.get_monthly_usage(1)
    b = await meter.get_monthly_usage(2)
    assert a["total"] == 150
    assert b["total"] == 15


@pytest.mark.asyncio
async def test_e2e_bot_bypass_is_per_call_not_persistent() -> None:
    """bot_bypass=True for one call must not leak into the next call.

    Simulates middleware passing bot_bypass derived from registry payload
    on call 1, then NOT-bypass on call 2 (different bot).
    """
    redis = _SharedFakeRedis()
    limiter = TenantRateLimiter(redis)
    # Call 1 — bot bypass; counter untouched
    d1 = await limiter.check(
        tenant_id=99, tenant_limit=2, bot_bypass=True,
    )
    assert d1.bypass is True
    # Call 2 — sibling bot, no bypass; counter starts at 0
    d2 = await limiter.check(tenant_id=99, tenant_limit=2)
    assert d2.allowed is True
    assert d2.used == 1
