"""Unit tests proving ``MetricsPort`` injection works without any
``ragbot.infrastructure.*`` import in the test path.

Two services consume the port:
  * ``StepTracker.step(...)`` calls ``observe_step_duration``.
  * ``TenantRateLimiter.check(...)`` (on bypass) calls
    ``inc_rate_limit_bypass``.

These tests inject a Fake implementing the Protocol so the assertion
verifies real business behaviour without touching prometheus_client.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from ragbot.application.ports.metrics_port import MetricsPort
from ragbot.application.services.step_tracker import StepTracker
from ragbot.application.services.tenant_rate_limiter import TenantRateLimiter

_TENANT = UUID("00000000-0000-0000-0000-000000000077")


class _FakeMetrics(MetricsPort):
    """Pure-Python Port impl — captures calls for assertions."""

    def __init__(self) -> None:
        self.step_calls: list[tuple[str, float]] = []
        self.bypass_calls: list[tuple[str, str]] = []

    def observe_step_duration(self, step_name: str, seconds: float) -> None:
        self.step_calls.append((step_name, seconds))

    def inc_rate_limit_bypass(self, *, tenant_id: str, source: str) -> None:
        self.bypass_calls.append((tenant_id, source))


class _RecordingRepo:
    """Minimal request-log repo — captures ``add_step`` invocations."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def add_step(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


class _FakeRedis:
    """Minimal INCR/EXPIRE double for the rate limiter."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


@pytest.mark.asyncio
async def test_step_tracker_forwards_observe_to_metrics_port() -> None:
    """``StepTracker.step()`` calls ``observe_step_duration`` exactly once."""
    metrics = _FakeMetrics()
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=UUID("11111111-1111-1111-1111-111111111111"),
        record_tenant_id=_TENANT,
        repo=repo,
        metrics=metrics,
    )
    async with tracker.step("retrieve") as ctx:
        ctx.add_tokens(prompt=10, completion=20)

    assert len(metrics.step_calls) == 1
    name, secs = metrics.step_calls[0]
    assert name == "retrieve"
    assert secs >= 0.0
    # And the persisted row still landed.
    assert repo.rows[0]["step_name"] == "retrieve"
    assert repo.rows[0]["input_tokens"] == 10
    assert repo.rows[0]["output_tokens"] == 20


@pytest.mark.asyncio
async def test_step_tracker_without_metrics_does_not_crash() -> None:
    """Default ``metrics=None`` — observe is a no-op but the row persists."""
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=UUID("22222222-2222-2222-2222-222222222222"),
        record_tenant_id=_TENANT,
        repo=repo,
    )
    async with tracker.step("rerank"):
        pass
    assert repo.rows[0]["step_name"] == "rerank"
    assert repo.rows[0]["status"] == "success"


@pytest.mark.asyncio
async def test_tenant_rate_limiter_forwards_bypass_to_metrics_port() -> None:
    """Bypass paths increment via ``MetricsPort.inc_rate_limit_bypass``."""
    metrics = _FakeMetrics()
    limiter = TenantRateLimiter(_FakeRedis(), metrics=metrics)
    decision = await limiter.check(
        record_tenant_id=_TENANT, tenant_bypass=True, tenant_limit=1,
    )
    assert decision.bypass is True
    assert decision.allowed is True
    assert metrics.bypass_calls == [(str(_TENANT), "tenant_bypass")]


@pytest.mark.asyncio
async def test_tenant_rate_limiter_without_metrics_skips_emission() -> None:
    """``metrics=None`` is a no-op; the decision still returns correctly."""
    limiter = TenantRateLimiter(_FakeRedis())
    decision = await limiter.check(
        record_tenant_id=_TENANT, bot_bypass=True, tenant_limit=1,
    )
    assert decision.bypass is True
    assert decision.source == "bot_bypass"


def test_fake_metrics_satisfies_protocol_runtime() -> None:
    """Quick sanity check on the Protocol — useful as documentation."""
    assert isinstance(_FakeMetrics(), MetricsPort)
