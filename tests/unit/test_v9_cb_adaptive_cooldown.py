"""Adaptive circuit-breaker cooldown tests.

Each consecutive OPEN cycle (HALF_OPEN → fail → OPEN) extends the
cooldown by ``DEFAULT_CB_COOLDOWN_STEP_S`` until ``DEFAULT_CB_COOLDOWN_MAX_S``.
A successful HALF_OPEN call rebases cooldown to the policy default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from ragbot.shared.constants import (
    DEFAULT_CB_COOLDOWN_MAX_S,
    DEFAULT_CB_COOLDOWN_STEP_S,
)


def _cb(*, fail_max: int = 1, base_s: int = 30) -> CircuitBreaker:
    return CircuitBreaker(
        name="test_provider",
        policy=CircuitBreakerPolicy(
            fail_max=fail_max,
            reset_timeout_s=base_s,
            cooldown_step_s=DEFAULT_CB_COOLDOWN_STEP_S,
            cooldown_max_s=DEFAULT_CB_COOLDOWN_MAX_S,
        ),
    )


def _trip_to_open_then_halfopen(
    cb: CircuitBreaker, monkeypatch: pytest.MonkeyPatch, base: datetime, *, base_s: int,
) -> datetime:
    """Drive CB through OPEN, advance clock past cooldown to HALF_OPEN."""
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    advanced = base + timedelta(seconds=base_s + 1)
    monkeypatch.setattr(cb, "_now", lambda: advanced)
    cb.can_execute()
    assert cb.state == CBState.HALF_OPEN
    return advanced


def test_first_open_uses_base_cooldown() -> None:
    cb = _cb(base_s=30)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    # First OPEN cycle has no growth: effective == base.
    assert cb.effective_cooldown_s == 30


def test_second_consecutive_open_extends_by_one_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cb = _cb(base_s=30)
    base = datetime.now(tz=timezone.utc)
    advanced = _trip_to_open_then_halfopen(cb, monkeypatch, base, base_s=30)
    # Half-open — second failure re-opens with consec_open_fails=2.
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.effective_cooldown_s == 30 + DEFAULT_CB_COOLDOWN_STEP_S
    # Independence check: clock advance, not used here.
    _ = advanced


def test_third_consecutive_open_extends_by_two_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cb = _cb(base_s=30)
    base = datetime.now(tz=timezone.utc)

    # Cycle 1: open → HALF_OPEN
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    monkeypatch.setattr(cb, "_now", lambda: base + timedelta(seconds=31))
    cb.can_execute()
    assert cb.state == CBState.HALF_OPEN

    # Cycle 2: half-open → fail → OPEN (consec=2)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    new_base_2 = base + timedelta(seconds=31)
    cooldown_2 = cb.effective_cooldown_s
    assert cooldown_2 == 30 + DEFAULT_CB_COOLDOWN_STEP_S

    # Cycle 3: cooldown elapsed → HALF_OPEN → fail → OPEN (consec=3)
    monkeypatch.setattr(cb, "_now", lambda: new_base_2 + timedelta(seconds=cooldown_2 + 1))
    cb.can_execute()
    assert cb.state == CBState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.effective_cooldown_s == 30 + 2 * DEFAULT_CB_COOLDOWN_STEP_S


def test_cooldown_capped_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adaptive growth stops at ``cooldown_max_s`` even after many OPEN cycles."""
    cb = _cb(base_s=30)
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    # Force consec_open_fails to a large value to validate the ceiling.
    cb._state.consec_open_fails = 100
    assert cb.effective_cooldown_s == DEFAULT_CB_COOLDOWN_MAX_S


def test_record_success_resets_cooldown_to_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cb = _cb(base_s=30)
    base = datetime.now(tz=timezone.utc)
    advanced = _trip_to_open_then_halfopen(cb, monkeypatch, base, base_s=30)

    # Success in HALF_OPEN → CLOSED, consec reset.
    cb.record_success()
    assert cb.state == CBState.CLOSED
    assert cb._state.consec_open_fails == 0

    # Force a fresh OPEN — cooldown is back to base, not the extended value.
    monkeypatch.setattr(cb, "_now", lambda: advanced + timedelta(seconds=1))
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.effective_cooldown_s == 30


def test_repeated_failures_while_already_open_do_not_inflate_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cb = _cb(base_s=30, fail_max=1)
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb, "_now", lambda: base)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    initial_cooldown = cb.effective_cooldown_s
    # Caller bumps record_failure several more times while breaker still
    # OPEN (e.g. concurrent requests racing) — cooldown must not grow.
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.effective_cooldown_s == initial_cooldown


def test_existing_cb_api_remains_intact() -> None:
    """Default-arg CircuitBreaker (no policy override) still constructs."""
    cb = CircuitBreaker(name="default")
    assert cb.state == CBState.CLOSED
    assert cb.can_execute() is True
    # Default policy must expose the new adaptive knobs.
    assert cb._policy.cooldown_step_s == DEFAULT_CB_COOLDOWN_STEP_S
    assert cb._policy.cooldown_max_s == DEFAULT_CB_COOLDOWN_MAX_S
