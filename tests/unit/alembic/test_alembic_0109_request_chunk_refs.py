"""G15 — alembic 0109 reversibility + shape assertions (offline).

Live Postgres apply happens via ``alembic upgrade head`` once the missing
intermediate revisions (0104..0108 from sibling coder branches A3 / C1)
are merged. Until then we pin the migration's STATIC contract:

* down_revision == "0108" (so C1 must merge before C3 can attach)
* upgrade() ends with DROP COLUMN retrieved_chunks
* downgrade() rebuilds the column AND back-fills it from the new table
  (otherwise rollback loses live evidence)
* both operations are wrapped in op.execute(text(...)) -- the project's
  alembic style avoids op.create_table for raw-DDL clarity.

Static parsing is sufficient here: a runtime alembic upgrade requires
every ancestor to exist (0104..0108 don't yet on this branch) and a
live Postgres connection -- neither is available in unit-test scope.
The runtime apply is documented in REPORT_*.md as the deploy gate.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "20260516_0109_request_chunk_refs.py"
)


@pytest.fixture(scope="module")
def migration_module():
    spec = importlib.util.spec_from_file_location(
        "_alembic_0109", MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_file_exists() -> None:
    assert MIGRATION_PATH.is_file(), MIGRATION_PATH


def test_revision_chain_attaches_to_0108(migration_module) -> None:
    """C3 split MUST land on top of C1's record_bot_id denormalize.

    If we accidentally retarget down_revision (e.g. main HEAD), the
    migration would skip required intermediate schema changes.
    """
    assert migration_module.revision == "0109"
    assert migration_module.down_revision == "0108"


def test_upgrade_drops_jsonb_column_after_table_create(migration_module) -> None:
    """upgrade() MUST create the new table BEFORE dropping the JSONB column.

    Order matters: if we DROP first and the migrate-data INSERT fails,
    we lose live evidence with no rollback path.
    """
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    # CREATE TABLE before INSERT before DROP COLUMN.
    create_pos = src.index("CREATE TABLE request_chunk_refs")
    insert_pos = src.index("INSERT INTO request_chunk_refs")
    drop_pos = src.index("DROP COLUMN IF EXISTS retrieved_chunks")
    assert create_pos < insert_pos < drop_pos


def test_upgrade_creates_indexes_for_both_fk_columns(migration_module) -> None:
    """Both FK columns need their own index (JOIN-back analytics)."""
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "CREATE INDEX ix_rcr_request" in src
    assert "CREATE INDEX ix_rcr_chunk" in src


def test_upgrade_uses_fk_cascade_on_both_sides(migration_module) -> None:
    """ON DELETE CASCADE on (request_logs, document_chunks) -- no dangling refs."""
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    # The CREATE TABLE block contains both FK CASCADE clauses.
    create_block = src[
        src.index("CREATE TABLE request_chunk_refs") :
        src.index("CREATE INDEX ix_rcr_request")
    ]
    assert re.search(
        r"FOREIGN KEY \(record_request_id\)\s+REFERENCES request_logs\(request_id\) ON DELETE CASCADE",
        create_block,
    )
    assert re.search(
        r"FOREIGN KEY \(record_chunk_id\)\s+REFERENCES document_chunks\(id\) ON DELETE CASCADE",
        create_block,
    )


def test_downgrade_reverses_upgrade_in_lifo_order(migration_module) -> None:
    """downgrade() MUST: ADD COLUMN -> rebuild JSONB -> DROP TABLE.

    Reverse order would either lose data (DROP TABLE first) or fail (rebuild
    references missing column).
    """
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    add_col_pos = src.index("ADD COLUMN retrieved_chunks JSONB")
    rebuild_pos = src.index("SELECT jsonb_agg(")
    drop_table_pos = src.index("DROP TABLE IF EXISTS request_chunk_refs")
    assert add_col_pos < rebuild_pos < drop_table_pos


def test_downgrade_rebuilds_jsonb_with_chunk_id_field(migration_module) -> None:
    """The reversed payload must include ``chunk_id`` so a rolled-back
    deployment's existing readers (e.g. test_chat preview projection)
    keep working.
    """
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    # jsonb_build_object embeds 'chunk_id' as text key.
    assert "'chunk_id', rcr.record_chunk_id::text" in src


def test_data_migrate_skips_non_uuid_chunk_refs(migration_module) -> None:
    """Live retrieved_chunks JSONB carries forensic-only entries with no
    chunk_id (legacy callers passed only chunk_index + preview). The data
    migrate MUST skip those rows so the new FK constraint doesn't reject
    the INSERT and abort the whole upgrade.
    """
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    # UUID regex guard + EXISTS document_chunks check both required.
    assert "~* '^[0-9a-f]{8}-[0-9a-f]{4}-" in src
    assert "EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.id = pick.cid::uuid)" in src


def test_module_exposes_callable_upgrade_downgrade(migration_module) -> None:
    """Alembic discovers ``upgrade`` / ``downgrade`` as module-level callables."""
    assert callable(migration_module.upgrade)
    assert callable(migration_module.downgrade)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
