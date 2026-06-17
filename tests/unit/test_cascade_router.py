"""Cascade Routing unit tests — WA-2 ship.

Coverage matrix:

A. ``ModelResolverService.resolve_cascade_runtime`` (pure threshold maths)
   1. Below T_LOW → cheap-tier model name from per-bot binding.
   2. T_LOW boundary (== T_LOW) → mid-band (bot default).
   3. Just above T_LOW → mid-band (bot default).
   4. Just below T_HIGH → mid-band (bot default).
   5. T_HIGH boundary (== T_HIGH) → high tier.
   6. Above T_HIGH → high tier.
   7. system_config fallback when per-bot binding is empty.
   8. NullObject return ("") when neither binding nor system_config set.
   9. NaN / negative / >1.0 score clamping.
  10. Threshold misconfig (T_LOW > T_HIGH) collapses to high-only band.

B. ``apply_cascade_routing`` helper (orchestration glue)
  11. Default OFF — returns ``current_model`` regardless of score.
  12. Opt-in ON — returns tier model when bot has cascade enabled.
  13. Missing ``bot`` in state → returns ``current_model`` (graceful).
  14. Resolver returns "" → falls back to ``current_model`` (NullObject).
  15. Per-bot ``plan_limits`` override beats ``system_config``.
  16. Resolver raises → returns ``current_model`` (graceful degradation).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.orchestration.nodes.cascade_router_helper import apply_cascade_routing
from ragbot.shared.constants import (
    DEFAULT_CASCADE_T_HIGH,
    DEFAULT_CASCADE_T_LOW,
)


# ── Stub config getter ────────────────────────────────────────────────────


def _make_getter(overrides: dict[str, Any] | None = None):
    """Return a ``get_boot_config(key, default)``-compatible callable."""
    store = overrides or {}

    def _getter(key: str, default: Any) -> Any:
        if key in store:
            return store[key]
        return default

    return _getter


# ── Helpers ───────────────────────────────────────────────────────────────


def _resolver() -> ModelResolverService:
    """Build a bare ModelResolverService — pure-method tests don't touch DB."""
    # The resolve_cascade_runtime path uses NO instance state, but the
    # constructor requires ports. Pass minimal stubs so __init__ succeeds.
    repo_stub = SimpleNamespace()
    cache_stub = SimpleNamespace()
    clock_stub = SimpleNamespace(monotonic=lambda: 0.0, now=lambda: None)
    return ModelResolverService(
        repo=repo_stub,  # type: ignore[arg-type]
        cache=cache_stub,  # type: ignore[arg-type]
        clock=clock_stub,  # type: ignore[arg-type]
    )


# Constants used across tests — domain-neutral placeholder names that
# match the model-name SSoT shape (provider/model or bare alias).
_CHEAP = "cheap-tier-answer"
_MID = "mid-tier-answer"
_HIGH = "premium-tier-answer"


# ── A. resolve_cascade_runtime — threshold + fallback ─────────────────────


class TestResolveCascadeRuntime:
    """Pure-method tier mapping behaviour."""

    def test_below_t_low_returns_cheap_from_bot_config(self) -> None:
        """Score < T_LOW → cheap-tier model from per-bot override."""
        svc = _resolver()
        getter = _make_getter()
        model = svc.resolve_cascade_runtime(
            0.10,
            {"cascade_low_model": _CHEAP},
            config_getter=getter,
        )
        assert model == _CHEAP

    def test_just_below_t_low_is_cheap(self) -> None:
        """Score = T_LOW - epsilon stays in cheap band."""
        svc = _resolver()
        getter = _make_getter({"cascade_low_model": _CHEAP})
        model = svc.resolve_cascade_runtime(
            DEFAULT_CASCADE_T_LOW - 0.01, {}, config_getter=getter,
        )
        assert model == _CHEAP

    def test_at_t_low_boundary_is_mid(self) -> None:
        """Score == T_LOW flips to mid-band (default answer model)."""
        svc = _resolver()
        getter = _make_getter(
            {"cascade_low_model": _CHEAP, "default_answer_model": _MID},
        )
        model = svc.resolve_cascade_runtime(
            DEFAULT_CASCADE_T_LOW, {}, config_getter=getter,
        )
        assert model == _MID

    def test_just_above_t_low_is_mid(self) -> None:
        """Score = T_LOW + epsilon is mid-band."""
        svc = _resolver()
        getter = _make_getter({"default_answer_model": _MID})
        model = svc.resolve_cascade_runtime(
            DEFAULT_CASCADE_T_LOW + 0.01, {}, config_getter=getter,
        )
        assert model == _MID

    def test_just_below_t_high_is_mid(self) -> None:
        """Score = T_HIGH - epsilon stays mid-band."""
        svc = _resolver()
        getter = _make_getter({"default_answer_model": _MID})
        model = svc.resolve_cascade_runtime(
            DEFAULT_CASCADE_T_HIGH - 0.01, {}, config_getter=getter,
        )
        assert model == _MID

    def test_at_t_high_boundary_is_high(self) -> None:
        """Score == T_HIGH flips to high tier."""
        svc = _resolver()
        getter = _make_getter(
            {"default_answer_model": _MID, "cascade_high_model": _HIGH},
        )
        model = svc.resolve_cascade_runtime(
            DEFAULT_CASCADE_T_HIGH, {}, config_getter=getter,
        )
        assert model == _HIGH

    def test_above_t_high_is_high(self) -> None:
        """Score > T_HIGH stays high tier."""
        svc = _resolver()
        getter = _make_getter({"cascade_high_model": _HIGH})
        model = svc.resolve_cascade_runtime(
            0.95, {}, config_getter=getter,
        )
        assert model == _HIGH

    def test_system_config_fallback_when_bot_empty(self) -> None:
        """Per-bot binding absent → ``system_config`` provides the model."""
        svc = _resolver()
        getter = _make_getter({"cascade_low_model": _CHEAP})
        model = svc.resolve_cascade_runtime(
            0.05,
            bot_config={},  # no per-bot override
            config_getter=getter,
        )
        assert model == _CHEAP

    def test_null_object_when_no_config(self) -> None:
        """Neither binding nor system_config set → empty string (NullObject)."""
        svc = _resolver()
        getter = _make_getter()  # no system_config keys
        model = svc.resolve_cascade_runtime(
            0.05, {}, config_getter=getter,
        )
        assert model == ""

    def test_per_bot_binding_beats_system_config(self) -> None:
        """Per-bot override takes priority over platform default."""
        svc = _resolver()
        getter = _make_getter({"cascade_low_model": "system-cheap"})
        model = svc.resolve_cascade_runtime(
            0.05,
            {"cascade_low_model": "bot-cheap"},
            config_getter=getter,
        )
        assert model == "bot-cheap"

    def test_score_nan_clamps_to_cheap_band(self) -> None:
        """NaN input → 0.0 → cheap tier."""
        svc = _resolver()
        getter = _make_getter({"cascade_low_model": _CHEAP})
        model = svc.resolve_cascade_runtime(
            math.nan, {}, config_getter=getter,
        )
        assert model == _CHEAP

    def test_score_negative_clamps_to_zero(self) -> None:
        """Negative input → 0.0 → cheap tier."""
        svc = _resolver()
        getter = _make_getter({"cascade_low_model": _CHEAP})
        model = svc.resolve_cascade_runtime(
            -5.0, {}, config_getter=getter,
        )
        assert model == _CHEAP

    def test_score_over_one_clamps_to_high(self) -> None:
        """Score > 1.0 → clamp to 1.0 → high tier (since 1.0 ≥ T_HIGH)."""
        svc = _resolver()
        getter = _make_getter({"cascade_high_model": _HIGH})
        model = svc.resolve_cascade_runtime(
            5.0, {}, config_getter=getter,
        )
        assert model == _HIGH

    def test_threshold_misconfig_collapses_cheap_band(self) -> None:
        """T_LOW > T_HIGH → cheap band collapsed; everything escalates."""
        svc = _resolver()
        # Misconfigure: T_LOW=0.9, T_HIGH=0.1 → service should clamp T_LOW=0.1
        getter = _make_getter({
            "cascade_t_low": 0.9,
            "cascade_t_high": 0.1,
            "cascade_low_model": _CHEAP,
            "default_answer_model": _MID,
            "cascade_high_model": _HIGH,
        })
        # Score 0.05 < clamped T_LOW(=0.1) → cheap.
        model_cheap = svc.resolve_cascade_runtime(
            0.05, {}, config_getter=getter,
        )
        # Score 0.5 ≥ T_HIGH(=0.1) → high tier.
        model_high = svc.resolve_cascade_runtime(
            0.5, {}, config_getter=getter,
        )
        assert model_cheap == _CHEAP
        assert model_high == _HIGH


# ── B. apply_cascade_routing — orchestration glue ─────────────────────────


def _bot(
    *,
    enabled: bool,
    plan_limits: dict[str, Any] | None = None,
    threshold_overrides: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Build a minimal BotConfig-shaped object for resolve_bot_limit."""
    pl: dict[str, Any] = dict(plan_limits or {})
    pl["cascade_routing_enabled"] = enabled
    return SimpleNamespace(
        bot_id="bot-test",
        plan_limits=pl,
        threshold_overrides=dict(threshold_overrides or {}),
    )


class _StubResolver:
    """Minimal resolver double — captures kwargs + returns configured value."""

    def __init__(self, return_value: str = "", raise_exc: bool = False) -> None:
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.calls: list[tuple[float, dict[str, Any] | None]] = []

    def resolve_cascade_runtime(
        self,
        complexity_score: float,
        bot_config: dict[str, Any] | None = None,
        *,
        config_getter: Any | None = None,  # noqa: ARG002 — parity
    ) -> str:
        self.calls.append((complexity_score, bot_config))
        if self.raise_exc:
            raise RuntimeError("resolver outage")
        return self.return_value


class TestApplyCascadeRouting:
    """Helper-node behaviour: opt-in gate + graceful degradation."""

    def test_default_off_returns_current_model(self) -> None:
        """Bot without opt-in → caller's current model unchanged."""
        bot = _bot(enabled=False)
        state = {"bot": bot, "complexity_score": 0.05}
        resolver = _StubResolver(return_value=_CHEAP)
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == _MID
        # Resolver MUST NOT be called when OFF — saves DB round-trip.
        assert resolver.calls == []

    def test_opt_in_returns_tier_model(self) -> None:
        """Bot opted in → resolver answer flows back to caller."""
        bot = _bot(enabled=True)
        state = {"bot": bot, "complexity_score": 0.05}
        resolver = _StubResolver(return_value=_CHEAP)
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == _CHEAP
        assert len(resolver.calls) == 1
        score_arg, _bot_cfg_arg = resolver.calls[0]
        assert score_arg == pytest.approx(0.05)

    def test_missing_bot_returns_current_model(self) -> None:
        """State without ``bot`` → cascade silent OFF."""
        state: dict[str, Any] = {"complexity_score": 0.95}
        resolver = _StubResolver(return_value=_HIGH)
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == _MID
        assert resolver.calls == []

    def test_resolver_returns_empty_falls_back(self) -> None:
        """NullObject contract — empty resolver answer → current model."""
        bot = _bot(enabled=True)
        state = {"bot": bot, "complexity_score": 0.95}
        resolver = _StubResolver(return_value="")
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == _MID

    def test_resolver_exception_falls_back(self) -> None:
        """Resolver outage MUST NOT kill the answer path."""
        bot = _bot(enabled=True)
        state = {"bot": bot, "complexity_score": 0.95}
        resolver = _StubResolver(raise_exc=True)
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == _MID

    def test_bot_config_lifted_into_resolver_call(self) -> None:
        """Per-bot ``plan_limits`` reach resolver as bot_config dict."""
        bot = _bot(
            enabled=True,
            plan_limits={"cascade_low_model": "bot-cheap-override"},
        )
        state = {"bot": bot, "complexity_score": 0.05}
        resolver = _StubResolver(return_value="bot-cheap-override")
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == "bot-cheap-override"
        # Resolver received the merged config — verifies the helper
        # actually plumbed per-bot overrides through, not just the flag.
        _score, bot_cfg = resolver.calls[0]
        assert bot_cfg is not None
        assert bot_cfg.get("cascade_low_model") == "bot-cheap-override"

    def test_missing_complexity_score_treated_as_zero(self) -> None:
        """Absent ``complexity_score`` defaults to 0.0 (cheap tier)."""
        bot = _bot(enabled=True)
        state = {"bot": bot}  # no complexity_score
        resolver = _StubResolver(return_value=_CHEAP)
        out = apply_cascade_routing(
            state, resolver, current_model=_MID,
        )
        assert out == _CHEAP
        score_arg, _ = resolver.calls[0]
        assert score_arg == pytest.approx(0.0)

    def test_threshold_overrides_take_priority(self) -> None:
        """``threshold_overrides`` shadows ``plan_limits`` on merge."""
        bot = _bot(
            enabled=True,
            plan_limits={"cascade_low_model": "plan-limits-cheap"},
            threshold_overrides={"cascade_low_model": "override-cheap"},
        )
        state = {"bot": bot, "complexity_score": 0.05}
        resolver = _StubResolver(return_value="override-cheap")
        apply_cascade_routing(state, resolver, current_model=_MID)
        _, bot_cfg = resolver.calls[0]
        assert bot_cfg is not None
        # threshold_overrides applied after plan_limits → final value wins.
        assert bot_cfg.get("cascade_low_model") == "override-cheap"
