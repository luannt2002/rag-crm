"""StepTracker proxy used by tests that build GraphState dicts manually.

Per-request fields (``step_tracker``, ``bot_system_prompt``, ``kg_service``,
``session_factory``) used to be passed as kwargs to ``build_graph(...)``; they
are now carried on ``GraphState``. This module exposes a single proxy tracker
object that nodes' ``state["step_tracker"].step(...)`` calls dispatch through.

The proxy looks up the *most-recently registered* tracker on every call so a
test that creates its own ``RecordingStepTracker``, registers it via
``register_active_tracker(tracker)``, and then drives the graph through state
built outside the helper module sees its ``step("...")`` invocations recorded
on the right instance. When no tracker is registered the proxy falls back to a
silent no-op so a stray pipeline run cannot raise ``KeyError`` on lookup.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


class _NoOpStepCtx:
    def set_metadata(self, **_a: Any) -> None:
        return None

    def add_tokens(self, **_a: Any) -> None:
        return None

    def record(self, **_a: Any) -> None:
        return None

    def record_llm(self, **_a: Any) -> None:
        """Wave M3.2 — match StepContext.record_llm signature."""
        return None


_NOOP_CTX = _NoOpStepCtx()


class _ProxyTracker:
    """Forwards ``step(...)`` to the most-recently registered concrete tracker."""

    def __init__(self) -> None:
        self._active: list[Any] = []

    def register(self, tracker: Any) -> None:
        self._active.append(tracker)

    def reset(self) -> None:
        self._active.clear()

    @asynccontextmanager
    async def step(self, name: str, **kw: Any):
        active = self._active[-1] if self._active else None
        if active is None:
            yield _NOOP_CTX
            return
        async with active.step(name, **kw) as ctx:
            yield ctx


_STATE_LIFT_DEFAULT_TRACKER = _ProxyTracker()


def register_active_tracker(tracker: Any) -> None:
    """Make subsequent ``state["step_tracker"].step(...)`` calls record on
    *tracker* until a different tracker is registered or ``reset_active_tracker``
    is called.
    """
    _STATE_LIFT_DEFAULT_TRACKER.register(tracker)


def reset_active_tracker() -> None:
    """Drop the registered tracker stack — used by per-test cleanup."""
    _STATE_LIFT_DEFAULT_TRACKER.reset()


__all__ = [
    "_STATE_LIFT_DEFAULT_TRACKER",
    "register_active_tracker",
    "reset_active_tracker",
]
