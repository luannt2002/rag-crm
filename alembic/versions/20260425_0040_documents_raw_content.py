"""S8 δ1: add documents.raw_content for BM25 / audit reconstruction.

Purpose: preserve the pre-chunked source document text so future BM25 /
lexical-search paths (P15-1) and audit tooling can reconstruct the original
without re-downloading the source_url. Nullable — legacy rows stay NULL until
re-ingested.

Revision: 0040
Down revision: 0039
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("raw_content", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "raw_content")
