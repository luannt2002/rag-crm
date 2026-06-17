"""Unit tests for OosTemplateResolver — 7-tier chain.

Each tier in turn is verified by setting only that tier and stubbing the
inner ports (config_service, language_pack_service) with AsyncMock. The
tests intentionally use ``SimpleNamespace`` for the ``bot`` arg so they
exercise the same attribute-access path that production hits with the
SQLAlchemy mapped object.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.oos_template_resolver import OosTemplateResolver


def _resolver(
    *,
    system_config_value: str | None = None,
    language_pack_value: str | None = None,
) -> OosTemplateResolver:
    """Build a resolver with stubbed ports. ``None`` means tier returns empty."""
    cfg = SimpleNamespace(get=AsyncMock(return_value=system_config_value))
    lp = SimpleNamespace(get=AsyncMock(return_value=language_pack_value))
    return OosTemplateResolver(config_service=cfg, language_pack_service=lp)


@pytest.mark.asyncio
async def test_tier1_bot_column_wins_over_everything() -> None:
    """bots.oos_answer_template (tier 1) wins even when later tiers populated."""
    resolver = _resolver(
        system_config_value="from_system_config",
        language_pack_value="from_language_pack",
    )
    bot = SimpleNamespace(
        oos_answer_template="owner_custom_text",
        plan_limits={"oos_answer_template": "from_plan_limits"},
    )
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "owner_custom_text"


@pytest.mark.asyncio
async def test_tier2_plan_limits_used_when_column_empty() -> None:
    """bots.plan_limits[oos_answer_template] (tier 2) used when column empty."""
    resolver = _resolver(
        system_config_value="from_system_config",
        language_pack_value="from_language_pack",
    )
    bot = SimpleNamespace(
        oos_answer_template="",
        plan_limits={"oos_answer_template": "from_plan_limits"},
    )
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "from_plan_limits"


@pytest.mark.asyncio
async def test_tier5_system_config_used_when_bot_empty() -> None:
    """system_config (tier 5) wins when bot column + plan_limits empty."""
    resolver = _resolver(
        system_config_value="platform_default",
        language_pack_value="from_language_pack",
    )
    bot = SimpleNamespace(oos_answer_template=None, plan_limits={})
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "platform_default"


@pytest.mark.asyncio
async def test_tier6_language_pack_used_when_above_empty() -> None:
    """language_packs[code][refuse_message] (tier 6) is the last fallback above constants."""
    resolver = _resolver(
        system_config_value=None,
        language_pack_value="vi_locale_refuse",
    )
    bot = SimpleNamespace(oos_answer_template=None, plan_limits=None)
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "vi_locale_refuse"


@pytest.mark.asyncio
async def test_tier7_empty_when_all_tiers_miss() -> None:
    """Constants tier returns DEFAULT_OOS_ANSWER_TEMPLATE = '' (sacred default)."""
    resolver = _resolver(system_config_value=None, language_pack_value=None)
    bot = SimpleNamespace(oos_answer_template=None, plan_limits=None)
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == ""


@pytest.mark.asyncio
async def test_bot_name_substitution_applied_to_resolved_template() -> None:
    """{bot_name} placeholder substituted when caller supplies a value."""
    resolver = _resolver()
    bot = SimpleNamespace(
        oos_answer_template="Bot {bot_name} doesn't have that info.",
        plan_limits=None,
    )
    out = await resolver.resolve(
        bot=bot, language="en", bot_name_substitution="Acme",
    )
    assert out == "Bot Acme doesn't have that info."


@pytest.mark.asyncio
async def test_bot_name_placeholder_left_blank_when_substitution_missing() -> None:
    """Missing substitution leaves the placeholder as empty string (backward-compat)."""
    resolver = _resolver()
    bot = SimpleNamespace(
        oos_answer_template="Hello from {bot_name}!",
        plan_limits=None,
    )
    out = await resolver.resolve(bot=bot, language="en")
    assert out == "Hello from !"


@pytest.mark.asyncio
async def test_language_defaults_to_DEFAULT_LANGUAGE_when_none() -> None:
    """Passing language=None resolves to DEFAULT_LANGUAGE locale."""
    cfg = SimpleNamespace(get=AsyncMock(return_value=None))
    captured = {}

    async def fake_get(locale: str, prompt_key: str) -> str:
        captured["locale"] = locale
        captured["key"] = prompt_key
        return "vi_pack"

    lp = SimpleNamespace(get=fake_get)
    resolver = OosTemplateResolver(config_service=cfg, language_pack_service=lp)
    bot = SimpleNamespace(oos_answer_template=None, plan_limits=None)
    out = await resolver.resolve(bot=bot, language=None)
    assert out == "vi_pack"
    assert captured["locale"] == "vi"  # DEFAULT_LANGUAGE
    assert captured["key"] == "refuse_message"


@pytest.mark.asyncio
async def test_dict_bot_falls_back_via_dict_access() -> None:
    """Resolver tolerates dict-shaped bot row (legacy fixture style)."""
    resolver = _resolver()
    bot = {
        "oos_answer_template": "",
        "plan_limits": {"oos_answer_template": "dict_path"},
    }
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "dict_path"


@pytest.mark.asyncio
async def test_system_config_failure_degrades_to_next_tier() -> None:
    """system_config port error must NOT break the chain (graceful degrade)."""
    cfg = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("redis down")))
    lp = SimpleNamespace(get=AsyncMock(return_value="locale_fallback"))
    resolver = OosTemplateResolver(config_service=cfg, language_pack_service=lp)
    bot = SimpleNamespace(oos_answer_template=None, plan_limits=None)
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "locale_fallback"


@pytest.mark.asyncio
async def test_language_pack_failure_degrades_to_constants_empty() -> None:
    """When all DB tiers fail, em-of-line constants tier returns ''."""
    cfg = SimpleNamespace(get=AsyncMock(return_value=None))
    lp = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("db down")))
    resolver = OosTemplateResolver(config_service=cfg, language_pack_service=lp)
    bot = SimpleNamespace(oos_answer_template=None, plan_limits=None)
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == ""


@pytest.mark.asyncio
async def test_non_string_jsonb_values_treated_as_missing() -> None:
    """JSONB drift (number / dict / list) does NOT crash; treated as missing."""
    resolver = _resolver(
        system_config_value={"unexpected": "dict"},  # drift sentinel
        language_pack_value="locale_default",
    )
    bot = SimpleNamespace(oos_answer_template=None, plan_limits=None)
    out = await resolver.resolve(bot=bot, language="vi")
    assert out == "locale_default"
