"""0026 — Add plan limit columns to bots table.

New columns:
- max_documents INT DEFAULT 5  — số file tối đa per bot
- prompt_max_tokens INT DEFAULT NULL — limit system prompt tokens
- rerank_top_n INT DEFAULT NULL — override rerank top N
- plan_limits JSONB DEFAULT '{}' — extensible config, no migration for new keys
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE bots ADD COLUMN IF NOT EXISTS max_documents INT DEFAULT 5;
        ALTER TABLE bots ADD COLUMN IF NOT EXISTS prompt_max_tokens INT DEFAULT NULL;
        ALTER TABLE bots ADD COLUMN IF NOT EXISTS rerank_top_n INT DEFAULT NULL;
        ALTER TABLE bots ADD COLUMN IF NOT EXISTS plan_limits JSONB NOT NULL DEFAULT '{}';

        COMMENT ON COLUMN bots.max_documents IS 'Max documents per bot. Default 5';
        COMMENT ON COLUMN bots.prompt_max_tokens IS 'Max tokens for system prompt. NULL = unlimited';
        COMMENT ON COLUMN bots.rerank_top_n IS 'Override rerank top_n. NULL = use system default';
        COMMENT ON COLUMN bots.plan_limits IS 'Extensible JSONB config — add keys without migration';
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE bots DROP COLUMN IF EXISTS max_documents;
        ALTER TABLE bots DROP COLUMN IF EXISTS prompt_max_tokens;
        ALTER TABLE bots DROP COLUMN IF EXISTS rerank_top_n;
        ALTER TABLE bots DROP COLUMN IF EXISTS plan_limits;
    """))
