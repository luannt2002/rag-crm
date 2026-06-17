"""P25 Phase B — CircuitBreaker (per-provider) state-machine tests.

Exercise the existing ``ragbot.application.services.retry_policy.CircuitBreaker``
to lock in the P25 contract: 5 fails → OPEN, cooldown elapses → HALF_OPEN,
success in HALF_OPEN → CLOSED, failure in HALF_OPEN → OPEN.

Plus router-level integration: each provider gets its OWN breaker instance so
one upstream's flap doesn't poison another provider's state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from ragbot.shared.constants import (
    DEFAULT_CB_COOLDOWN_S,
    DEFAULT_CB_FAILURE_THRESHOLD,
)
from ragbot.shared.errors import CircuitBreakerOpen


def _fresh_cb(*, fail_max: int = 5, reset_s: int = 30) -> CircuitBreaker:
    return CircuitBreaker(
        name="test",
        policy=CircuitBreakerPolicy(fail_max=fail_max, reset_timeout_s=reset_s),
    )


def test_initial_state_is_closed_and_executable() -> None:
    cb = _fresh_cb()
    assert cb.state == CBState.CLOSED
    assert cb.can_execute() is True


def test_five_failures_open_the_breaker() -> None:
    cb = _fresh_cb(fail_max=DEFAULT_CB_FAILURE_THRESHOLD)
    for _ in range(DEFAULT_CB_FAILURE_THRESHOLD):
        cb.record_failure()
    assert cb.state == CBState.OPEN


def test_open_breaker_rejects_during_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    cb = _fresh_cb(fail_max=2, reset_s=DEFAULT_CB_COOLDOWN_S)
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CBState.OPEN
    # Still inside cooldown — must reject.
    monkeypatch.setattr(cb, "_now", lambda: base + timedelta(seconds=1))
    assert cb.can_execute() is False
    # Context-manager raises CircuitBreakerOpen for callers that prefer it.
    with pytest.raises(CircuitBreakerOpen):
        with cb:
            pass


def test_cooldown_elapsed_transitions_to_half_open(monkeypatch: pytest.MonkeyPatch) -> None:
    cb = _fresh_cb(fail_max=1, reset_s=10)
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    # Move clock past cooldown.
    monkeypatch.setattr(cb, "_now", lambda: base + timedelta(seconds=11))
    assert cb.can_execute() is True
    assert cb.state == CBState.HALF_OPEN


def test_half_open_success_transitions_back_to_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cb = _fresh_cb(fail_max=1, reset_s=5)
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    monkeypatch.setattr(cb, "_now", lambda: base + timedelta(seconds=6))
    cb.can_execute()  # → HALF_OPEN
    assert cb.state == CBState.HALF_OPEN
    cb.record_success()
    assert cb.state == CBState.CLOSED


def test_half_open_failure_reopens_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    cb = _fresh_cb(fail_max=1, reset_s=5)
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()  # → OPEN (fail_count=1)
    monkeypatch.setattr(cb, "_now", lambda: base + timedelta(seconds=6))
    cb.can_execute()  # → HALF_OPEN
    assert cb.state == CBState.HALF_OPEN
    # 1 failure in HALF_OPEN with fail_max=1 → fail_count rolls to 2
    # but state machine flips back to OPEN because fail_count >= fail_max.
    cb.record_failure()
    assert cb.state == CBState.OPEN


def test_record_success_resets_fail_counter() -> None:
    cb = _fresh_cb(fail_max=5)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CBState.CLOSED  # not yet at threshold
    cb.record_success()
    # Counter must reset — 4 more failures should not OPEN it.
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CBState.CLOSED


# ---------------------------------------------------------------------------
# Router-level: per-provider isolation.
# ---------------------------------------------------------------------------

class _FakeRepo:
    async def list_providers(self, *, enabled_only: bool = True) -> list[Any]:  # noqa: ARG002
        return []

    async def list_models(self, *, enabled_only: bool = True) -> list[Any]:  # noqa: ARG002
        return []


def test_router_creates_distinct_breaker_per_provider() -> None:
    from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
    router = DynamicLiteLLMRouter(_FakeRepo())
    cb_openai = router._get_circuit_breaker("openai")
    cb_anthropic = router._get_circuit_breaker("anthropic")
    cb_cohere = router._get_circuit_breaker("cohere")
    assert cb_openai is not cb_anthropic
    assert cb_anthropic is not cb_cohere
    # Reusing same key returns the same instance (cache).
    assert router._get_circuit_breaker("openai") is cb_openai
    # Tripping one provider does not affect siblings.
    for _ in range(DEFAULT_CB_FAILURE_THRESHOLD):
        cb_openai.record_failure()
    assert cb_openai.state == CBState.OPEN
    assert cb_anthropic.state == CBState.CLOSED
    assert cb_cohere.state == CBState.CLOSED
