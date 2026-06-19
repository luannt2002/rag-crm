"""Squashed baseline schema — replaces the pre-2026-06-18 incremental history.

The full public schema (tables, indexes, triggers, functions) lives in the
tracked SQL file ``alembic/squashed_baseline.sql`` (a cleaned ``pg_dump``
``--schema-only`` of the canonical schema). This migration installs the
required extensions and then executes that file, so a fresh database reaches
the complete schema in one step.

Existing databases already at the old head must be stamped to this revision:
``alembic stamp squash_base_20260618`` (no DDL replay).
"""
from pathlib import Path

from alembic import op

revision = "squash_base_20260618"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parent.parent / "squashed_baseline.sql"

_EXTENSIONS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
)


def upgrade() -> None:
    for ext in _EXTENSIONS:
        op.execute(ext)
    sql = _SQL_FILE.read_text(encoding="utf-8")
    # psycopg2 executes a multi-statement script in a single call; dollar-quoted
    # function bodies are preserved verbatim.
    op.execute(sql)


def downgrade() -> None:
    # Squashed baseline is the root; teardown drops the whole public schema.
    op.execute("DROP SCHEMA public CASCADE")
    op.execute("CREATE SCHEMA public")
