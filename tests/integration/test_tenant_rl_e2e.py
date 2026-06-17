"""P33 — Per-tenant rate limiter integration scenarios.

Exercises the cross-tenant isolation contract end-to-end against the
shared Redis double: tenant A spamming must not consume tenant B's
quota; flipping ``tenants.bypass_rate_limit`` for A unblocks A while
leaving B's bucket untouched.
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.application.services.tenant_rate_limiter import (
    TenantRateLimiter,
    check_tenant_rate_limit,
)


class _SharedFakeRedis:
    """Single Redis instance shared across both tenants."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def expire(self, *_args: Any, **_kw: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_tenant_a_spam_does_not_block_tenant_b() -> None:
    redis = _SharedFakeRedis()
    limiter = TenantRateLimiter(redis)
    # Tenant A burns through its bucket
    for _ in range(5):
        decision = await limiter.check(tenant_id=1, tenant_limit=5)
        assert decision.allowed is True
    blocked = await limiter.check(tenant_id=1, tenant_limit=5)
    assert blocked.allowed is False

    # Tenant B is independent
    decision = await limiter.check(tenant_id=2, tenant_limit=5)
    assert decision.allowed is True
    assert decision.used == 1


@pytest.mark.asyncio
async def test_tenant_bypass_unblocks_after_lockout() -> None:
    """Flipping bypass=TRUE on tenant A skips the counter regardless of state."""
    redis = _SharedFakeRedis()
    limiter = TenantRateLimiter(redis)
    # Burn through limit first
    for _ in range(5):
        await limiter.check(tenant_id=1, tenant_limit=5)
    blocked = await limiter.check(tenant_id=1, tenant_limit=5)
    assert blocked.allowed is False

    # Admin flips bypass — next call must pass
    unblocked = await limiter.check(
        tenant_id=1, tenant_limit=5, tenant_bypass=True,
    )
    assert unblocked.allowed is True
    assert unblocked.bypass is True
    assert unblocked.source == "tenant_bypass"


@pytest.mark.asyncio
async def test_bot_bypass_does_not_unblock_other_bots_of_same_tenant() -> None:
    """Luồng B is bot-specific — only that bot escapes, others still limited.

    The middleware OR-merges per-(tenant, bot) so a tenant's bot with
    bypass=TRUE escapes; a sibling bot still hits the tenant counter.
    """
    redis = _SharedFakeRedis()
    limiter = TenantRateLimiter(redis)

    # Bot 1 has bypass — 100 calls all pass
    for _ in range(100):
        decision = await limiter.check(
            tenant_id=42, tenant_limit=3, bot_bypass=True,
        )
        assert decision.allowed is True
        assert decision.bypass is True

    # Bot 2 (no bypass) shares the same tenant counter,
    # which is still at 0 (bypass calls didn't increment).
    for _ in range(3):
        decision = await limiter.check(
            tenant_id=42, tenant_limit=3, bot_bypass=False,
        )
        assert decision.allowed is True
    blocked = await limiter.check(
        tenant_id=42, tenant_limit=3, bot_bypass=False,
    )
    assert blocked.allowed is False


@pytest.mark.asyncio
async def test_helper_returns_bool_consistent_with_decision() -> None:
    redis = _SharedFakeRedis()
    # Helper-style API: check_tenant_rate_limit -> bool
    assert await check_tenant_rate_limit(redis, 99, tenant_limit=2) is True
    assert await check_tenant_rate_limit(redis, 99, tenant_limit=2) is True
    assert await check_tenant_rate_limit(redis, 99, tenant_limit=2) is False
