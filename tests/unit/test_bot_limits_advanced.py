"""Advanced unit tests: resolve_bot_limit edge cases + validate_plan_limits edge cases."""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from ragbot.shared.bot_limits import (
    PLAN_LIMIT_SCHEMA,
    resolve_bot_limit,
    validate_plan_limits,
)


def _ns(**kwargs) -> SimpleNamespace:
    """Lightweight fake bot config — no DB dependency."""
    return SimpleNamespace(**kwargs)


# ── resolve_bot_limit: numeric max() logic ──────────────────────────────


class TestResolveBotLimitMaxLogic:
    def test_system_covers_bad_bot_input(self):
        """max(bot=1, system=10) → 10: system default protects against typos."""
        bot = _ns(retrieval_top_k=1, plan_limits=None)
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=10) == 10

    def test_bot_upgrade_wins(self):
        """max(bot=20, system=10) → 20: higher bot value is honoured."""
        bot = _ns(retrieval_top_k=20, plan_limits=None)
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=10) == 20

    def test_bot_none_system_present(self):
        """bot=None, system=10 → 10."""
        bot = _ns(plan_limits=None)
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=10) == 10

    def test_both_none_falls_to_schema_default(self):
        """bot=None, system=None → schema default (20 for retrieval_top_k)."""
        bot = _ns(plan_limits=None)
        result = resolve_bot_limit(bot, "retrieval_top_k", system_default=None)
        assert result == PLAN_LIMIT_SCHEMA["retrieval_top_k"]["default"]

    def test_plan_limits_numeric_in_range_bot_wins(self):
        """Bot value in schema [min, max] range: bot WINS over system_default.

        260525 Bug #6 fix — prior behaviour applied ``max(bot, system)``
        which made it impossible to override numeric defaults DOWNWARD.
        Now the bot value wins as long as it falls inside the documented
        schema range. ``retrieval_top_k`` schema has ``min=5, max=200``;
        bot=5 is the lower bound → in-range → bot wins.
        """
        bot = _ns(plan_limits={"retrieval_top_k": 5})
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=15) == 5

    def test_plan_limits_numeric_out_of_range_falls_back_to_system(self):
        """Bot value below schema ``min``: rejected → system_default wins.

        260525 Bug #6 fix — schema range guard replaces the old
        ``max(bot, system)`` heuristic. A typo like
        ``retrieval_top_k = 1`` (below schema min=5) is now caught
        in-place rather than silently elevated to system value.
        """
        bot = _ns(plan_limits={"retrieval_top_k": 1})
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=15) == 15

    def test_float_no_schema_bot_wins(self):
        """No schema entry for the key: bot value wins (no range guard to apply)."""
        bot = _ns(some_float=0.5, plan_limits=None)
        # 260525 Bug #6 fix — was 0.8 under old max() heuristic.
        assert resolve_bot_limit(bot, "some_float", system_default=0.8) == 0.5


# ── resolve_bot_limit: boolean overrides ────────────────────────────────


class TestResolveBotLimitBoolOverride:
    def test_bot_true_overrides_system_false(self):
        """Boolean: bot=True overrides system=False (non-numeric, bot wins)."""
        bot = _ns(plan_limits={"reflection_enabled": True})
        assert resolve_bot_limit(bot, "reflection_enabled", system_default=False) is True

    def test_bot_false_overrides_system_true(self):
        """Boolean: bot=False overrides system=True. Bot WINS outright.

        260525 Bug #6 fix — prior behaviour applied ``max(False, True)``
        treating bool as int and silently elevating False to True. That
        made it impossible for a bot owner to opt OUT of a default-ON
        feature like ``reflection_enabled``. Now bot wins explicitly;
        the schema range guard skips booleans (no min/max declared).
        """
        bot = _ns(plan_limits={"reflection_enabled": False})
        assert resolve_bot_limit(bot, "reflection_enabled", system_default=True) is False


# ── resolve_bot_limit: string enum overrides ────────────────────────────


class TestResolveBotLimitStringOverride:
    def test_bot_enabled_overrides_system_disabled(self):
        """String enum: bot='enabled' overrides system='disabled'."""
        bot = _ns(plan_limits={"graph_rag_mode": "enabled"})
        result = resolve_bot_limit(bot, "graph_rag_mode", system_default="disabled")
        assert result == "enabled"

    def test_bot_adaptive_overrides_system_enabled(self):
        bot = _ns(plan_limits={"graph_rag_mode": "adaptive"})
        result = resolve_bot_limit(bot, "graph_rag_mode", system_default="enabled")
        assert result == "adaptive"


# ── validate_plan_limits: edge cases ────────────────────────────────────


class TestValidatePlanLimitsAdvanced:
    def test_unknown_keys_dropped(self):
        result = validate_plan_limits({"totally_unknown": 42, "also_unknown": "x"})
        assert result == {}

    def test_out_of_range_int_clamped_low(self):
        result = validate_plan_limits({"cache_ttl_s": 1})
        assert result["cache_ttl_s"] == 60  # min=60

    def test_out_of_range_int_clamped_high(self):
        result = validate_plan_limits({"cache_ttl_s": 999_999})
        assert result["cache_ttl_s"] == 86400  # max=86400

    def test_invalid_enum_raises_valueerror(self):
        with pytest.raises(ValueError, match="must be one of"):
            validate_plan_limits({"graph_rag_mode": "turbo"})

    def test_mixed_valid_and_unknown(self):
        result = validate_plan_limits({
            "retrieval_top_k": 50,
            "nonexistent": True,
        })
        assert "nonexistent" not in result
        assert result["retrieval_top_k"] == 50
