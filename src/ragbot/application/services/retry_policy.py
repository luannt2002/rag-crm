"""RetryPolicy + CircuitBreaker (pure logic).

Production-grade implementations live in `ragbot.infrastructure.*`
using `tenacity` and `pybreaker`. This module is for use cases needing
in-process retry without infra coupling.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TypeVar

import structlog

from ragbot.shared.constants import (
    DEFAULT_CB_COOLDOWN_MAX_S,
    DEFAULT_CB_COOLDOWN_STEP_S,
    DEFAULT_CB_POLICY_FAIL_MAX,
    DEFAULT_CB_POLICY_RESET_TIMEOUT_S,
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


@dataclass(slots=True)
class CircuitBreakerState:
    state: CBState = CBState.CLOSED
    fail_count: int = 0
    last_failure_at: datetime | None = None
    # Adaptive cooldown — count of consecutive OPEN cycles since the last
    # CLOSED state. Drives ``effective_cooldown_s`` linearly until the
    # ``cooldown_max_s`` ceiling. Reset to 0 on ``record_success``.
    consec_open_fails: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class CircuitBreaker:
    def __init__(self, *, name: str, policy: CircuitBreakerPolicy = CircuitBreakerPolicy()) -> None:
        self.name = name
        self._policy = policy
        self._state = CircuitBreakerState()

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

    def can_execute(self) -> bool:
        if self._state.state == CBState.CLOSED:
            return True
        if self._state.state == CBState.OPEN:
            assert self._state.last_failure_at is not None
            elapsed = (self._now() - self._state.last_failure_at).total_seconds()
            if elapsed >= self.effective_cooldown_s:
                self._state.state = CBState.HALF_OPEN
                return True
            return False
        # half_open
        return True

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

    def record_success(self) -> None:
        was_open_cycle = self._state.consec_open_fails > 0
        self._state.state = CBState.CLOSED
        self._state.fail_count = 0
        self._state.last_failure_at = None
        self._state.consec_open_fails = 0
        if was_open_cycle:
            self._safe_log(
                "info",
                "cb_cooldown_reset",
                provider=self.name,
                cooldown_s=self._policy.reset_timeout_s,
            )

    def record_failure(self) -> None:
        prev_state = self._state.state
        self._state.fail_count += 1
        self._state.last_failure_at = self._now()
        if self._state.fail_count >= self._policy.fail_max:
            self._state.state = CBState.OPEN
            # Only count this as an *additional* consecutive OPEN cycle
            # when transitioning into OPEN — repeated record_failure while
            # already OPEN must not inflate cooldown.
            if prev_state != CBState.OPEN:
                self._state.consec_open_fails += 1
                if self._state.consec_open_fails > 1:
                    self._safe_log(
                        "warning",
                        "cb_cooldown_extended",
                        provider=self.name,
                        cooldown_s=self.effective_cooldown_s,
                        consec_fails=self._state.consec_open_fails,
                    )

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
