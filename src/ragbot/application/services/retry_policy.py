"""RetryPolicy + CircuitBreaker (pure logic).

Production-grade implementations live in `ragbot.infrastructure.*`
using `tenacity` and `pybreaker`. This module is for use cases needing
in-process retry without infra coupling.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TypeVar

import structlog

from ragbot.shared.constants import (
    CB_MODE_RATE,
    DEFAULT_CB_COOLDOWN_MAX_S,
    DEFAULT_CB_COOLDOWN_STEP_S,
    DEFAULT_CB_FAILURE_RATE_THRESHOLD,
    DEFAULT_CB_HALF_OPEN_MAX_CALLS,
    DEFAULT_CB_MIN_CALLS,
    DEFAULT_CB_MODE,
    DEFAULT_CB_POLICY_FAIL_MAX,
    DEFAULT_CB_POLICY_RESET_TIMEOUT_S,
    DEFAULT_CB_WINDOW_SIZE,
)
from ragbot.shared.errors import CircuitBreakerOpen

logger = structlog.get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_ms: int = 100
    max_backoff_ms: int = 10_000
    exponential_base: float = 2.0
    jitter: bool = True


async def retry_with_backoff(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = RetryPolicy(),
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await coro_factory()
        except retryable_exceptions as exc:  # noqa: PERF203
            last_exc = exc
            if attempt + 1 >= policy.max_attempts:
                break
            backoff = min(
                policy.initial_backoff_ms * (policy.exponential_base**attempt),
                policy.max_backoff_ms,
            )
            if policy.jitter:
                backoff = backoff * (0.5 + random.random())  # noqa: S311
            await asyncio.sleep(backoff / 1000.0)
    if last_exc is not None:
        raise last_exc
    msg = "retry_with_backoff: unreachable"
    raise RuntimeError(msg)


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    """Static policy for the breaker.

    ``reset_timeout_s`` is the *base* cooldown (after the first OPEN). The
    effective cooldown grows by ``cooldown_step_s`` for every consecutive
    OPEN cycle (HALF_OPEN → fail → OPEN), capped at ``cooldown_max_s``.
    """

    fail_max: int = DEFAULT_CB_POLICY_FAIL_MAX
    reset_timeout_s: int = DEFAULT_CB_POLICY_RESET_TIMEOUT_S
    cooldown_step_s: int = DEFAULT_CB_COOLDOWN_STEP_S
    cooldown_max_s: int = DEFAULT_CB_COOLDOWN_MAX_S
    # Trip condition. ``consecutive`` (default) = legacy ``fail_max`` in a row,
    # reset by ANY success — blind to an upstream that fails a scattered 10-30%.
    # ``rate`` = open when >= ``failure_rate_threshold`` of the last
    # ``window_size`` outcomes failed, once >= ``min_calls`` samples exist.
    mode: str = DEFAULT_CB_MODE
    window_size: int = DEFAULT_CB_WINDOW_SIZE
    failure_rate_threshold: float = DEFAULT_CB_FAILURE_RATE_THRESHOLD
    min_calls: int = DEFAULT_CB_MIN_CALLS
    # HALF_OPEN admits at most this many recovery probes at once (resilience4j
    # ``permittedNumberOfCallsInHalfOpenState``). Extra concurrent callers are
    # refused until a probe resolves, so a burst does not stampede a provider
    # that just came back.
    half_open_max_calls: int = DEFAULT_CB_HALF_OPEN_MAX_CALLS


@dataclass(slots=True)
class CircuitBreakerState:
    state: CBState = CBState.CLOSED
    fail_count: int = 0
    last_failure_at: datetime | None = None
    # Adaptive cooldown — count of consecutive OPEN cycles since the last
    # CLOSED state. Drives ``effective_cooldown_s`` linearly until the
    # ``cooldown_max_s`` ceiling. Reset to 0 on ``record_success``.
    consec_open_fails: int = 0
    # Recovery probes admitted in the CURRENT HALF_OPEN cycle. Reset to 0 on
    # every entry to HALF_OPEN; gates ``can_execute`` so only the configured
    # probe budget is let through.
    half_open_calls: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class CircuitBreaker:
    def __init__(self, *, name: str, policy: CircuitBreakerPolicy = CircuitBreakerPolicy()) -> None:
        self.name = name
        self._policy = policy
        self._state = CircuitBreakerState()
        # Rolling outcome window (True = failure). Only consulted in ``rate``
        # mode; kept unconditionally so the two modes share one record path.
        self._window: deque[bool] = deque(maxlen=max(1, policy.window_size))

    def _rate_should_open(self) -> bool:
        """``rate`` mode: >= threshold of the last ``window_size`` outcomes failed.

        Below ``min_calls`` samples the verdict is withheld — a tiny burst of
        failures at startup must not fast-fail a healthy provider.
        """
        n = len(self._window)
        if n < self._policy.min_calls:
            return False
        return (sum(self._window) / n) >= self._policy.failure_rate_threshold

    @property
    def state(self) -> CBState:
        return self._state.state

    def _now(self) -> datetime:
        # Uses real clock — for test, inject Clock at higher level.
        return datetime.now(tz=__import__("datetime").timezone.utc)

    @property
    def effective_cooldown_s(self) -> int:
        """Adaptive cooldown for the *current* OPEN cycle.

        ``base + step * consec_open_fails`` clamped to ``cooldown_max_s``.
        First OPEN ⇒ base; second consecutive OPEN (HALF_OPEN failed) ⇒
        base + step; capped at ``cooldown_max_s``.
        """
        base = self._policy.reset_timeout_s
        step = self._policy.cooldown_step_s
        # ``consec_open_fails`` is incremented at the moment of OPEN, so for
        # the very first open it equals 1 → no growth on the first cycle.
        growth = max(0, self._state.consec_open_fails - 1) * step
        return min(base + growth, self._policy.cooldown_max_s)

    def _enter(self, new_state: CBState) -> None:
        """Transition to *new_state*, rotating the rolling window and emitting a
        structured ``cb_state_transition`` event.

        Every state change funnels through here for two reasons: (1) the window
        is cleared on transition so a prior cycle's outcomes never carry into the
        next — a poisoned window would re-trip a just-recovered provider; (2) the
        OPEN/CLOSE is observable (the only place a flapping breaker is visible).
        """
        prev = self._state.state
        if new_state is prev:
            return
        n = len(self._window)
        window_fail_rate = (sum(self._window) / n) if n else 0.0
        self._state.state = new_state
        self._window.clear()
        if new_state is not CBState.HALF_OPEN:
            self._state.half_open_calls = 0
        self._safe_log(
            "warning" if new_state is CBState.OPEN else "info",
            "cb_state_transition",
            provider=self.name,
            from_state=prev.value,
            to_state=new_state.value,
            window_fail_rate=round(window_fail_rate, 4),
            consec_open_fails=self._state.consec_open_fails,
        )

    def can_execute(self) -> bool:
        st = self._state.state
        if st == CBState.CLOSED:
            return True
        if st == CBState.OPEN:
            assert self._state.last_failure_at is not None
            elapsed = (self._now() - self._state.last_failure_at).total_seconds()
            if elapsed >= self.effective_cooldown_s:
                # Cooldown elapsed → admit the FIRST recovery probe and move to
                # HALF_OPEN. ``half_open_calls`` is set BEFORE ``_enter`` (which
                # zeroes it on entry) so this admit is counted.
                self._enter(CBState.HALF_OPEN)
                self._state.half_open_calls = 1
                return True
            return False
        # HALF_OPEN — admit only up to the probe budget; refuse the rest until
        # the in-flight probe resolves (success → CLOSED, failure → OPEN).
        if self._state.half_open_calls < self._policy.half_open_max_calls:
            self._state.half_open_calls += 1
            return True
        return False

    @staticmethod
    def _safe_log(level: str, event: str, **fields: object) -> None:
        """Best-effort structured log.

        structlog can raise ``ValueError: I/O operation on closed file`` when
        a captured stream (pytest capsys, container shutdown, broken pipe)
        is torn down after the cached handle was bound. CB state machine
        purity must not depend on the log sink — swallow narrow stream
        failures so callers see only state semantics.
        """
        try:
            getattr(logger, level)(event, **fields)
        except (ValueError, OSError, AttributeError):
            pass

    def _log_cooldown_extended(self) -> None:
        if self._state.consec_open_fails > 1:
            self._safe_log(
                "warning",
                "cb_cooldown_extended",
                provider=self.name,
                cooldown_s=self.effective_cooldown_s,
                consec_fails=self._state.consec_open_fails,
            )

    def record_success(self) -> None:
        prev_state = self._state.state
        if prev_state is CBState.OPEN:
            # A late in-flight success (a call admitted just before the trip,
            # landing after) must NOT cancel the cooldown — only a HALF_OPEN
            # recovery probe may close the breaker. Pre-fix this closed the
            # breaker unconditionally → OPEN<->CLOSED flap under scattered load.
            return
        self._state.fail_count = 0
        if prev_state is CBState.HALF_OPEN:
            # Recovery probe succeeded → close and reset the cooldown ladder.
            # ``_enter`` clears the window so the recovered provider is not
            # re-tripped by its own stale failure history.
            was_open_cycle = self._state.consec_open_fails > 0
            self._state.consec_open_fails = 0
            self._state.last_failure_at = None
            self._enter(CBState.CLOSED)
            if was_open_cycle:
                self._safe_log(
                    "info",
                    "cb_cooldown_reset",
                    provider=self.name,
                    cooldown_s=self._policy.reset_timeout_s,
                )
            return
        # CLOSED — a normal success. Record the outcome (dilutes the rate window,
        # the whole point of rate mode) and clear any transient failure state.
        self._window.append(False)
        self._state.last_failure_at = None
        self._state.consec_open_fails = 0

    def record_failure(self) -> None:
        prev_state = self._state.state
        if prev_state is CBState.OPEN:
            # Late in-flight failure while already OPEN — expected and ignored.
            # Do NOT refresh the cooldown clock: a stream of late failures must
            # not push the recovery probe out indefinitely.
            return
        if prev_state is CBState.HALF_OPEN:
            # A recovery probe failed → straight back to OPEN and extend the
            # cooldown ladder. One failed probe is enough — no rate re-eval.
            self._state.fail_count += 1
            self._state.last_failure_at = self._now()
            self._state.consec_open_fails += 1
            self._enter(CBState.OPEN)
            self._log_cooldown_extended()
            return
        # CLOSED — evaluate the trip condition on the fresh outcome. The
        # consecutive check is kept in BOTH modes so a hard-down upstream opens
        # fast (before ``min_calls`` samples exist); rate mode ADDS the
        # rolling-window check to catch the scattered-failure upstream.
        self._state.fail_count += 1
        self._window.append(True)
        self._state.last_failure_at = self._now()
        _should_open = self._state.fail_count >= self._policy.fail_max
        if not _should_open and self._policy.mode == CB_MODE_RATE:
            _should_open = self._rate_should_open()
        if _should_open:
            self._state.consec_open_fails += 1
            self._enter(CBState.OPEN)
            self._log_cooldown_extended()

    def reset(self) -> None:
        """Force the breaker CLOSED and wipe all transient state (window, fail
        count, cooldown ladder, probe budget).

        Explicit because ``record_success`` is now a no-op while OPEN — an admin
        / failover reset must not route through it or a stuck-OPEN breaker would
        never clear.
        """
        self._state.fail_count = 0
        self._state.last_failure_at = None
        self._state.consec_open_fails = 0
        self._state.half_open_calls = 0
        if self._state.state is not CBState.CLOSED:
            self._enter(CBState.CLOSED)
        else:
            self._window.clear()

    def __enter__(self) -> CircuitBreaker:
        if not self.can_execute():
            raise CircuitBreakerOpen(f"circuit breaker '{self.name}' is OPEN")
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_a: object) -> None:
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()


__all__ = [
    "CBState",
    "CircuitBreaker",
    "CircuitBreakerPolicy",
    "CircuitBreakerState",
    "RetryPolicy",
    "retry_with_backoff",
]
