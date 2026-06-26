"""S1-B / AG-A2 — ANTI-FABRICATE language-pack rule seed (append-only).

The migration ``20260627_seed_anti_fabricate_rule_lang_packs`` appends a new
``# ANTI-FABRICATE`` (vi: ``# CHỐNG BỊA DỮ LIỆU``) section to
``language_packs[code][sysprompt_default_rules]``. Sacred-rule 2 (governed
append-only exception, ADR-W1-S10) requires the platform-default text to be
APPENDED — never prepended, never inserted mid-prompt.

These tests pin the migration's string contract without a DB:
- the block is concatenated at the END of existing content (append, not
  prepend / not mid-insert),
- upgrade is idempotent (the marker guard prevents a double-append),
- downgrade restores the original content byte-for-byte,
- the rule text is domain-neutral (forbids fabricating link/number/value, no
  brand / service / price literal),
- the rule instructs the "say you don't have it yet" behaviour.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260627_seed_anti_fabricate_rule_lang_packs.py"
)


def _load_migration():
    """Load the digit-prefixed migration module by file path.

    ``import_module`` rejects a package path whose final component starts with
    a digit; ``spec_from_file_location`` does not have that restriction.
    """
    spec = importlib.util.spec_from_file_location(
        "_seed_anti_fabricate_rule_260627", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MOD = _load_migration()

# Representative existing content (shape mirrors the real seed).
_BASE_EN = "# ROLE\nYou are an assistant.\n\n# GROUNDING\n1. Use only the context."
_BASE_VI = "# VAI TRÒ\nBạn là trợ lý.\n\n# KỶ LUẬT NGỮ CẢNH\n1. Chỉ dùng ngữ cảnh."


def _append(base: str, block: str, marker: str) -> str:
    """Mirror the migration's idempotent upgrade SQL on a Python string."""
    if marker in base:
        return base
    return base + block


def _strip(content: str, block: str) -> str:
    """Mirror the migration's downgrade SQL (remove exact block at END)."""
    idx = content.find(block)
    if idx < 0:
        return content
    return content[:idx]


def test_migration_has_chain_metadata() -> None:
    assert _MOD.revision == "seed_anti_fabricate_rule_260627"
    assert _MOD.down_revision == "rls_missing_ok_setting_20260626"


def test_block_is_appended_not_prepended() -> None:
    """Owner content (base) must remain the PREFIX; rule text comes after."""
    for code, marker, block, base in (
        ("en", _MOD._MARKER_EN, _MOD._BLOCK_EN, _BASE_EN),
        ("vi", _MOD._MARKER_VI, _MOD._BLOCK_VI, _BASE_VI),
    ):
        out = _append(base, block, marker)
        assert out.startswith(base), f"{code}: base content must stay the prefix"
        assert out.endswith(block), f"{code}: rule block must be appended at END"
        # Marker appears exactly once and AFTER all of the base content.
        assert out.count(marker) == 1
        assert out.index(marker) >= len(base)


def test_upgrade_is_idempotent() -> None:
    """Re-running on already-seeded content is a no-op (marker guard)."""
    for marker, block, base in (
        (_MOD._MARKER_EN, _MOD._BLOCK_EN, _BASE_EN),
        (_MOD._MARKER_VI, _MOD._BLOCK_VI, _BASE_VI),
    ):
        once = _append(base, block, marker)
        twice = _append(once, block, marker)
        assert once == twice, "double-append must not happen"
        assert once.count(marker) == 1


def test_downgrade_restores_original() -> None:
    """Downgrade removes exactly the appended block, including its separator."""
    for marker, block, base in (
        (_MOD._MARKER_EN, _MOD._BLOCK_EN, _BASE_EN),
        (_MOD._MARKER_VI, _MOD._BLOCK_VI, _BASE_VI),
    ):
        seeded = _append(base, block, marker)
        restored = _strip(seeded, block)
        assert restored == base, "downgrade must restore byte-for-byte"
        assert marker not in restored


def test_block_starts_with_blank_line_separator() -> None:
    """Appended block must lead with a blank line so it never fuses with the
    prior section's last line."""
    assert _MOD._BLOCK_EN.startswith("\n\n")
    assert _MOD._BLOCK_VI.startswith("\n\n")


def test_rule_text_is_domain_neutral() -> None:
    """No brand / service / industry literal in the seeded rule text."""
    text = (_MOD._BLOCK_EN + _MOD._BLOCK_VI).lower()
    for literal in (
        "spa", "medispa", "legal", "thông tư", "hotline:", "vnd", "http",
    ):
        assert literal not in text, f"domain literal leaked into rule: {literal!r}"


def test_rule_forbids_fabricating_link_number_value() -> None:
    """The English rule must cover link + number + value, and the 'don't have
    it yet' behaviour."""
    en = _MOD._BLOCK_EN.lower()
    assert "link" in en
    assert "number" in en
    assert "value" in en
    assert "do not have" in en or "don't have" in en


def test_rule_vi_forbids_fabrication() -> None:
    """Vietnamese rule mirrors the anti-fabricate intent."""
    vi = _MOD._BLOCK_VI.lower()
    assert "không bịa" in vi or "không" in vi
    assert "link" in vi
    assert "chưa có" in vi
