"""Static-analysis regression for alembic 0106 — GIN on documents.metadata_json.

Mega-sprint G5 fix: migration 0044 attempted to create the GIN index but
silently no-op'd (suspected: the index existed in an invalid state from a
prior failed deploy and ``IF NOT EXISTS`` skipped the recreate). Live psql
shows the index missing on coder-dev, leaving every JSONB containment
filter (e.g. ``metadata_json @> '{"article_number": 38}'``) on a sequential
scan.

The migration must:
1. Be revision ``0106`` chained off ``0105``.
2. Defensively DROP the index before recreating, so a left-over invalid
   index from any prior failed deploy is forced to a known-good state.
3. CREATE the GIN index without ``IF NOT EXISTS`` so the recreate is
   guaranteed to run.
4. Provide a ``downgrade`` that drops the index.
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "alembic/versions/20260516_0106_add_gin_documents_metadata.py"


def _src() -> str:
    return _MIGRATION.read_text()


def test_migration_file_exists() -> None:
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_revision_chain_is_0106_off_0105() -> None:
    src = _src()
    assert 'revision = "0106"' in src
    assert 'down_revision = "0105"' in src


def test_upgrade_drops_then_recreates() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    drop_pos = upgrade_block.index("DROP INDEX IF EXISTS ix_documents_metadata_json_gin")
    create_pos = upgrade_block.index("CREATE INDEX ix_documents_metadata_json_gin")
    assert drop_pos < create_pos, "must DROP before CREATE for left-over-invalid recovery"


def test_upgrade_uses_gin_on_metadata_json() -> None:
    src = _src()
    assert "USING gin(metadata_json)" in src


def test_create_index_does_not_use_if_not_exists() -> None:
    """``IF NOT EXISTS`` would re-introduce the very bug this fixes —
    silently skipping when an invalid index already occupies the name."""
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    assert "CREATE INDEX IF NOT EXISTS ix_documents_metadata_json_gin" not in upgrade_block


def test_downgrade_drops_index() -> None:
    src = _src()
    downgrade_block = src.split("_DOWNGRADE_SQL")[1]
    assert "DROP INDEX IF EXISTS ix_documents_metadata_json_gin" in downgrade_block
