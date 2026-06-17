"""Per-tenant rate limiter unit tests.

Covers the resolver precedence (4 paths × 2 bypass channels) plus the
counter behaviour (under/over the limit, sliding window, Redis error
fail-open). Uses a tiny in-process FakeRedis matching the patterns
already used in tests/unit/test_redis_streams_recovery.py.

Bypass channels ALWAYS INCR the counter so admin dashboards retain
visibility of VIP / internal-partner traffic; only the ``allowed`` and
``bypass`` flags differ.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from redis.exceptions import RedisError

from ragbot.application.services.tenant_rate_limiter import (
    TenantRateLimiter,
    check_tenant_rate_limit,
)
from ragbot.infrastructure.observability.metrics import (
    rate_limit_bypass_observed_total,
)
from ragbot.infrastructure.observability.prometheus_metrics_adapter import (
    PrometheusMetricsAdapter,
)
from ragbot.shared.constants import (
    DEFAULT_TENANT_RATE_LIMIT_PER_MIN,
    DEFAULT_TENANT_RATE_LIMIT_WINDOW_S,
)
from tests.conftest import TEST_TENANT_UUID, TEST_TENANT_2_UUID


class _FakeRedis:
    """Minimal async Redis double — INCR/EXPIRE only."""

    def __init__(self, *, fail: bool = False) -> None:
        self._store: dict[str, int] = {}
        self._ttl: dict[str, int] = {}
        self._fail = fail
        self.calls: list[tuple[str, str]] = []

    async def incr(self, key: str) -> int:
        if self._fail:
            raise RedisError("redis down")
        self.calls.append(("incr", key))
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        if self._fail:
            raise RedisError("redis down")
        self.calls.append(("expire", key))
        self._ttl[key] = ttl
        return True


# ---------------------------------------------------------------------------
# Pure resolver — no I/O
# ---------------------------------------------------------------------------

def test_resolver_tenant_bypass_short_circuits() -> None:
    limit, source, bypass = TenantRateLimiter.resolve_effective(
        tenant_bypass=True, bot_bypass=False,
        tenant_limit=10, system_limit=100,
    )
    assert bypass is True
    assert source == "tenant_bypass"
    assert limit == 0  # bypass returns 0 sentinel


def test_resolver_bot_bypass_short_circuits_when_tenant_off() -> None:
    limit, source, bypass = TenantRateLimiter.resolve_effective(
        tenant_bypass=False, bot_bypass=True,
        tenant_limit=10, system_limit=100,
    )
    assert bypass is True
    assert source == "bot_bypass"


def test_resolver_tenant_bypass_wins_when_both_true() -> None:
    """OR-merge — tenant flag wins for source label (checked first)."""
    _, source, bypass = TenantRateLimiter.resolve_effective(
        tenant_bypass=True, bot_bypass=True,
        tenant_limit=None, system_limit=None,
    )
    assert bypass is True
    assert source == "tenant_bypass"


def test_resolver_tenant_value_overrides_system() -> None:
    limit, source, bypass = TenantRateLimiter.resolve_effective(
        tenant_bypass=False, bot_bypass=False,
        tenant_limit=42, system_limit=999,
    )
    assert limit == 42
    assert source == "tenant"
    assert bypass is False


def test_resolver_falls_back_to_system_when_tenant_null() -> None:
    limit, source, _ = TenantRateLimiter.resolve_effective(
        tenant_bypass=False, bot_bypass=False,
        tenant_limit=None, system_limit=999,
    )
    assert limit == 999
    assert source == "system"


def test_resolver_falls_back_to_default_when_both_null() -> None:
    limit, source, _ = TenantRateLimiter.resolve_effective(
        tenant_bypass=False, bot_bypass=False,
        tenant_limit=None, system_limit=None,
    )
    assert limit == DEFAULT_TENANT_RATE_LIMIT_PER_MIN
    assert source == "fallback"


def test_resolver_tenant_zero_means_soft_unlimited() -> None:
    """tenant.rate_limit_per_min=0 — explicit unlimited, NOT bypass."""
    limit, source, bypass = TenantRateLimiter.resolve_effective(
        tenant_bypass=False, bot_bypass=False,
        tenant_limit=0, system_limit=999,
    )
    assert limit == 0
    assert source == "tenant"
    assert bypass is False  # different actor / different audit dimension


# ---------------------------------------------------------------------------
# Counter behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_under_limit_allows() -> None:
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    decision = await limiter.check(
        record_tenant_id=TEST_TENANT_UUID, tenant_limit=5, system_limit=None,
    )
    assert decision.allowed is True
    assert decision.bypass is False
    assert decision.used == 1
    assert decision.limit == 5
    assert decision.source == "tenant"


@pytest.mark.asyncio
async def test_check_over_limit_blocks() -> None:
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    # 5 allowed, 6th must be rejected
    for _ in range(5):
        decision = await limiter.check(
            record_tenant_id=TEST_TENANT_UUID, tenant_limit=5, system_limit=None,
        )
        assert decision.allowed is True
    decision = await limiter.check(
        record_tenant_id=TEST_TENANT_UUID, tenant_limit=5, system_limit=None,
    )
    assert decision.allowed is False
    assert decision.used == 6


@pytest.mark.asyncio
async def test_tenant_bypass_increments_counter_for_observability() -> None:
    """tenant bypass MUST INCR Redis counter (VIP visibility)."""
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    decision = await limiter.check(
        record_tenant_id=TEST_TENANT_UUID,
        tenant_bypass=True,
        tenant_limit=1,  # would otherwise block 2nd call
    )
    assert decision.allowed is True
    assert decision.bypass is True
    assert decision.source == "tenant_bypass"
    assert decision.used >= 1  # counter touched
    # Real Redis ops happened — INCR + EXPIRE on first hit.
    assert any(op == "incr" for op, _ in redis.calls)
    assert any(op == "expire" for op, _ in redis.calls)
    # Key contains the tenant UUID — cross-tenant isolation preserved.
    incr_key = next(k for op, k in redis.calls if op == "incr")
    assert str(TEST_TENANT_UUID) in incr_key


@pytest.mark.asyncio
async def test_bot_bypass_increments_counter_for_observability() -> None:
    """bot bypass MUST INCR Redis counter (VIP visibility)."""
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    decision = await limiter.check(
        record_tenant_id=TEST_TENANT_UUID, bot_bypass=True, tenant_limit=1,
    )
    assert decision.allowed is True
    assert decision.bypass is True
    assert decision.source == "bot_bypass"
    assert decision.used >= 1
    assert any(op == "incr" for op, _ in redis.calls)


@pytest.mark.asyncio
async def test_bypass_repeated_calls_keep_incrementing() -> None:
    """Bypass never returns 429 even when traffic exceeds the would-be limit."""
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    used_seq: list[int] = []
    for _ in range(7):
        decision = await limiter.check(
            record_tenant_id=TEST_TENANT_UUID,
            tenant_bypass=True,
            tenant_limit=2,
        )
        assert decision.allowed is True  # never blocked
        assert decision.bypass is True
        used_seq.append(decision.used)
    # Counter monotonically grows — operators see all 7 calls.
    assert used_seq == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_bypass_emits_observed_metric_with_correct_labels() -> None:
    """bypass events bump the new bypass-observed counter."""
    redis = _FakeRedis()
    # Wire the prometheus adapter explicitly — the limiter now takes
    # metrics via ``MetricsPort`` (Issue #7 hexagonal-boundary fix).
    limiter = TenantRateLimiter(redis, metrics=PrometheusMetricsAdapter())
    # Snapshot before
    before_tenant = rate_limit_bypass_observed_total.labels(
        tenant_id=str(TEST_TENANT_UUID), source="tenant_bypass",
    )._value.get()
    before_bot = rate_limit_bypass_observed_total.labels(
        tenant_id=str(TEST_TENANT_UUID), source="bot_bypass",
    )._value.get()

    await limiter.check(
        record_tenant_id=TEST_TENANT_UUID, tenant_bypass=True, tenant_limit=1,
    )
    await limiter.check(
        record_tenant_id=TEST_TENANT_UUID, bot_bypass=True, tenant_limit=1,
    )

    after_tenant = rate_limit_bypass_observed_total.labels(
        tenant_id=str(TEST_TENANT_UUID), source="tenant_bypass",
    )._value.get()
    after_bot = rate_limit_bypass_observed_total.labels(
        tenant_id=str(TEST_TENANT_UUID), source="bot_bypass",
    )._value.get()
    assert after_tenant - before_tenant == 1
    assert after_bot - before_bot == 1


@pytest.mark.asyncio
async def test_check_tenant_rate_limit_helper() -> None:
    """Convenience wrapper returns a plain bool."""
    redis = _FakeRedis()
    assert await check_tenant_rate_limit(redis, TEST_TENANT_UUID, tenant_limit=2) is True
    assert await check_tenant_rate_limit(redis, TEST_TENANT_UUID, tenant_limit=2) is True
    assert await check_tenant_rate_limit(redis, TEST_TENANT_UUID, tenant_limit=2) is False


@pytest.mark.asyncio
async def test_redis_error_fails_open() -> None:
    redis = _FakeRedis(fail=True)
    limiter = TenantRateLimiter(redis)
    decision = await limiter.check(
        record_tenant_id=TEST_TENANT_UUID, tenant_limit=1,
    )
    # Layer-1 fails-open; per-user layer (P25-L6) handles fail-closed.
    assert decision.allowed is True
    assert decision.used == 0


@pytest.mark.asyncio
async def test_sliding_window_uses_correct_bucket(monkeypatch: Any) -> None:
    """Window boundary must roll the counter without cross-talk."""
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    fake_now = [1_000_000]

    def _now() -> int:
        return fake_now[0]

    monkeypatch.setattr(time, "time", _now)
    # Same minute — counter accumulates
    await limiter.check(record_tenant_id=TEST_TENANT_UUID, tenant_limit=2)
    decision = await limiter.check(record_tenant_id=TEST_TENANT_UUID, tenant_limit=2)
    assert decision.used == 2

    # Jump beyond the window — new bucket key
    fake_now[0] = 1_000_000 + DEFAULT_TENANT_RATE_LIMIT_WINDOW_S + 5
    decision = await limiter.check(record_tenant_id=TEST_TENANT_UUID, tenant_limit=2)
    assert decision.used == 1


@pytest.mark.asyncio
async def test_cross_tenant_isolation() -> None:
    """Spam from tenant A must not affect tenant B."""
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    for _ in range(5):
        await limiter.check(record_tenant_id=TEST_TENANT_UUID, tenant_limit=5)
    blocked = await limiter.check(record_tenant_id=TEST_TENANT_UUID, tenant_limit=5)
    assert blocked.allowed is False
    # Tenant B unaffected
    other = await limiter.check(record_tenant_id=TEST_TENANT_2_UUID, tenant_limit=5)
    assert other.allowed is True
    assert other.used == 1


@pytest.mark.asyncio
async def test_zero_tenant_limit_is_soft_unlimited() -> None:
    """tenant.rate_limit_per_min=0 — counter not enforced, not bypass."""
    redis = _FakeRedis()
    limiter = TenantRateLimiter(redis)
    for _ in range(50):
        decision = await limiter.check(record_tenant_id=TEST_TENANT_UUID, tenant_limit=0)
        assert decision.allowed is True
        assert decision.bypass is False
    # Counter not touched (limit<=0 short-circuit before incr)
    assert redis.calls == []
