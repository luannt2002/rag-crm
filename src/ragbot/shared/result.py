"""Result pattern + API envelope.

Ref: docs/application/PLAN_02_CONVENTIONS_BASE_CONTRACTS.md §result.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, _default: T) -> T:
        return self.value

    def map(self, fn: Callable[[T], U]) -> Ok[U]:
        return Ok(fn(self.value))


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> Any:
        raise RuntimeError(f"called unwrap() on Err: {self.error!r}")

    def unwrap_or(self, default: T) -> T:
        return default

    def map(self, _fn: Callable[[Any], Any]) -> Err[E]:
        return self


Result = Ok[T] | Err[E]


# --- API envelope (HTTP response shape) ---------------------------------------
class ErrorPayload(BaseModel):
    """Standard error payload in API response envelope."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: dict[str, Any] = {}


class ApiEnvelope(BaseModel, Generic[T]):
    """Standard API response envelope."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: T | None = None
    error: ErrorPayload | None = None
    trace_id: str
    timestamp: datetime


__all__ = ["ApiEnvelope", "Err", "ErrorPayload", "Ok", "Result"]
