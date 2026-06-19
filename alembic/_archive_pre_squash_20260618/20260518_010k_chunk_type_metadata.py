"""[T1-Smartness] document_chunks.chunk_type — first-class modality column.

Revision ID: 010j
Revises: 010i
Create Date: 2026-05-18

RAG-Anything M10 mindset — lift block modality out of ``metadata_json``
into a first-class indexed column on ``document_chunks`` so the retrieval
path (modality-aware rerank, content-type dispatch) can filter without
parsing JSONB on every row.

Allowed values (kept in sync with ``shared/constants.py``):

* ``text`` — prose / heading / list — default for legacy rows.
* ``table`` — markdown pipe-table block.
* ``table_row`` — single CSV / Excel row carrying its header.
* ``code`` — fenced code block.

The column is ``NOT NULL DEFAULT 'text'`` so the backfill is free —
existing chunks keep their old behaviour (prose-style retrieval) while
new ingests start emitting the correct modality. An ``ix_chunks_type``
b-tree index supports modality-filtered retrieval queries.

Why a CHECK constraint (not enum):
* Enum types in Postgres require ``ALTER TYPE`` ceremony to add a value
  (cannot run inside a transaction in some versions); a VARCHAR + CHECK
  evolves cleanly via ``DROP CONSTRAINT ... ADD CONSTRAINT ...``.
* The allowed set is small + stable; the CHECK gives the same data-quality
  guarantee at a fraction of the migration risk.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010k"
down_revision = "010j"
branch_labels = None
depends_on = None

# Allowed chunk-type values — mirror CHUNK_TYPES_ALLOWED in
# ``ragbot/shared/constants.py``. Kept inline so the migration is
# self-contained (alembic upgrade runs without importing app code).
_ALLOWED_TYPES = ("text", "table", "table_row", "code")


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "chunk_type",
            sa.String(length=32),
            nullable=False,
            server_default="text",
        ),
    )
    op.create_check_constraint(
        "ck_document_chunks_chunk_type",
        "document_chunks",
        "chunk_type IN ({})".format(
            ", ".join(f"'{t}'" for t in _ALLOWED_TYPES),
        ),
    )
    op.create_index(
        "ix_chunks_type",
        "document_chunks",
        ["chunk_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_type", table_name="document_chunks")
    op.drop_constraint(
        "ck_document_chunks_chunk_type",
        "document_chunks",
        type_="check",
    )
    op.drop_column("document_chunks", "chunk_type")
