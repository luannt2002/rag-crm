"""Static-analysis regression for alembic 0105 — semantic_cache dim 1024 -> 1280.

Mega-sprint G4 fix: ZeroEntropy ``zembed-1`` returns 1280-dim vectors;
the cache table column was provisioned ``vector(1024)`` so every INSERT
fails silently in the broad-except-wrapped writer, holding the cache
hit rate at 0%.

The migration must:
1. Be revision ``0105`` chained off ``0104`` (Coder-A1's preceding rev).
2. DROP the HNSW index before altering the column (HNSW indexes pin the
   column type and block ALTER).
3. DROP+ADD the column (no ``USING NULL`` cast — the pgvector type does
   not support a numeric reshape; table is empty so loss-less).
4. ADD with ``vector(1280)`` — the live ``zembed-1`` matryoshka width.
5. Recreate the HNSW index over the new column.
6. Provide a ``downgrade`` that restores the legacy 1024 shape.
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "alembic/versions/20260516_0105_fix_semantic_cache_dim.py"


def _src() -> str:
    return _MIGRATION.read_text()


def test_migration_file_exists() -> None:
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_revision_chain_is_0105_off_0104() -> None:
    src = _src()
    assert 'revision = "0105"' in src
    assert 'down_revision = "0104"' in src


def test_upgrade_drops_hnsw_index_first() -> None:
    src = _src()
    upgrade_idx = src.index("_UPGRADE_SQL")
    upgrade_block = src[upgrade_idx : src.index("_DOWNGRADE_SQL")]
    drop_idx = upgrade_block.index("DROP INDEX IF EXISTS ix_semantic_cache_qe_hnsw")
    drop_col_idx = upgrade_block.index("DROP COLUMN IF EXISTS query_embedding")
    assert drop_idx < drop_col_idx, "HNSW index must be dropped BEFORE the column"


def test_upgrade_recreates_column_at_dim_1280() -> None:
    src = _src()
    assert "ADD COLUMN query_embedding vector(1280)" in src


def test_upgrade_recreates_hnsw_index() -> None:
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    assert "CREATE INDEX ix_semantic_cache_qe_hnsw" in upgrade_block
    assert "USING hnsw (query_embedding vector_cosine_ops)" in upgrade_block


def test_downgrade_restores_prior_1024_dim() -> None:
    src = _src()
    downgrade_block = src.split("_DOWNGRADE_SQL")[1]
    assert "ADD COLUMN query_embedding vector(1024)" in downgrade_block


def test_no_using_null_cast() -> None:
    """``ALTER COLUMN ... USING NULL`` would silently wipe data; we use
    the explicit DROP + ADD pattern instead, predicated on the table
    being empty (verified pre-condition documented in the docstring)."""
    src = _src()
    # Strip the docstring (which mentions the pattern in prose) before
    # scanning for occurrences in executable code.
    import ast
    tree = ast.parse(src)
    docstring = ast.get_docstring(tree) or ""
    body_only = src.replace(docstring, "")
    assert "USING NULL" not in body_only


def test_idempotent_drop_clauses() -> None:
    src = _src()
    assert "DROP INDEX IF EXISTS ix_semantic_cache_qe_hnsw" in src
    assert "DROP COLUMN IF EXISTS query_embedding" in src


def test_hnsw_parameters_match_spec() -> None:
    """HNSW build parameters tuned for matryoshka 1280 throughput."""
    src = _src()
    upgrade_block = src.split("_DOWNGRADE_SQL")[0]
    assert "m = 32" in upgrade_block
    assert "ef_construction = 200" in upgrade_block
