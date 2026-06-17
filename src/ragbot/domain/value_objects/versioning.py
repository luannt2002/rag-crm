"""Freshness + authority + validity value objects.

Ref: PLAN_03 §versioning.py / RAGBOT_MASTER §5.4 / §14.7.

Note: `bump_corpus_version` / `bump_bot_version` removed (migration 0011 đã
drop cột tương ứng — dead code).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ragbot.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class AuthorityScore:
    """Document authority score in [0.0, 1.0] — higher = more trustworthy."""

    value: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.value <= 1.0):
            raise InvariantViolation(
                f"AuthorityScore must be in [0.0, 1.0], got {self.value}",
            )


@dataclass(frozen=True, slots=True)
class ValidityWindow:
    """Document validity window. None = no upper bound."""

    valid_from: datetime
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise InvariantViolation("valid_until must be > valid_from")

    def is_valid_at(self, when: datetime) -> bool:
        if when < self.valid_from:
            return False
        if self.valid_until is not None and when >= self.valid_until:
            return False
        return True


def compute_freshness(*, age_days: int, half_life_days: int = 90) -> float:
    """Exponential decay: score *= exp(-age / half_life)."""
    if half_life_days <= 0:
        raise InvariantViolation("half_life_days must be > 0")
    if age_days < 0:
        return 1.0
    return math.exp(-age_days / half_life_days)


__all__ = [
    "AuthorityScore",
    "ValidityWindow",
    "compute_freshness",
]
