"""Add channel_type column to documents table.

Documents need (record_bot_id, channel_type) composite for proper isolation.
Backfill from bots table, default 'web'.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS channel_type VARCHAR(32)")
    # Backfill from bots table
    op.execute("""
        UPDATE documents d SET channel_type = b.channel_type
        FROM bots b WHERE d.bot_id = b.id AND d.channel_type IS NULL
    """)
    op.execute("UPDATE documents SET channel_type = 'web' WHERE channel_type IS NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN channel_type SET NOT NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN channel_type SET DEFAULT 'web'")
    # Composite index
    op.execute("DROP INDEX IF EXISTS ix_doc_bot")
    op.execute("CREATE INDEX IF NOT EXISTS ix_doc_bot_channel ON documents (bot_id, channel_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_doc_created ON documents (created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_doc_created")
    op.execute("DROP INDEX IF EXISTS ix_doc_bot_channel")
    op.execute("CREATE INDEX IF NOT EXISTS ix_doc_bot ON documents (bot_id)")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS channel_type")
