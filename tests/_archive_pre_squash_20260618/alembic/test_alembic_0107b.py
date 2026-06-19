"""Static-analysis regression for alembic 0107b — drop duplicate ix_semantic_cache_bot.

Mega-sprint G17 fix: ``ix_semantic_cache_bot`` indexes ``(record_bot_id)``
alone but two wider composite indexes on ``semantic_cache`` already lead
with ``record_bot_id`` — the standalone index is dead weight that costs
write throughput and disk on every INSERT.

The migration must:
1. Be revision ``0107b`` chained off ``0107a``.
2. DROP the redundant index in upgrade.
3. ``downgrade`` defensively recreates the index for full reversibility
   (the recreated index will continue to be a no-op for query planning).
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "alembic/versions/20260516_0107b_drop_duplicate_index.py"


def _src() -> str:
    return _MIGRATION.read_text()


def test_migration_file_exists() -> None:
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_revision_chain_is_0107b_off_0107a() -> None:
    src = _src()
    assert 'revision = "0107b"' in src
    assert 'down_revision = "0107a"' in src


def test_upgrade_drops_duplicate_index() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    assert "DROP INDEX IF EXISTS ix_semantic_cache_bot" in upgrade_block


def test_upgrade_does_not_create_replacement() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    assert "CREATE INDEX" not in upgrade_block


def test_downgrade_restores_index_for_reversibility() -> None:
    src = _src()
    downgrade_block = src.split("_DOWNGRADE_SQL")[1]
    assert "CREATE INDEX IF NOT EXISTS ix_semantic_cache_bot" in downgrade_block
    assert "ON semantic_cache(record_bot_id)" in downgrade_block
