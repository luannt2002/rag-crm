"""Sprint 10 Tier-IQ #4 (Gap B.8): GIN index on metadata_json columns.

Backs the read-side of metadata-aware retrieval. Queries shaped like

    WHERE metadata_json @> '{"doc_type":"pricing"}'::jsonb

need a GIN index with the ``jsonb_path_ops`` operator class to be
sub-linear; without it Postgres falls back to a sequential scan.

We index BOTH ``documents`` and ``document_chunks``:
* ``documents`` — used by the bot-scope ``_doc_filter_sql`` subquery
  and any future per-doc metadata cuts.
* ``document_chunks`` — used directly by ``hybrid_search`` when the
  intent extractor surfaces a chunk-level facet (e.g. table type).

CONCURRENTLY caveat
-------------------
Alembic wraps each migration in a transaction. ``CREATE INDEX
CONCURRENTLY`` is **not** allowed inside a transaction, so we **cannot**
use it here without ``op.get_context().autocommit_block()``. The
``IF NOT EXISTS`` form is safe + idempotent and the dev/staging
boxes have small tables, so we run plain ``CREATE INDEX`` here.

For prod replays where the table is large enough that an exclusive
lock matters, operators should:

    1. ``alembic stamp 0044`` (skip the in-tx DDL)
    2. ``CREATE INDEX CONCURRENTLY ix_documents_metadata_json_gin
            ON documents USING gin (metadata_json jsonb_path_ops);``
    3. Same for ``document_chunks``.

The two indexes are deterministic so stamping after manual creation is
equivalent to running this migration.

Defensive: we verify each ``metadata_json`` column exists before
issuing the CREATE INDEX so a fresh schema (chunks table without the
column) doesn't fail the migration.

Revision: 0044
Down revision: 0043
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ).bindparams(t=table, c=column)
    )
    return bool(result.scalar())


def upgrade() -> None:
    """Create GIN ``jsonb_path_ops`` indexes on ``metadata_json``.

    Defensive: skip table whose ``metadata_json`` column is missing
    so the migration is forward-compatible with partial schemas.
    """
    if _column_exists("documents", "metadata_json"):
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_documents_metadata_json_gin "
            "ON documents USING gin (metadata_json jsonb_path_ops)"
        )
    if _column_exists("document_chunks", "metadata_json"):
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_metadata_json_gin "
            "ON document_chunks USING gin (metadata_json jsonb_path_ops)"
        )


def downgrade() -> None:
    """Drop the GIN indexes."""
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_metadata_json_gin")
    op.execute("DROP INDEX IF EXISTS ix_documents_metadata_json_gin")
