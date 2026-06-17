"""BF1 Wave G — cascade threshold recalibrate pin tests.

WE-1 50-turn pilot (`reports/WAVE_E_CASCADE_PILOT_20260519.md`) found
the legacy `cascade_t_low=0.3` band was structurally unreachable on the
medispa pilot bot — every probe scored ≥0.55 post-clamp, leaving the
Haiku tier at 0/25 hits. Wave G ships a fresh calibration pair:

* ``DEFAULT_CASCADE_T_LOW`` 0.3 → 0.6
* ``DEFAULT_CASCADE_T_HIGH`` 0.7 → 0.9

The 0.9 ceiling stays inside the resolver's [0.0, 1.0] post-clamp band
so the premium tier remains reachable for clearly multi-entity probes;
pushing it beyond 1.0 would collapse the high tier to dead code (the
resolver clamps before the band comparison — see
``ModelResolverService.resolve_cascade_runtime``).

These tests pin the new band so any future drift surfaces at the
test-collection stage (same pattern as the integration band sanity
guard at ``tests/integration/test_cascade_routing_e2e.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.shared.constants import (
    DEFAULT_CASCADE_T_HIGH,
    DEFAULT_CASCADE_T_LOW,
)


def _resolver() -> ModelResolverService:
    """Bare resolver — pure-method tests never touch DB."""
    repo_stub = SimpleNamespace()
    cache_stub = SimpleNamespace()
    clock_stub = SimpleNamespace(monotonic=lambda: 0.0, now=lambda: None)
    return ModelResolverService(
        repo=repo_stub,  # type: ignore[arg-type]
        cache=cache_stub,  # type: ignore[arg-type]
        clock=clock_stub,  # type: ignore[arg-type]
    )


def _make_getter(overrides: dict[str, Any] | None = None):
    """Build a get_boot_config(key, default)-compatible callable."""
    store = overrides or {}

    def _getter(key: str, default: Any) -> Any:
        if key in store:
            return store[key]
        return default

    return _getter


_CHEAP = "cheap-tier-model"
_MID = "mid-tier-model"
_HIGH = "premium-tier-model"


def test_wave_g_t_low_equals_zero_point_six() -> None:
    """Recalibrated default for the cheap band entry point.

    Justification: WE-1 lowest observed complexity_score post-clamp =
    0.55 on the 25-turn medispa pool. Bumping T_LOW to 0.6 catches the
    greeting + simple-lookup band that was scoring 0.55-0.59 yet
    failing to trigger Haiku at the legacy 0.3 floor.
    """
    assert DEFAULT_CASCADE_T_LOW == pytest.approx(0.6), (
        "Wave G mandate: T_LOW must be 0.6 so Haiku tier becomes "
        "reachable on the medispa pool histogram."
    )


def test_wave_g_t_high_equals_zero_point_nine() -> None:
    """Recalibrated default for the premium band entry point.

    Justification: WE-1 promoted 14/25 turns to Sonnet at the legacy
    0.7 ceiling — too aggressive. Bumping to 0.9 reserves the premium
    tier for clearly multi-entity / hypothetical queries while keeping
    it reachable inside the post-clamp [0.0, 1.0] band.
    """
    assert DEFAULT_CASCADE_T_HIGH == pytest.approx(0.9), (
        "Wave G mandate: T_HIGH must be 0.9 so Sonnet stays reachable "
        "yet less aggressive than the legacy 0.7."
    )


def test_wave_g_t_high_stays_within_post_clamp_ceiling() -> None:
    """T_HIGH > 1.0 collapses the premium tier to dead code.

    The resolver clamps complexity_score into [0.0, 1.0] before the
    band comparison. Any T_HIGH > 1.0 makes ``score >= t_high``
    unsatisfiable, silently killing the high tier. Wave G honours this
    invariant by capping at 0.9. This test pins the invariant for
    future tuning.
    """
    assert DEFAULT_CASCADE_T_HIGH <= 1.0, (
        f"T_HIGH={DEFAULT_CASCADE_T_HIGH} > 1.0 would dead-code the "
        "premium tier (resolver clamps score to 1.0)."
    )


def test_wave_g_t_low_below_t_high_so_mid_band_exists() -> None:
    """Cheap < Premium so the mid band has a non-empty range."""
    assert DEFAULT_CASCADE_T_LOW < DEFAULT_CASCADE_T_HIGH, (
        f"T_LOW={DEFAULT_CASCADE_T_LOW} ≥ T_HIGH={DEFAULT_CASCADE_T_HIGH} "
        "would collapse the mid band — bot defaults would never fire."
    )


def test_wave_g_band_routes_score_0_55_to_cheap() -> None:
    """WE-1 lowest observed score (~0.55) MUST land in the cheap band.

    This is the smoking-gun behavioural assertion behind the
    recalibration: the legacy band put 0.55 in the mid tier (0.3 ≤
    0.55 < 0.7), giving the bot's default answer model. The new band
    pulls 0.55 down into the cheap tier where Haiku takes the turn.
    """
    svc = _resolver()
    getter = _make_getter(
        {
            "cascade_low_model": _CHEAP,
            "default_answer_model": _MID,
            "cascade_high_model": _HIGH,
        },
    )
    model = svc.resolve_cascade_runtime(0.55, {}, config_getter=getter)
    assert model == _CHEAP, (
        f"WE-1 lowest-observed score 0.55 must route to cheap (Haiku) "
        f"with the new band; got {model}."
    )


def test_wave_g_band_routes_score_0_75_to_mid() -> None:
    """Mid-band default queries land at the bot's current model.

    Score 0.75 is inside [T_LOW=0.6, T_HIGH=0.9) — the mid band
    preserves the bot's status-quo answer model.
    """
    svc = _resolver()
    getter = _make_getter(
        {
            "cascade_low_model": _CHEAP,
            "default_answer_model": _MID,
            "cascade_high_model": _HIGH,
        },
    )
    model = svc.resolve_cascade_runtime(0.75, {}, config_getter=getter)
    assert model == _MID, (
        f"Mid-band score 0.75 must route to mid (bot default); got {model}."
    )


def test_wave_g_band_routes_score_0_95_to_premium() -> None:
    """Clearly complex score lands at the premium tier.

    Score 0.95 ≥ T_HIGH=0.9 → Sonnet (or the configured premium model).
    """
    svc = _resolver()
    getter = _make_getter(
        {
            "cascade_low_model": _CHEAP,
            "default_answer_model": _MID,
            "cascade_high_model": _HIGH,
        },
    )
    model = svc.resolve_cascade_runtime(0.95, {}, config_getter=getter)
    assert model == _HIGH, (
        f"High-band score 0.95 must route to premium (Sonnet); got {model}."
    )
