"""Unit test: BotModel.setting_options default uses constants (not hardcoded literals).

Phase 2 Y1 infra audit 2026-04-29: P0-BUG-3 fix verification.

Ensures that BotModel ORM default for setting_options is driven by constants.py
so that changing DEFAULT_GENERATION_MAX_TOKENS actually affects new bots.
"""

from __future__ import annotations

from ragbot.shared.constants import (
    DEFAULT_FREQUENCY_PENALTY,
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_PRESENCE_PENALTY,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
)


def _get_default_setting_options() -> dict:
    """Invoke the BotModel.setting_options default lambda without DB.

    SQLAlchemy callable column defaults receive an `ExecutionContext` as
    their first positional arg; we pass None since the lambda ignores it.
    """
    from ragbot.infrastructure.db.models import BotModel

    col = BotModel.__table__.c["setting_options"]
    # SQLAlchemy stores the default as a ColumnDefault whose `arg` is the lambda
    default_fn = col.default.arg
    return default_fn(None)  # None satisfies the ctx positional param


def test_setting_options_max_tokens_uses_constant() -> None:
    opts = _get_default_setting_options()
    assert opts["max_tokens"] == DEFAULT_GENERATION_MAX_TOKENS, (
        f"BotModel.setting_options default max_tokens={opts['max_tokens']!r} "
        f"but DEFAULT_GENERATION_MAX_TOKENS={DEFAULT_GENERATION_MAX_TOKENS!r}. "
        "The ORM default must import from constants, not use a bare literal."
    )


def test_setting_options_temperature_uses_constant() -> None:
    opts = _get_default_setting_options()
    assert opts["temperature"] == DEFAULT_TEMPERATURE


def test_setting_options_top_p_uses_constant() -> None:
    opts = _get_default_setting_options()
    assert opts["top_p"] == DEFAULT_TOP_P


def test_setting_options_frequency_penalty_uses_constant() -> None:
    opts = _get_default_setting_options()
    assert opts["frequency_penalty"] == DEFAULT_FREQUENCY_PENALTY


def test_setting_options_presence_penalty_uses_constant() -> None:
    opts = _get_default_setting_options()
    assert opts["presence_penalty"] == DEFAULT_PRESENCE_PENALTY


def test_setting_options_response_format_default() -> None:
    opts = _get_default_setting_options()
    assert opts["response_format"] == "text"
