"""Pin tests for ``SysPromptAssembler`` (ADR-W1-S10 condition guard).

The governed-exception ruling for the platform-rule append (sacred #10)
stands ONLY while these invariants hold:

1. Owner content is ALWAYS the prefix — platform rules append strictly
   AFTER ``bot.system_prompt``, never prepend, never interleave.
2. Any port failure degrades to the owner prompt unchanged.
3. Empty platform rules ⇒ owner prompt unchanged (no separator junk).
4. Per-bot opt-out strips exactly the disabled rule blocks.

Breaking any of these flips the ruling to VIOLATION (see
``program/decisions/ADR-W1-S10-sysprompt-append-adjudication.md``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ragbot.application.services.sysprompt_assembler import SysPromptAssembler

_RULES = (
    "\n\n15. ⭐ SYNTHESIS_COMPLETE\nLuôn tổng hợp đủ các ý."
    "\n\n16. ⭐ COMPARISON_VERDICT\nSo sánh phải có kết luận."
)


def _bot(**overrides):
    base = dict(system_prompt="OWNER PROMPT", language="vi", plan_limits={})
    base.update(overrides)
    return SimpleNamespace(**base)


def _assembler(rules: str | None = _RULES, *, raises: bool = False):
    svc = AsyncMock()
    if raises:
        svc.get.side_effect = RuntimeError("language_packs unavailable")
    else:
        svc.get.return_value = rules
    return SysPromptAssembler(language_pack_service=svc)


def test_owner_prompt_is_always_the_prefix():
    out = asyncio.run(_assembler().assemble(bot=_bot()))
    assert out.startswith("OWNER PROMPT"), (
        "platform rules must APPEND after owner content — owner prompt is "
        "the single authoritative prefix (sacred #10 condition 1)"
    )
    assert out == "OWNER PROMPT" + _RULES


def test_port_failure_degrades_to_owner_prompt_unchanged():
    out = asyncio.run(_assembler(raises=True).assemble(bot=_bot()))
    assert out == "OWNER PROMPT"


def test_empty_platform_rules_returns_owner_prompt_unchanged():
    out = asyncio.run(_assembler(rules="").assemble(bot=_bot()))
    assert out == "OWNER PROMPT"


def test_opt_out_strips_disabled_rule_block():
    bot = _bot(plan_limits={"sysprompt_rules_disabled": ["rule_15"]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert "SYNTHESIS_COMPLETE" not in out
    assert "COMPARISON_VERDICT" in out
    assert out.startswith("OWNER PROMPT")


def test_all_rules_disabled_returns_owner_prompt():
    bot = _bot(plan_limits={"sysprompt_rules_disabled": [15, 16]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert out == "OWNER PROMPT"
