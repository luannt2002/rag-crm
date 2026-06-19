"""P17-P0-3: align knowledge_edges schema with code + add channel composite.

Issue: knowledge_edges had drift between DB (`bot_id`, `predicate`, no
channel_type, no UNIQUE) and code (which writes to `record_bot_id`,
`relation`, `channel_type`, with `ON CONFLICT (...composite...)`).
Result: GraphRAG store_triples() would error on any real insert. Hidden
until now because graph_rag_default_mode is seeded disabled.

This migration:
  1. Renames `bot_id` -> `record_bot_id` to follow naming convention
  2. Renames `predicate` -> `relation` to match code
  3. Adds `channel_type VARCHAR(64) NOT NULL DEFAULT 'web'` so composite
     identity is complete (same as documents / chunks / semantic_cache)
  4. Adds `source_document TEXT` (code writes to it)
  5. Adds UNIQUE (record_bot_id, channel_type, subject, relation, object)
     to make ON CONFLICT work and prevent duplicate triples per channel
  6. Drops + recreates per-bot indexes to use the new column names

Safe: no production data is lost. The table is empty in practice
because graph_rag is disabled.

Revision: 0037
Down revision: 0036
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old indexes that reference bot_id (column about to be renamed).
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_bot"))
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_subject"))
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_object"))

    # Drop old UNIQUE if present (migration 0022 declared one at CREATE
    # time but the live DB lost it through some earlier ALTER — guard
    # against both states with IF EXISTS).
    op.execute(text("ALTER TABLE knowledge_edges DROP CONSTRAINT IF EXISTS knowledge_edges_bot_id_subject_relation_object_key"))

    # Rename columns
    op.execute(text("ALTER TABLE knowledge_edges RENAME COLUMN bot_id TO record_bot_id"))
    op.execute(text("ALTER TABLE knowledge_edges RENAME COLUMN predicate TO relation"))

    # Add channel_type (default 'web' for any legacy row)
    op.execute(text("ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS channel_type VARCHAR(64) NOT NULL DEFAULT 'web'"))

    # Add source_document (code writes to it; was missing)
    op.execute(text("ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS source_document TEXT"))

    # Composite uniqueness matching the ON CONFLICT target in code
    op.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_edges_unique
        ON knowledge_edges (record_bot_id, channel_type, subject, relation, object)
    """))

    # Lookup indexes on the new composite
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_edges_bot_channel
        ON knowledge_edges (record_bot_id, channel_type)
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_edges_subject
        ON knowledge_edges (record_bot_id, channel_type, subject)
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_edges_object
        ON knowledge_edges (record_bot_id, channel_type, object)
    """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_unique"))
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_bot_channel"))
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_subject"))
    op.execute(text("DROP INDEX IF EXISTS idx_knowledge_edges_object"))

    op.execute(text("ALTER TABLE knowledge_edges DROP COLUMN IF EXISTS source_document"))
    op.execute(text("ALTER TABLE knowledge_edges DROP COLUMN IF EXISTS channel_type"))
    op.execute(text("ALTER TABLE knowledge_edges RENAME COLUMN relation TO predicate"))
    op.execute(text("ALTER TABLE knowledge_edges RENAME COLUMN record_bot_id TO bot_id"))

    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_edges_bot
        ON knowledge_edges (bot_id)
    """))
