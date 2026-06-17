"""Unit tests for SysPromptAssembler — multi-tenant platform-default rules.

Verifies the J1 ship pattern: bot owner's system_prompt stays
authoritative (rendered first), platform-default rules from
``language_packs[code].sysprompt_default_rules`` append after, and
per-bot opt-out via ``bots.plan_limits.sysprompt_rules_disabled`` JSONB
list strips matching rule blocks.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.sysprompt_assembler import SysPromptAssembler


_PLATFORM_RULES = """

15. ⭐ SYNTHESIS_COMPLETE — multipart question rule body.
   - Step A: list sub-intents.
   - Step B: address each.

16. ⭐ COMPARISON_VERDICT — explicit verdict rule body.
   - Step A: list entities.
   - Step B: conclude winner.

17. ⭐ ANTI_CSV_ROW_CONFLATE — CSV row binding rule body.
   - Row to entity strict binding.

18. ⭐ INLINE_SLOT_CAPTURE — slot recognition rule body.
   - Scan current turn for slots.

19. ⭐ STRICT_PROMO_BINDING — promo strict binding rule body.
   - Promo binds to entity in same chunk."""


def _assembler(*, platform_rules: str | None = _PLATFORM_RULES) -> SysPromptAssembler:
    """Build assembler with a stubbed language_pack_service."""
    lp = SimpleNamespace(get=AsyncMock(return_value=platform_rules))
    return SysPromptAssembler(language_pack_service=lp)


# --------------------------------------------------------------------------- #
# Owner content authoritative (tier 1)                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_owner_content_appears_first() -> None:
    bot = SimpleNamespace(system_prompt="OWNER_PROMPT", plan_limits={})
    out = await _assembler().assemble(bot=bot, language="vi")
    assert out.startswith("OWNER_PROMPT")
    assert "SYNTHESIS_COMPLETE" in out


@pytest.mark.asyncio
async def test_platform_rules_appended_after_owner() -> None:
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={})
    out = await _assembler().assemble(bot=bot, language="vi")
    # Owner content first, rules block after.
    assert out.index("OWNER") < out.index("15. ⭐ SYNTHESIS_COMPLETE")


# --------------------------------------------------------------------------- #
# Empty platform pack (tier 6 missing)                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_platform_returns_owner_unchanged() -> None:
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={})
    out = await _assembler(platform_rules="").assemble(bot=bot, language="vi")
    assert out == "OWNER"


@pytest.mark.asyncio
async def test_none_platform_returns_owner_unchanged() -> None:
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={})
    out = await _assembler(platform_rules=None).assemble(bot=bot, language="vi")
    assert out == "OWNER"


# --------------------------------------------------------------------------- #
# Per-bot opt-out via plan_limits.sysprompt_rules_disabled                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_owner_optout_one_rule_strips_only_that_rule() -> None:
    bot = SimpleNamespace(
        system_prompt="OWNER",
        plan_limits={"sysprompt_rules_disabled": ["rule_17"]},
    )
    out = await _assembler().assemble(bot=bot, language="vi")
    assert "ANTI_CSV_ROW_CONFLATE" not in out
    # Other rules remain.
    assert "SYNTHESIS_COMPLETE" in out
    assert "COMPARISON_VERDICT" in out
    assert "INLINE_SLOT_CAPTURE" in out
    assert "STRICT_PROMO_BINDING" in out


@pytest.mark.asyncio
async def test_owner_optout_multiple_rules() -> None:
    bot = SimpleNamespace(
        system_prompt="OWNER",
        plan_limits={"sysprompt_rules_disabled": ["rule_17", "rule_19"]},
    )
    out = await _assembler().assemble(bot=bot, language="vi")
    assert "ANTI_CSV_ROW_CONFLATE" not in out
    assert "STRICT_PROMO_BINDING" not in out
    # Other rules remain.
    assert "SYNTHESIS_COMPLETE" in out
    assert "COMPARISON_VERDICT" in out
    assert "INLINE_SLOT_CAPTURE" in out


@pytest.mark.asyncio
async def test_owner_optout_numeric_form_accepted() -> None:
    """Owner can specify rule IDs as 17 (int), '17' (str), 'rule_17', etc."""
    bot = SimpleNamespace(
        system_prompt="OWNER",
        plan_limits={"sysprompt_rules_disabled": [17, "19", "rule_15"]},
    )
    out = await _assembler().assemble(bot=bot, language="vi")
    assert "ANTI_CSV_ROW_CONFLATE" not in out
    assert "STRICT_PROMO_BINDING" not in out
    assert "SYNTHESIS_COMPLETE" not in out
    # Rules not in opt-out list remain.
    assert "COMPARISON_VERDICT" in out
    assert "INLINE_SLOT_CAPTURE" in out


# --------------------------------------------------------------------------- #
# Language resolution                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_explicit_language_overrides_bot_field() -> None:
    captured = {}

    async def _get(code, key):
        captured["code"] = code
        captured["key"] = key
        return _PLATFORM_RULES

    lp = SimpleNamespace(get=_get)
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={}, language="ja")
    asm = SysPromptAssembler(language_pack_service=lp)
    await asm.assemble(bot=bot, language="en")
    assert captured["code"] == "en"
    assert captured["key"] == "sysprompt_default_rules"


@pytest.mark.asyncio
async def test_bot_language_used_when_explicit_missing() -> None:
    captured = {}

    async def _get(code, key):
        captured["code"] = code
        return _PLATFORM_RULES

    lp = SimpleNamespace(get=_get)
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={}, language="en")
    asm = SysPromptAssembler(language_pack_service=lp)
    await asm.assemble(bot=bot, language=None)
    assert captured["code"] == "en"


@pytest.mark.asyncio
async def test_default_language_used_when_both_missing() -> None:
    captured = {}

    async def _get(code, key):
        captured["code"] = code
        return _PLATFORM_RULES

    lp = SimpleNamespace(get=_get)
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={})
    asm = SysPromptAssembler(language_pack_service=lp)
    await asm.assemble(bot=bot)
    assert captured["code"] == "vi"  # DEFAULT_LANGUAGE


# --------------------------------------------------------------------------- #
# Graceful degrade                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_port_failure_returns_owner_only() -> None:
    lp = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("db down")))
    asm = SysPromptAssembler(language_pack_service=lp)
    bot = SimpleNamespace(system_prompt="OWNER", plan_limits={})
    out = await asm.assemble(bot=bot, language="vi")
    assert out == "OWNER"


@pytest.mark.asyncio
async def test_dict_bot_supported() -> None:
    """Bot config as dict (legacy fixture / SimpleNamespace alternative)."""
    bot = {
        "system_prompt": "OWNER_DICT",
        "plan_limits": {"sysprompt_rules_disabled": ["rule_17"]},
    }
    out = await _assembler().assemble(bot=bot, language="vi")
    assert out.startswith("OWNER_DICT")
    assert "ANTI_CSV_ROW_CONFLATE" not in out


@pytest.mark.asyncio
async def test_none_bot_returns_just_platform_rules() -> None:
    """None bot edge case — should not crash."""
    out = await _assembler().assemble(bot=None, language="vi")
    assert "SYNTHESIS_COMPLETE" in out


@pytest.mark.asyncio
async def test_all_rules_disabled_returns_owner_only() -> None:
    """If owner disables every rule, output is owner content alone."""
    bot = SimpleNamespace(
        system_prompt="OWNER",
        plan_limits={
            "sysprompt_rules_disabled": [
                "rule_15", "rule_16", "rule_17", "rule_18", "rule_19",
            ],
        },
    )
    out = await _assembler().assemble(bot=bot, language="vi")
    assert out == "OWNER"
