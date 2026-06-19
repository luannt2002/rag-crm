"""Pin test — understand_query prompt must include few-shot example block.

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 2.

The intent classifier prompt without concrete examples biases the LLM
toward ``factoid`` (the value used in the single template example).
Verified failure mode 2026-05-21: 0/9 turn-level classifications hit
``aggregation`` despite 3 turns being canonical aggregation queries.

This test asserts the few-shot block is present + lists aggregation-pattern
examples that anchor "có mấy" / "có bao nhiêu" / "liệt kê" to
``aggregation``. The canonical ``understand`` prompt content lives in the
alembic migration that seeds ``language_packs`` (010z — CLASSIFY-FIRST +
few-shot block, the last revision to rewrite this prompt body). After the
2026-06-18 migration squash that file was moved to
``alembic/_archive_pre_squash_20260618/`` and the schema-only squash
baseline no longer replays the data UPDATE, so the source-of-truth for the
prompt body is the archived migration's ``_VI_NEW_CONTENT`` /
``_EN_NEW_CONTENT`` constants. The test reads those constants directly,
keeping every content assertion meaningful and independent of ambient DB
seed state.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


# Last migration that rewrote the understand prompt body (010z); 0134 only
# REPLACEs the out_of_scope line and touches none of the asserted tokens.
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "_archive_pre_squash_20260618"
    / "20260525_010z_understand_preserve_aggregation.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "understand_preserve_aggregation", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_language_pack(code: str, prompt_key: str) -> str:
    """Return the canonical ``understand`` prompt content from the migration
    that seeds ``language_packs`` (source-of-truth post-squash)."""
    assert prompt_key == "understand", (
        f"only the understand prompt is pinned here, got {prompt_key!r}"
    )
    module = _load_migration_module()
    if code == "vi":
        return module._VI_NEW_CONTENT
    if code == "en":
        return module._EN_NEW_CONTENT
    return ""


def test_vi_understand_prompt_has_aggregation_fewshot_section() -> None:
    content = _read_language_pack("vi", "understand")
    assert content, "vi/understand row missing in language_packs"
    assert "VÍ DỤ PHÂN LOẠI" in content, (
        "Few-shot example section header missing — alembic 010w not applied."
    )


def test_vi_understand_prompt_aggregation_examples_present() -> None:
    content = _read_language_pack("vi", "understand")
    # At least 3 aggregation-pattern examples must be present.
    assert "Có mấy X" in content
    assert "Có bao nhiêu X" in content
    assert "Liệt kê tất cả X" in content
    # AGGREGATION section header arrow keyword.
    assert "→ aggregation" in content


def test_vi_understand_prompt_factoid_examples_disambiguate() -> None:
    """Factoid examples must be present so LLM disambiguates 'X là gì' vs
    'có mấy X'."""
    content = _read_language_pack("vi", "understand")
    assert "X là gì" in content
    assert "Giá X bao nhiêu" in content
    assert "→ factoid" in content


def test_en_understand_prompt_has_examples_section() -> None:
    content = _read_language_pack("en", "understand")
    assert content, "en/understand row missing"
    assert "EXAMPLES" in content
    assert "How many X" in content
    assert "List all X" in content
    assert "-> aggregation" in content or "→ aggregation" in content


def test_understand_prompt_domain_neutral_no_brand_leak() -> None:
    """Few-shot must use placeholder X/Y/Z — no brand / industry literal."""
    for code in ("vi", "en"):
        content = _read_language_pack(code, "understand")
        # Sanity: domain literals that would violate CLAUDE.md must NOT appear.
        forbidden = ["medispa", "innocom", "diode laser", "shopee", "lazada"]
        lc = content.lower()
        for word in forbidden:
            assert word not in lc, (
                f"Domain-neutral violation in {code}/understand: {word!r}"
            )


def test_understand_prompt_json_shape_preserved() -> None:
    """The closing ``Trả về JSON`` / ``Return JSON`` block must still exist
    so downstream JSON parser keeps working."""
    vi = _read_language_pack("vi", "understand")
    assert "Trả về JSON" in vi
    assert '"query":' in vi
    assert '"intent":' in vi

    en = _read_language_pack("en", "understand")
    assert "Return JSON" in en
    assert '"query":' in en
    assert '"intent":' in en
