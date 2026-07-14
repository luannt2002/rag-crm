"""(c) Rate-based circuit-breaker trip condition (resilience4j-style).

Root cause (load-test 2026-07-13): the CONSECUTIVE-count trip (``fail_max`` in a
row, ``record_success`` resets the counter to 0) NEVER fires against an upstream
that fails ~10-30% of calls scattered among successes — the exact "slow + flaky
gateway" mode measured: 236 provider failures, **0** breaker opens. The breaker
gave zero fast-fail protection.

Rate mode trips on the failure RATE over a rolling window instead: a genuinely
degraded upstream (>= threshold over >= min_calls) is fast-failed and given room
to recover, while scattered blips BELOW the threshold never trip it.

Default stays ``consecutive`` — every existing adapter keeps byte-identical
behaviour; only the LLM provider breaker opts into rate mode.
"""
from __future__ import annotations

from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from ragbot.shared.constants import (
    CB_MODE_CONSECUTIVE,
    CB_MODE_RATE,
    DEFAULT_CB_MODE,
)


def _rate_cb(**over) -> CircuitBreaker:
    policy = CircuitBreakerPolicy(
        mode=CB_MODE_RATE,
        window_size=over.pop("window_size", 20),
        failure_rate_threshold=over.pop("failure_rate_threshold", 0.5),
        min_calls=over.pop("min_calls", 10),
        **over,
    )
    return CircuitBreaker(name="test:rate", policy=policy)


# --- default is unchanged (zero-regression guard) ---------------------------

def test_default_mode_is_consecutive() -> None:
    """Every existing caller keeps the legacy trip condition."""
    assert DEFAULT_CB_MODE == CB_MODE_CONSECUTIVE
    assert CircuitBreakerPolicy().mode == CB_MODE_CONSECUTIVE


def test_consecutive_mode_unchanged_opens_on_run() -> None:
    cb = CircuitBreaker(name="t", policy=CircuitBreakerPolicy(fail_max=5))
    for _ in range(5):
        cb.record_failure()
    assert cb.state == CBState.OPEN


def test_consecutive_mode_unchanged_success_resets() -> None:
    """The legacy flaw, preserved for legacy callers: any success resets."""
    cb = CircuitBreaker(name="t", policy=CircuitBreakerPolicy(fail_max=5))
    for _ in range(4):
        cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state == CBState.CLOSED


# --- rate mode --------------------------------------------------------------

def test_rate_mode_does_not_trip_on_scattered_failures() -> None:
    """The innocom pattern: ~25% failures scattered among successes must NOT
    trip the breaker (they are survivable; fast-failing them would be worse)."""
    cb = _rate_cb()
    for i in range(20):
        if i % 4 == 0:          # 5/20 = 25% < 50% threshold
            cb.record_failure()
        else:
            cb.record_success()
    assert cb.state == CBState.CLOSED


def test_rate_mode_trips_on_high_failure_rate() -> None:
    """A genuinely degraded upstream (>= 50% over the window) trips — even
    though the failures are NEVER consecutive (legacy mode would never open)."""
    cb = _rate_cb()
    for _ in range(10):         # strictly alternating success/failure = 50%
        cb.record_success()
        cb.record_failure()
    assert cb.state == CBState.OPEN


def test_rate_min_calls_gates_only_the_rate_check() -> None:
    """The ``min_calls`` floor gates the RATE verdict (no verdict on too few
    samples). Consecutive trip disabled here to isolate it."""
    cb = _rate_cb(min_calls=10, fail_max=100)
    for _ in range(5):          # 5/5 = 100% failure but < min_calls
        cb.record_failure()
    assert cb.state == CBState.CLOSED


def test_rate_mode_keeps_consecutive_hard_down_trip() -> None:
    """ADDITIVE: rate mode must NOT weaken hard-down detection — a run of
    ``fail_max`` failures still opens fast, before ``min_calls`` samples exist.
    (Losing this was a real protection regression the legacy tests caught.)"""
    cb = _rate_cb(fail_max=5, min_calls=10)
    for _ in range(5):
        cb.record_failure()
    assert cb.state == CBState.OPEN


def test_rate_mode_success_does_not_reset_the_window() -> None:
    """THE fix: unlike consecutive mode, a success must NOT wipe the failure
    history — otherwise a flaky upstream never trips."""
    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_success()         # window [F,S,F,S] — 2/4 = 50%, but no failure
    assert cb.state == CBState.CLOSED   # verdict is only evaluated ON a failure
    cb.record_failure()         # window rolls to [S,F,S,F] — 2/4 = 50% >= threshold
    assert cb.state == CBState.OPEN


def test_rate_mode_half_open_success_closes_and_clears() -> None:
    """After the cooldown probe succeeds the breaker CLOSES with a fresh window
    (a recovered provider must not be re-tripped by its stale failure history)."""
    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5,
                  reset_timeout_s=0)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.can_execute() is True          # cooldown 0 -> HALF_OPEN probe
    cb.record_success()
    assert cb.state == CBState.CLOSED
    # stale history cleared: one more failure must not immediately re-open
    cb.record_failure()
    assert cb.state == CBState.CLOSED


# --- wiring: only the LLM provider breaker opts into rate mode --------------

def test_llm_provider_breaker_uses_rate_mode() -> None:
    """The LLM gateway is the breaker that measured ZERO opens against 236
    failures — it must use the rate trip."""
    import inspect

    from ragbot.infrastructure.llm import dynamic_litellm_router as r

    src = inspect.getsource(r)
    assert "mode=CB_MODE_RATE" in src


def test_embedder_reranker_breakers_keep_default_mode() -> None:
    """Blast-radius guard: adapters that did NOT have the flaw keep the legacy
    consecutive trip (no CircuitBreakerPolicy(mode=...) override outside the
    LLM router)."""
    import pathlib

    root = pathlib.Path("src/ragbot")
    offenders = [
        str(p)
        for p in root.rglob("*.py")
        if "CB_MODE_RATE" in p.read_text(encoding="utf-8")
        and p.name != "dynamic_litellm_router.py"
        and "constants" not in str(p)
        and "retry_policy" not in str(p)
    ]
    assert offenders == [], f"rate mode leaked into non-LLM adapters: {offenders}"


# --- state-machine correctness (0.2 flap / illegal OPEN->CLOSED) ------------

def test_open_breaker_holds_against_late_inflight_success() -> None:
    """A success recorded WHILE OPEN (a call admitted just before the breaker
    tripped, landing after) must NOT close the breaker or cancel the cooldown —
    only a HALF_OPEN recovery probe may close it. Pre-fix ``record_success`` set
    CLOSED unconditionally, so a scattered upstream flapped OPEN<->CLOSED."""
    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5,
                  reset_timeout_s=30)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CBState.OPEN

    cb.record_success()  # a late in-flight completion lands after the trip

    assert cb.state == CBState.OPEN, "a late success must not close an OPEN breaker"
    assert cb.can_execute() is False, "cooldown must still hold"


def test_half_open_admits_only_one_probe() -> None:
    """HALF_OPEN admits at most one recovery probe; a second concurrent caller
    is refused until the probe resolves. Pre-fix HALF_OPEN returned True
    unconditionally, so every concurrent request was admitted at once."""
    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5,
                  reset_timeout_s=0)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CBState.OPEN

    assert cb.can_execute() is True          # first probe → HALF_OPEN
    assert cb.state == CBState.HALF_OPEN
    assert cb.can_execute() is False, "only ONE probe may be in flight"


def test_cooldown_ladder_extends_on_repeated_half_open_failures() -> None:
    """Each HALF_OPEN probe that fails re-opens with a longer cooldown: base 30,
    then +15 per consecutive open — 30 -> 45 -> 60 -> 75 -> 90."""
    from datetime import datetime, timedelta, timezone

    clock = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5,
                  reset_timeout_s=30, cooldown_step_s=15, cooldown_max_s=120)
    cb._now = lambda: clock["t"]  # type: ignore[method-assign]

    for _ in range(4):
        cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.effective_cooldown_s == 30

    for expected in (45, 60, 75, 90):
        clock["t"] += timedelta(seconds=cb.effective_cooldown_s + 1)
        assert cb.can_execute() is True          # cooldown elapsed → probe
        assert cb.state == CBState.HALF_OPEN
        cb.record_failure()                      # probe fails → re-open, extend
        assert cb.state == CBState.OPEN
        assert cb.effective_cooldown_s == expected


def test_reset_force_closes_even_from_open() -> None:
    """``reset()`` must force CLOSED from OPEN — it can no longer route through
    ``record_success`` (which is now a no-op while OPEN)."""
    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5,
                  reset_timeout_s=30)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CBState.OPEN

    cb.reset()

    assert cb.state == CBState.CLOSED
    assert cb.can_execute() is True


def test_state_transition_emits_observability_event(monkeypatch) -> None:
    """Every transition emits ``cb_state_transition`` with the from/to states,
    provider and observed window fail-rate — the only signal a load-test has to
    assert ``count(OPEN->CLOSED)==0`` on the flap fix."""
    captured: list[tuple[str, dict]] = []

    class _Recorder:
        def __getattr__(self, _level: str):
            def _record(event: str, **kw) -> None:
                captured.append((event, kw))
            return _record

    import ragbot.application.services.retry_policy as rp
    monkeypatch.setattr(rp, "logger", _Recorder())

    cb = _rate_cb(min_calls=4, window_size=4, failure_rate_threshold=0.5,
                  reset_timeout_s=0)
    for _ in range(4):
        cb.record_failure()  # → OPEN

    events = [(e, kw) for e, kw in captured if e == "cb_state_transition"]
    assert events, "no cb_state_transition emitted on CLOSED->OPEN"
    _, kw = events[0]
    assert kw["from_state"] == "closed" and kw["to_state"] == "open"
    assert kw["provider"] == "test:rate"
    assert kw["window_fail_rate"] == 1.0
