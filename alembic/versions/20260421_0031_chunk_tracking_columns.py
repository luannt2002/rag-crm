"""Add content_chars / chunk_chars columns for audit tracking.

Pre-compute string lengths at ingest time so auditor analytics
have zero overhead at query time.

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # documents: total content length (chars)
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_chars INTEGER")
    # document_chunks: per-chunk length (chars)
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_chars INTEGER")
    # Backfill existing rows
    op.execute("UPDATE documents SET content_chars = 0 WHERE content_chars IS NULL")
    op.execute(
        "UPDATE document_chunks SET chunk_chars = length(content) WHERE chunk_chars IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS chunk_chars")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS content_chars")
