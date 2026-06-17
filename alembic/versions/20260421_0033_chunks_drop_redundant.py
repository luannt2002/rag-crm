"""Drop redundant bot_id and tenant_id from document_chunks.

Chunks reference documents via document_id FK. bot_id and tenant_id
are redundant — query through documents table instead.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_bot")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS bot_id")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tenant_id")


def downgrade() -> None:
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS bot_id UUID")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tenant_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_bot ON document_chunks (bot_id)")
