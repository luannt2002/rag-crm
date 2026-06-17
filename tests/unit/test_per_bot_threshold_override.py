"""Unit tests for per-bot threshold_overrides resolve chain (Stream V Phase 2)."""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any

from ragbot.shared.bot_limits import (
    PLAN_LIMIT_SCHEMA,
    resolve_bot_limit,
    validate_plan_limits,
    get_effective_config,
)


@dataclass
class FakeBotConfig:
    """Minimal BotConfig stub for threshold override testing."""
    plan_limits: dict[str, Any] = field(default_factory=dict)
    threshold_overrides: dict[str, Any] = field(default_factory=dict)
    max_documents: int = 5
    max_history: int | None = None
    prompt_max_tokens: int | None = None
    rerank_top_n: int | None = None


class TestThresholdOverrideSchema:
    """Verify the new threshold keys are in PLAN_LIMIT_SCHEMA."""

    def test_reranker_min_score_active_in_schema(self):
        assert "reranker_min_score_active" in PLAN_LIMIT_SCHEMA
        s = PLAN_LIMIT_SCHEMA["reranker_min_score_active"]
        assert s["type"] == "float"
        # Calibrated floor: empirical ZE rerank scores 0.3+ correlate
        # with relevant retrievals. See test_rerank_defaults_recalibrated.
        assert s["default"] == 0.30

    def test_grounding_check_threshold_in_schema(self):
        assert "grounding_check_threshold" in PLAN_LIMIT_SCHEMA
        s = PLAN_LIMIT_SCHEMA["grounding_check_threshold"]
        assert s["type"] == "float"
        assert s["default"] == 0.30

    def test_guard_output_min_score_in_schema(self):
        assert "guard_output_min_score" in PLAN_LIMIT_SCHEMA
        s = PLAN_LIMIT_SCHEMA["guard_output_min_score"]
        assert s["type"] == "float"

    def test_generate_context_chars_cap_in_schema(self):
        assert "generate_context_chars_cap" in PLAN_LIMIT_SCHEMA
        s = PLAN_LIMIT_SCHEMA["generate_context_chars_cap"]
        assert s["type"] == "int"
        assert s["default"] == 2900
        assert s["min"] == 500
        assert s["max"] == 50000


class TestResolveBotLimitThresholdOverrides:
    """Verify threshold_overrides takes priority in the resolve chain."""

    def test_threshold_override_wins_over_plan_limits(self):
        bot = FakeBotConfig(
            plan_limits={"reranker_min_score_active": 0.05},
            threshold_overrides={"reranker_min_score_active": 0.25},
        )
        result = resolve_bot_limit(bot, "reranker_min_score_active", system_default=0.15)
        assert result == 0.25  # threshold_overrides wins

    def test_threshold_override_wins_over_system_default(self):
        bot = FakeBotConfig(
            threshold_overrides={"grounding_check_threshold": 0.50},
        )
        result = resolve_bot_limit(bot, "grounding_check_threshold", system_default=0.30)
        assert isinstance(result, (int, float))
        assert result == 0.50

    def test_plan_limits_used_when_threshold_missing(self):
        """260525 Bug #6 fix — bot WINS outright when set, no max() floor.

        Pre-fix: ``max(0.10, 0.15) = 0.15`` (system floor silently elevated
        the bot value). Post-fix: bot 0.10 wins. ``reranker_min_score_active``
        has no PLAN_LIMIT_SCHEMA range entry so no range guard fires.
        """
        bot = FakeBotConfig(
            plan_limits={"reranker_min_score_active": 0.10},
            threshold_overrides={},
        )
        result = resolve_bot_limit(bot, "reranker_min_score_active", system_default=0.15)
        assert result == 0.10  # bot wins (Bug #6 fix)

    def test_system_default_used_when_both_missing(self):
        bot = FakeBotConfig()
        result = resolve_bot_limit(bot, "generate_context_chars_cap", system_default=5000)
        assert isinstance(result, int)
        assert result >= 5000

    def test_schema_default_used_without_system_default(self):
        bot = FakeBotConfig()
        result = resolve_bot_limit(bot, "generate_context_chars_cap", system_default=None)
        assert result == 2900  # schema default

    def test_numeric_threshold_bot_wins_no_floor(self):
        """260525 Bug #6 fix — bot can lower numeric threshold below system.

        Renamed from test_max_logic_applies_to_numeric_threshold to reflect
        the new contract. ``grounding_check_threshold`` has no schema range
        entry so the range guard does not apply; bot 0.10 wins outright.
        """
        bot = FakeBotConfig(
            threshold_overrides={"grounding_check_threshold": 0.10},
        )
        result = resolve_bot_limit(bot, "grounding_check_threshold", system_default=0.30)
        assert result == 0.10  # bot wins (Bug #6 fix)

    def test_context_chars_cap_override(self):
        bot = FakeBotConfig(
            threshold_overrides={"generate_context_chars_cap": 5000},
        )
        result = resolve_bot_limit(bot, "generate_context_chars_cap", system_default=2900)
        assert result == 5000  # max(5000, 2900) = 5000

    def test_missing_key_returns_none_without_schema(self):
        bot = FakeBotConfig()
        result = resolve_bot_limit(bot, "nonexistent_key")
        assert result is None


class TestGetEffectiveConfigWithThresholds:
    """Verify get_effective_config includes threshold keys."""

    def test_effective_config_includes_threshold_keys(self):
        bot = FakeBotConfig(
            threshold_overrides={"reranker_min_score_active": 0.20},
        )
        config = get_effective_config(bot, system_defaults={})
        assert "reranker_min_score_active" in config
        assert isinstance(config["reranker_min_score_active"], (int, float))
        assert "grounding_check_threshold" in config
        assert "guard_output_min_score" in config
        assert "generate_context_chars_cap" in config


class TestValidatePlanLimitsThresholds:
    """Verify threshold keys are accepted by plan_limits validator."""

    def test_float_threshold_accepted(self):
        result = validate_plan_limits({"reranker_min_score_active": 0.25})
        assert result["reranker_min_score_active"] == 0.25

    def test_float_clamped_to_max(self):
        result = validate_plan_limits({"reranker_min_score_active": 2.0})
        assert result["reranker_min_score_active"] == 1.0  # clamped

    def test_int_threshold_accepted(self):
        result = validate_plan_limits({"generate_context_chars_cap": 5000})
        assert result["generate_context_chars_cap"] == 5000

    def test_int_clamped_to_min(self):
        result = validate_plan_limits({"generate_context_chars_cap": 100})
        assert result["generate_context_chars_cap"] == 500  # clamped to min

    def test_int_clamped_to_max(self):
        result = validate_plan_limits({"generate_context_chars_cap": 99999})
        assert result["generate_context_chars_cap"] == 50000  # clamped to max
