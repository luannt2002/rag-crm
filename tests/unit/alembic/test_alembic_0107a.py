"""Static-analysis regression for alembic 0107a — composite ix_doc_bot_state.

Mega-sprint G16 fix: ``documents`` shows seq_scan rate 54% on the
coder-dev DB. The hot retrieve-time predicate is::

    WHERE record_bot_id = :bot_id AND state = 'active'

Two single-column indexes (``ix_doc_bot``, ``ix_doc_state``) are present
but the planner can use only one and must filter the other in-memory; a
composite index on ``(record_bot_id, state)`` lets a single index lookup
satisfy both equality predicates.

The migration must:
1. Be revision ``0107a`` chained off ``0106``.
2. Defensively DROP the composite if it already exists.
3. CREATE composite over ``(record_bot_id, state)`` in that order
   (``record_bot_id`` is the higher-cardinality key — leftmost for
   B-tree prefix scans).
4. NOT touch the existing ``ix_doc_bot`` / ``ix_doc_state`` single-column
   indexes (they remain available; downgrade is a clean drop of the
   composite alone).
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "alembic/versions/20260516_0107a_composite_doc_bot_state.py"


def _src() -> str:
    return _MIGRATION.read_text()


def test_migration_file_exists() -> None:
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_revision_chain_is_0107a_off_0106() -> None:
    src = _src()
    assert 'revision = "0107a"' in src
    assert 'down_revision = "0106"' in src


def test_upgrade_creates_composite_in_correct_column_order() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    assert "CREATE INDEX ix_doc_bot_state ON documents(record_bot_id, state)" in upgrade_block


def test_upgrade_drops_existing_composite_first() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    drop_pos = upgrade_block.index("DROP INDEX IF EXISTS ix_doc_bot_state")
    create_pos = upgrade_block.index("CREATE INDEX ix_doc_bot_state")
    assert drop_pos < create_pos


def test_upgrade_does_not_touch_single_column_indexes() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    # Must not drop the single-column siblings.
    assert "DROP INDEX IF EXISTS ix_doc_bot" not in upgrade_block.replace("ix_doc_bot_state", "")
    assert "DROP INDEX IF EXISTS ix_doc_state" not in upgrade_block


def test_downgrade_drops_composite_only() -> None:
    src = _src()
    downgrade_block = src.split("_DOWNGRADE_SQL")[1]
    assert "DROP INDEX IF EXISTS ix_doc_bot_state" in downgrade_block
    # Doesn't recreate single-column siblings (they were never dropped).
    assert "ix_doc_bot " not in downgrade_block
