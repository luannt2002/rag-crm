"""Unit tests: BotSettingOptions strict bounds."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.dto.bot_config import BotSettingOptions


def test_defaults_valid() -> None:
    opts = BotSettingOptions()
    assert opts.frequency_penalty == 0.0
    assert opts.max_tokens == 450
    assert opts.response_format == "text"
    assert opts.presence_penalty == 0.0
    assert opts.temperature == 0.3
    assert opts.top_p == 0.4


def test_temperature_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        BotSettingOptions(temperature=2.5)
    with pytest.raises(ValidationError):
        BotSettingOptions(temperature=-0.1)


def test_top_p_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        BotSettingOptions(top_p=1.5)
    with pytest.raises(ValidationError):
        BotSettingOptions(top_p=-0.1)


def test_max_tokens_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        BotSettingOptions(max_tokens=0)
    with pytest.raises(ValidationError):
        BotSettingOptions(max_tokens=-10)
    with pytest.raises(ValidationError):
        BotSettingOptions(max_tokens=32001)


def test_response_format_literal() -> None:
    BotSettingOptions(response_format="text")
    BotSettingOptions(response_format="json_object")
    with pytest.raises(ValidationError):
        BotSettingOptions(response_format="html")


def test_frequency_penalty_bounds() -> None:
    BotSettingOptions(frequency_penalty=-2.0)
    BotSettingOptions(frequency_penalty=2.0)
    with pytest.raises(ValidationError):
        BotSettingOptions(frequency_penalty=-2.1)
    with pytest.raises(ValidationError):
        BotSettingOptions(frequency_penalty=2.1)


def test_presence_penalty_bounds() -> None:
    BotSettingOptions(presence_penalty=-2.0)
    BotSettingOptions(presence_penalty=2.0)
    with pytest.raises(ValidationError):
        BotSettingOptions(presence_penalty=-2.1)
    with pytest.raises(ValidationError):
        BotSettingOptions(presence_penalty=2.1)
