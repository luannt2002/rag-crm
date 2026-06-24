"""Add document_service_index.entity_synonyms search column + trigram index.

[T1-Smartness] Aliases/synonym role for the stats index. The parser
(document_stats._column_roles) now recognises an Aliases column and captures its
``;``-separated search variants into ParsedEntity.aliases. This migration adds the
backing column + a GIN trigram index so query_by_name_keyword can match an alias via
unaccent()+ILIKE even when entity_name uses a different notation (e.g. "265/50ZR20"
vs a query "265/50R20", both listed in the row's Aliases). The pg_trgm GIN index
accelerates the substring ILIKE the synonym match issues.

Schema change (derived enrichment column, reproducible from chunks on re-ingest) —
a tracked alembic migration is the correct vehicle (CLAUDE.md: no psql hot-fix).

Revision ID: stats_index_entity_synonyms_20260624
Revises: enable_vision_gpt41_20260621
Create Date: 2026-06-24
"""
from __future__ import annotations

from alembic import op

revision = "stats_index_entity_synonyms_20260624"
down_revision = "enable_vision_gpt41_20260621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pg_trgm powers the substring ILIKE the synonym match runs; idempotent create.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "ALTER TABLE document_service_index "
        "ADD COLUMN IF NOT EXISTS entity_synonyms TEXT"
    )
    # GIN trigram index accelerates ``entity_synonyms ILIKE '%...%'`` lookups.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_dsi_entity_synonyms_trgm "
        "ON document_service_index USING gin (entity_synonyms gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_dsi_entity_synonyms_trgm")
    op.execute(
        "ALTER TABLE document_service_index "
        "DROP COLUMN IF EXISTS entity_synonyms"
    )
