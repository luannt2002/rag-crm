"""Clock abstraction for testability.

Ref: docs/application/PLAN_02_CONVENTIONS_BASE_CONTRACTS.md §clock.py.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SystemClock:
    """Real wall-clock + monotonic — production default."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class FrozenClock:
    """Test-only clock with manual advance."""

    def __init__(self, *, initial: datetime | None = None) -> None:
        self._now = initial or datetime(2026, 1, 1, tzinfo=UTC)
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, *, seconds: float = 0, minutes: float = 0) -> None:
        delta = timedelta(seconds=seconds, minutes=minutes)
        self._now += delta
        self._mono += delta.total_seconds()


_default_clock: Clock = SystemClock()


def get_clock() -> Clock:
    """Return the active clock singleton."""
    return _default_clock


def set_clock(clock: Clock) -> None:  # pragma: no cover (test-only)
    """Override the default clock (e.g. in tests)."""
    global _default_clock  # noqa: PLW0603
    _default_clock = clock


__all__ = ["Clock", "FrozenClock", "SystemClock", "get_clock", "set_clock"]
