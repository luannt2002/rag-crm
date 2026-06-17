"""C.5 — Per-tenant monthly token meter unit tests.

Validates HINCRBY accumulation, calendar-month bucket isolation, and the
3-state cap policy (NULL = no cap, 0 = block, positive = warn@80%/cut@100%).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ragbot.application.services.tenant_token_meter import (
    TenantTokenMeter,
    TokenCapDecision,
)


class _FakeRedis:
    """Minimal async hash store — HINCRBY / HGETALL / EXPIRE."""

    def __init__(self, *, fail: bool = False) -> None:
        self._h: dict[str, dict[str, int]] = {}
        self._ttl: dict[str, int] = {}
        self._fail = fail

    async def hincrby(self, key: str, field: str, n: int) -> int:
        if self._fail:
            raise RuntimeError("redis down")
        h = self._h.setdefault(key, {})
        h[field] = h.get(field, 0) + int(n)
        return h[field]

    async def hgetall(self, key: str) -> dict:
        if self._fail:
            raise RuntimeError("redis down")
        return dict(self._h.get(key, {}))

    async def expire(self, key: str, ttl: int) -> bool:
        self._ttl[key] = ttl
        return True


@pytest.mark.asyncio
async def test_increment_and_read_back() -> None:
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    after = await meter.increment_tokens(1, prompt_tokens=100, completion_tokens=50)
    assert after == {"prompt": 100, "completion": 50, "total": 150}
    after2 = await meter.increment_tokens(1, prompt_tokens=10, completion_tokens=5)
    assert after2 == {"prompt": 110, "completion": 55, "total": 165}
    usage = await meter.get_monthly_usage(1)
    assert usage == {"prompt": 110, "completion": 55, "total": 165}


@pytest.mark.asyncio
async def test_monthly_bucket_isolation() -> None:
    """April vs May totals live in distinct hash keys."""
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    apr = datetime(2026, 4, 15, tzinfo=timezone.utc)
    may = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await meter.increment_tokens(1, 100, 0, now=apr)
    await meter.increment_tokens(1, 200, 0, now=may)
    apr_usage = await meter.get_monthly_usage(1, now=apr)
    may_usage = await meter.get_monthly_usage(1, now=may)
    assert apr_usage["total"] == 100
    assert may_usage["total"] == 200


@pytest.mark.asyncio
async def test_negative_inputs_clamped_to_zero() -> None:
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    after = await meter.increment_tokens(1, -50, -10)
    assert after["total"] == 0


@pytest.mark.asyncio
async def test_check_cap_null_means_no_cap() -> None:
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    decision = await meter.check_token_cap(1, cap=None)
    assert decision.allowed is True
    assert decision.reason == "no_cap"
    assert decision.warn is False


@pytest.mark.asyncio
async def test_check_cap_zero_blocks_immediately() -> None:
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    decision = await meter.check_token_cap(1, cap=0)
    assert decision.allowed is False
    assert decision.reason == "blocked_zero"
    assert decision.cap == 0


@pytest.mark.asyncio
async def test_check_cap_under_threshold_no_warn() -> None:
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    await meter.increment_tokens(1, 500, 0)  # 50% of 1000
    decision = await meter.check_token_cap(1, cap=1000)
    assert decision.allowed is True
    assert decision.warn is False
    assert decision.used == 500


@pytest.mark.asyncio
async def test_check_cap_at_warn_threshold() -> None:
    """≥80% triggers warn but still allows."""
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    await meter.increment_tokens(1, 800, 0)
    decision = await meter.check_token_cap(1, cap=1000)
    assert decision.allowed is True
    assert decision.warn is True
    assert decision.reason == "ok"


@pytest.mark.asyncio
async def test_check_cap_at_block_threshold() -> None:
    """100% triggers hard-cut."""
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    await meter.increment_tokens(1, 1000, 0)
    decision = await meter.check_token_cap(1, cap=1000)
    assert decision.allowed is False
    assert decision.reason == "exceeded"
    assert decision.warn is True


@pytest.mark.asyncio
async def test_check_cap_above_threshold() -> None:
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    await meter.increment_tokens(1, 1500, 0)
    decision = await meter.check_token_cap(1, cap=1000)
    assert decision.allowed is False
    assert decision.reason == "exceeded"


@pytest.mark.asyncio
async def test_redis_error_during_increment_returns_zero() -> None:
    """Failure must not corrupt caller state."""
    redis = _FakeRedis(fail=True)
    meter = TenantTokenMeter(redis)
    after = await meter.increment_tokens(1, 100, 50)
    assert after == {"prompt": 0, "completion": 0, "total": 0}


@pytest.mark.asyncio
async def test_decision_dataclass_frozen() -> None:
    """TokenCapDecision is immutable so callers can't mutate audit fields."""
    decision = TokenCapDecision(allowed=True, reason="ok", used=0, cap=1000, warn=False)
    with pytest.raises((AttributeError, Exception)):
        decision.allowed = False  # type: ignore[misc]


@pytest.mark.asyncio
async def test_bytes_field_decode_in_hash() -> None:
    """Real Redis returns bytes; meter coerces both bytes and str keys."""
    redis = _FakeRedis()
    meter = TenantTokenMeter(redis)
    # Manually inject a bytes-keyed hash to exercise the decode path
    redis._h["tokens:tenant:1:2026-04"] = {b"prompt": b"500", b"completion": b"250"}
    usage = await meter.get_monthly_usage(1, now=datetime(2026, 4, 15, tzinfo=timezone.utc))
    assert usage["total"] == 750
