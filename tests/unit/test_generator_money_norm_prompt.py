"""Pin tests — 260525 P1c alembic 0114 generator money-norm rule.

Bug evidence (live test 2026-05-25 22:32, request 4e722794): K1 query
"1tr499 có những dịch vụ nào" returned "Bikini 499.000đ"
(citations_extract n_valid=1) when DB had 6 ground-truth chunks
containing 1499000 across 2 docs.

Root cause: alembic 0112 aggregation rule lacks (a) VN money shorthand
normalisation ("1tr499" → 1499000) and (b) explicit "scan all columns
in multi-price table chunks" enforcement.

These pin tests guard the alembic 0114 prompt content so the rule is
not silently dropped by a later migration that rewrites the generator
prompt.
"""

from __future__ import annotations

from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260525_0114_generator_money_norm.py"
)


def _read_migration() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists() -> None:
    """Alembic 0114 file must be present and revises 0113."""
    src = _read_migration()
    assert 'revision: str = "0114"' in src
    assert 'down_revision: str | None = "0113"' in src


def test_vi_new_content_has_money_normalize_rule() -> None:
    """vi generator prompt rule 6 names the VN shorthand pattern.

    Without "1tr499" / "Xtr" / "Xk" tokens, the LLM has no anchor to
    decode the user's question into a full integer amount and falls
    back to ambiguous matching (the original Bikini=499 bug).
    """
    src = _read_migration()
    # Body lives between _VI_NEW_CONTENT and _EN_OLD_CONTENT.
    vi_start = src.index('_VI_NEW_CONTENT = """') + len('_VI_NEW_CONTENT = """')
    vi_end = src.index('"""', vi_start)
    vi_body = src[vi_start:vi_end]

    assert "QUY TẮC SỐ TIỀN VIỆT NAM" in vi_body
    assert "1tr499" in vi_body
    assert "1.499.000" in vi_body or "1499000" in vi_body
    assert "Xtr" in vi_body
    assert "Xk" in vi_body
    # Scan-all-columns + enumerate-all-rows enforcement.
    assert "scan TẤT CẢ cột" in vi_body or "TẤT CẢ cột" in vi_body
    assert "LIỆT KÊ TẤT CẢ row" in vi_body or "không dừng" in vi_body.lower() or "KHÔNG dừng" in vi_body


def test_en_new_content_has_money_normalize_rule() -> None:
    """en generator prompt rule 6 mirrors the vi rule."""
    src = _read_migration()
    en_start = src.index('_EN_NEW_CONTENT = """') + len('_EN_NEW_CONTENT = """')
    en_end = src.index('"""', en_start)
    en_body = src[en_start:en_end]

    assert "VIETNAMESE MONEY NORMALIZATION" in en_body
    assert "1tr499" in en_body
    assert "1,499,000" in en_body or "1499000" in en_body
    assert "scan ALL columns" in en_body
    assert "LIST ALL matching rows" in en_body


def test_downgrade_restores_p1a_5_rule_content() -> None:
    """downgrade() must restore alembic 0112 (P1a) 5-rule content, NOT
    leave a 6-rule prompt that survives the rollback.
    """
    src = _read_migration()
    vi_old_start = src.index('_VI_OLD_CONTENT = """') + len('_VI_OLD_CONTENT = """')
    vi_old_end = src.index('"""', vi_old_start)
    vi_old_body = src[vi_old_start:vi_old_end]

    # The 5-rule body must NOT contain the new rule 6 markers.
    assert "QUY TẮC SỐ TIỀN VIỆT NAM" not in vi_old_body
    # But it MUST still contain the rules 1-5 markers from alembic 0112.
    assert "QUY TẮC TRẢ LỜI THEO LOẠI CÂU HỎI" in vi_old_body
    assert "intent = aggregation" in vi_old_body
    assert "intent = factoid" in vi_old_body


def test_upgrade_uses_idempotent_update_where_pattern() -> None:
    """The migration must use UPDATE WHERE (code, prompt_key) so a
    re-run is a no-op (idempotent). Also bumps version + updated_at."""
    src = _read_migration()
    assert "WHERE code = 'vi' AND prompt_key = 'generator'" in src
    assert "WHERE code = 'en' AND prompt_key = 'generator'" in src
    assert "version = version + 1" in src
    assert "updated_at = NOW()" in src
