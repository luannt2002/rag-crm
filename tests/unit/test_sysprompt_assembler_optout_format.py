"""[Deep-debug GEN-F6] Per-bot opt-out must strip the REAL seeded rule format.

The live ``sysprompt_default_rules`` seed (alembic 20260627 / 20260701) stores
rule blocks in the markdown ``# HEADER`` form (``# ANTI-FABRICATE``,
``# CHỐNG BỊA DỮ LIỆU``, ...), NOT the legacy ``NN. ⭐ NAME`` form. The strip
regex previously matched only the legacy shape, so ``sysprompt_rules_disabled``
silently no-op'd for every production rule — breaking the sacred-exception's
per-bot opt-out condition. These tests pin the fix against the REAL format.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ragbot.application.services.sysprompt_assembler import SysPromptAssembler

# Production-shaped default rules — exactly the seed block format (leading blank
# line + markdown header), two English blocks + one Vietnamese block.
_PROD_RULES = (
    "\n\n# ANTI-FABRICATE\n"
    "Only state facts present in the context; never invent a number."
    "\n\n# ANTI-PAD-LIST\n"
    "List only items that appear explicitly; do not pad the list."
    "\n\n# CHỐNG BỊA DỮ LIỆU\n"
    "Chỉ nêu dữ kiện có trong ngữ cảnh; không bịa số."
)


def _bot(**overrides):
    base = dict(system_prompt="OWNER PROMPT", language="vi", plan_limits={})
    base.update(overrides)
    return SimpleNamespace(**base)


def _assembler(rules: str = _PROD_RULES):
    svc = AsyncMock()
    svc.get.return_value = rules
    return SysPromptAssembler(language_pack_service=svc)


def test_optout_by_header_name_strips_markdown_rule_block():
    """Disabling by the ``# HEADER`` name removes exactly that block."""
    bot = _bot(plan_limits={"sysprompt_rules_disabled": ["ANTI-FABRICATE"]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert out.startswith("OWNER PROMPT")
    assert "# ANTI-FABRICATE" not in out, "disabled block must be stripped"
    assert "Only state facts" not in out, "disabled block BODY must be stripped too"
    # untouched blocks remain
    assert "# ANTI-PAD-LIST" in out
    assert "# CHỐNG BỊA DỮ LIỆU" in out


def test_optout_name_matching_is_fold_insensitive():
    """``anti_pad_list`` / ``anti pad list`` fold to the ``# ANTI-PAD-LIST`` header."""
    bot = _bot(plan_limits={"sysprompt_rules_disabled": ["anti_pad_list"]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert "# ANTI-PAD-LIST" not in out
    assert "do not pad" not in out
    assert "# ANTI-FABRICATE" in out


def test_optout_vietnamese_header_name():
    """A locale-specific accented header is disable-able by its own text."""
    bot = _bot(plan_limits={"sysprompt_rules_disabled": ["CHỐNG BỊA DỮ LIỆU"]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert "# CHỐNG BỊA DỮ LIỆU" not in out
    assert "# ANTI-FABRICATE" in out
    assert "# ANTI-PAD-LIST" in out


def test_disable_all_markdown_rules_returns_owner_prompt():
    bot = _bot(plan_limits={"sysprompt_rules_disabled": [
        "ANTI-FABRICATE", "ANTI-PAD-LIST", "CHỐNG BỊA DỮ LIỆU",
    ]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert out == "OWNER PROMPT"


def test_unknown_disabled_name_is_noop_not_crash():
    """A disable entry that matches no block leaves all rules intact."""
    bot = _bot(plan_limits={"sysprompt_rules_disabled": ["NOT-A-REAL-RULE"]})
    out = asyncio.run(_assembler().assemble(bot=bot))
    assert "# ANTI-FABRICATE" in out
    assert "# ANTI-PAD-LIST" in out
    assert "# CHỐNG BỊA DỮ LIỆU" in out


def test_owner_content_with_own_markdown_header_is_never_stripped():
    """``_strip_rules`` runs on platform rules ONLY — owner headers stay intact
    even when the owner disables a platform rule of a similar name."""
    bot = _bot(
        system_prompt="OWNER PROMPT\n\n# ANTI-FABRICATE\nMy own owner section.",
        plan_limits={"sysprompt_rules_disabled": ["ANTI-FABRICATE"]},
    )
    out = asyncio.run(_assembler().assemble(bot=bot))
    # Owner's own header + body survive; only the PLATFORM rule block is stripped.
    assert "My own owner section." in out
    assert out.startswith("OWNER PROMPT")
    # The platform ANTI-FABRICATE body must be gone (its unique body text).
    assert "Only state facts present in the context" not in out
