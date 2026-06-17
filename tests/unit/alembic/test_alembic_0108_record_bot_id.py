"""Static lock test — alembic 0108 denormalize record_bot_id (MEGA-1 G14).

Live evidence: ``ix_chunks_embedding_hnsw idx_scan = 0`` over a 22 MB
index. Root cause: ``document_chunks`` had no ``record_bot_id`` column,
so the bot-isolation predicate sat behind a ``documents`` JOIN and the
planner could not push it into the HNSW operator.

This test asserts the migration script's content guarantees the fix:
- Column added + NOT NULL after back-fill.
- FK to ``bots(id) ON DELETE CASCADE`` (mirrors document cascade).
- Two indexes (single-col + composite) so the planner has both
  bot-only and bot-doc lookup paths.
- RLS policy refactored to filter on the new local column instead of
  EXISTS-over-documents.
- Reversible downgrade (drops policy → indexes → FK → column).
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "alembic/versions/20260516_0108_chunks_record_bot_id.py"


def _src() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists() -> None:
    assert _MIGRATION.exists(), f"missing migration: {_MIGRATION}"


def test_revision_metadata() -> None:
    src = _src()
    assert 'revision = "0108"' in src
    assert 'down_revision = "0107b"' in src


def test_upgrade_adds_column_then_backfills_then_sets_not_null() -> None:
    """Order matters: ADD COLUMN nullable → UPDATE back-fill → SET NOT NULL.

    Reversing this order would crash on the constraint promotion since
    pre-existing rows would still hold NULL.
    """
    src = _src()
    add_idx = src.find("ADD COLUMN record_bot_id UUID")
    update_idx = src.find("UPDATE document_chunks dc")
    notnull_idx = src.find("SET NOT NULL")
    assert add_idx != -1, "ADD COLUMN missing"
    assert update_idx != -1, "back-fill UPDATE missing"
    assert notnull_idx != -1, "SET NOT NULL missing"
    assert add_idx < update_idx < notnull_idx, (
        "Migration order broken: must add nullable column → backfill → promote NOT NULL"
    )


def test_upgrade_creates_fk_with_cascade() -> None:
    src = _src()
    assert "fk_chunks_bot" in src
    assert "REFERENCES bots(id) ON DELETE CASCADE" in src


def test_upgrade_creates_both_indexes() -> None:
    src = _src()
    # Single-column for the hot-path bot filter that lets HNSW activate.
    assert "CREATE INDEX ix_chunks_bot ON document_chunks(record_bot_id)" in src
    # Composite for bot-scoped chunk-by-document scans (rechunk, delete-by-doc).
    assert "ix_chunks_bot_doc" in src
    assert "(record_bot_id, record_document_id)" in src


def test_upgrade_refactors_rls_policy_to_local_column() -> None:
    """Policy must filter on the new local ``record_bot_id`` (lookup via
    the new index), NOT via EXISTS-over-documents."""
    src = _src()
    assert "DROP POLICY IF EXISTS tenant_isolation ON document_chunks" in src
    assert "CREATE POLICY tenant_isolation ON document_chunks" in src
    # Local-column path
    assert "record_bot_id IN (" in src
    assert "FROM bots" in src
    assert "current_setting('app.tenant_id', true)" in src


def test_downgrade_reverses_in_correct_order() -> None:
    """Downgrade must drop in reverse dependency order:
    policy → composite idx → single-col idx → FK → column."""
    src = _src()
    down_start = src.find("def downgrade() -> None:")
    assert down_start != -1
    down = src[down_start:]
    pol = down.find("DROP POLICY IF EXISTS tenant_isolation")
    cidx = down.find("DROP INDEX IF EXISTS ix_chunks_bot_doc")
    # Anchor on the closing paren / quote so the substring match for the
    # composite index name (``ix_chunks_bot``) does not collide with
    # ``ix_chunks_bot_doc`` which is dropped earlier.
    sidx = down.find('DROP INDEX IF EXISTS ix_chunks_bot"')
    fk = down.find("DROP CONSTRAINT IF EXISTS fk_chunks_bot")
    col = down.find("DROP COLUMN IF EXISTS record_bot_id")
    assert -1 not in {pol, cidx, sidx, fk, col}, (
        f"missing downgrade step: pol={pol} cidx={cidx} sidx={sidx} fk={fk} col={col}"
    )
    assert pol < cidx < sidx < fk < col, (
        "Downgrade order broken — must drop dependents before parents"
    )


def test_no_hardcoded_tenant_or_bot_literal() -> None:
    """Domain-neutral: migration must not pin a specific tenant/bot UUID
    or brand name (CLAUDE.md domain-neutral rule)."""
    src = _src()
    # Allow only the orphan-bot reset UUID owned by Coder-C2 (G12).
    forbidden_substrings = ("vietcombank", "medispa", "Dr.", "tt-nhnn")
    for needle in forbidden_substrings:
        assert needle.lower() not in src.lower(), (
            f"domain-neutral violation: {needle!r} in migration"
        )


def test_migration_motivation_documented() -> None:
    """Live evidence (idx_scan=0 / 22MB) must be cited so a future reader
    understands why we denormalized."""
    src = _src()
    assert "MEGA-1" in src
    assert "HNSW" in src or "hnsw" in src.lower()
    assert "idx_scan" in src
