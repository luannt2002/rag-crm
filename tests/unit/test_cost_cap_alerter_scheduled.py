"""Cost-cap alerter is scheduled, not just an offline script (D11 / P2-J).

P2-J found ``evaluate_tenants`` was correct but only an offline script
called it — no scheduler, so monthly-token-cap warnings never fired in
production. It now runs as an embedded worker task on a fixed interval.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_alerter_registered_in_embedded_workers() -> None:
    import ragbot.interfaces.http.embedded_workers as ew

    assert hasattr(ew, "run_embedded_cost_cap_alerter")
    src = __import__("inspect").getsource(ew.start_embedded_workers)
    assert "run_embedded_cost_cap_alerter" in src, (
        "the cost-cap alerter loop must be spawned in start_embedded_workers"
    )


def test_interval_constant_is_positive() -> None:
    from ragbot.shared.constants import DEFAULT_COST_CAP_ALERT_INTERVAL_S

    assert DEFAULT_COST_CAP_ALERT_INTERVAL_S > 0


@pytest.mark.asyncio
async def test_alerter_sweeps_then_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """One loop iteration: opens a session, evaluates, then awaits the
    interval sleep (which we interrupt to end the test)."""
    import ragbot.interfaces.http.embedded_workers as ew

    evaluated = {"n": 0}

    async def _fake_eval(*, session, logger):  # noqa: ANN001
        evaluated["n"] += 1
        return []

    monkeypatch.setattr(ew, "evaluate_tenants", _fake_eval)

    # Make the post-sweep sleep raise so the infinite loop exits deterministically.
    async def _stop_sleep(_seconds):  # noqa: ANN001
        raise asyncio.CancelledError

    monkeypatch.setattr(ew.asyncio, "sleep", _stop_sleep)

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    container = MagicMock()
    container.session_factory.return_value = lambda: session_cm

    with pytest.raises(asyncio.CancelledError):
        await ew.run_embedded_cost_cap_alerter(container)

    assert evaluated["n"] == 1, "alerter must run one sweep before sleeping"


@pytest.mark.asyncio
async def test_sweep_error_does_not_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient DB error during a sweep is logged and the loop sleeps to
    retry — it must never crash the API process."""
    import ragbot.interfaces.http.embedded_workers as ew

    async def _boom(*, session, logger):  # noqa: ANN001
        raise RuntimeError("db blip")

    monkeypatch.setattr(ew, "evaluate_tenants", _boom)

    async def _stop_sleep(_seconds):  # noqa: ANN001
        raise asyncio.CancelledError

    monkeypatch.setattr(ew.asyncio, "sleep", _stop_sleep)

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    container = MagicMock()
    container.session_factory.return_value = lambda: session_cm

    # RuntimeError is caught (logged) → loop reaches sleep → CancelledError.
    with pytest.raises(asyncio.CancelledError):
        await ew.run_embedded_cost_cap_alerter(container)
