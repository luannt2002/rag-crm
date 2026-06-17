"""Add bots.language column for i18n support.

Each bot can specify its language (default 'vi' = Vietnamese).
The language determines which LanguagePack is used for prompts and
user-facing messages.

Revision ID: 0035
Revises: 0034
"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("language", sa.String(8), nullable=False, server_default="vi"),
    )
    # Backfill existing bots (server_default handles this, but be explicit).
    op.execute("UPDATE bots SET language = 'vi' WHERE language IS NULL")


def downgrade() -> None:
    op.drop_column("bots", "language")
