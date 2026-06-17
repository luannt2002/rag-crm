"""Test BotSettingOptions + BotConfig Pydantic DTO."""
from __future__ import annotations

import pytest
from uuid import uuid4

from ragbot.application.dto.bot_config import BotConfig, BotSettingOptions
from tests.conftest import TEST_TENANT_UUID


def test_bot_setting_options_defaults_six_llm_fields():
    opts = BotSettingOptions()
    assert opts.frequency_penalty == 0
    assert opts.max_tokens == 450
    assert opts.response_format == "text"
    assert opts.presence_penalty == 0
    assert opts.temperature == 0.3
    assert opts.top_p == 0.4


def test_bot_setting_options_extra_forbid():
    with pytest.raises(Exception):
        BotSettingOptions(unknown_field="x")


# Mirrors the ``resolve_workspace_id`` fallback to avoid coupling fixtures to
# any specific literal value the migration writes.
_TEST_WORKSPACE_ID = str(TEST_TENANT_UUID)


def test_bot_config_bot_id_not_empty():
    with pytest.raises(Exception):
        BotConfig(
            id=uuid4(), bot_id="  ", channel_type="api",
            bot_name="Test Bot", record_tenant_id=TEST_TENANT_UUID,
            workspace_id=_TEST_WORKSPACE_ID,
        )


def test_bot_config_bot_id_trimmed():
    cfg = BotConfig(
        id=uuid4(), bot_id="  BOT_X  ", channel_type="api",
        bot_name="Test", record_tenant_id=TEST_TENANT_UUID,
        workspace_id=_TEST_WORKSPACE_ID,
    )
    assert cfg.bot_id == "BOT_X"


def test_bot_config_record_tenant_id_required_uuid():
    """``BotConfig.record_tenant_id`` is REQUIRED ``UUID``.

    Legacy ``Optional[int]`` semantics removed; missing or ``None`` must
    raise a ``ValidationError`` so bootstrap/lookup never builds a
    poisoned cache entry with a "none" sentinel tenant segment.
    """
    # Missing record_tenant_id → ValidationError
    with pytest.raises(Exception):
        BotConfig(
            id=uuid4(), bot_id="B0", channel_type="api",
            bot_name="T", workspace_id=_TEST_WORKSPACE_ID,
        )
    # Explicit None → ValidationError (UUID field, no Optional)
    with pytest.raises(Exception):
        BotConfig(
            id=uuid4(), bot_id="B1", channel_type="api",
            bot_name="T", record_tenant_id=None,
            workspace_id=_TEST_WORKSPACE_ID,
        )
    # Happy path — concrete UUID accepted
    cfg2 = BotConfig(
        id=uuid4(), bot_id="B2", channel_type="api",
        bot_name="T", record_tenant_id=TEST_TENANT_UUID,
        workspace_id=_TEST_WORKSPACE_ID,
    )
    assert cfg2.record_tenant_id == TEST_TENANT_UUID


def test_bot_config_setting_options_default():
    cfg = BotConfig(
        id=uuid4(), bot_id="B3", channel_type="api", bot_name="T",
        record_tenant_id=TEST_TENANT_UUID,
        workspace_id=_TEST_WORKSPACE_ID,
    )
    assert cfg.setting_options.max_tokens == 450
    assert cfg.setting_options.temperature == 0.3


def test_bot_config_bypass_token_limit_default_false():
    cfg = BotConfig(
        id=uuid4(), bot_id="B4", channel_type="api", bot_name="T",
        record_tenant_id=TEST_TENANT_UUID,
        workspace_id=_TEST_WORKSPACE_ID,
    )
    assert cfg.bypass_token_limit is False


def test_bot_config_bypass_token_limit_true():
    cfg = BotConfig(
        id=uuid4(), bot_id="B5", channel_type="api", bot_name="T",
        record_tenant_id=TEST_TENANT_UUID, bypass_token_limit=True,
        workspace_id=_TEST_WORKSPACE_ID,
    )
    assert cfg.bypass_token_limit is True
