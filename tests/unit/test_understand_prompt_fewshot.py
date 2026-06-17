"""Pin test — understand_query prompt must include few-shot example block.

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 2.

The intent classifier prompt without concrete examples biases the LLM
toward ``factoid`` (the value used in the single template example).
Verified failure mode 2026-05-21: 0/9 turn-level classifications hit
``aggregation`` despite 3 turns being canonical aggregation queries.

This test reads the prompt from the live DB (post-migration) and
asserts the few-shot block is present + lists aggregation-pattern
examples that anchor "có mấy" / "có bao nhiêu" / "liệt kê" to
``aggregation``. Skipped when DATABASE_URL is not set so the test
suite remains green in isolated environments.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")),
    reason="DATABASE_URL[_SYNC] not set — DB-bound pin test skipped.",
)


def _read_language_pack(code: str, prompt_key: str) -> str:
    """Read language_packs.content directly via psycopg2 for verification."""
    import psycopg2
    raw = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL", "")
    # Strip async driver prefix if present.
    if "+" in raw.split("://", 1)[0]:
        scheme, rest = raw.split("://", 1)
        raw = scheme.split("+", 1)[0] + "://" + rest
    conn = psycopg2.connect(raw)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM language_packs WHERE code=%s AND prompt_key=%s",
                (code, prompt_key),
            )
            row = cur.fetchone()
            return row[0] if row else ""
    finally:
        conn.close()


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
