"""0022 — Add knowledge_edges table for GraphRAG entity-relation storage.

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-18
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            bot_id UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            relation TEXT NOT NULL,
            object TEXT NOT NULL,
            source_document TEXT,
            source_chunk_id UUID,
            confidence FLOAT DEFAULT 1.0,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(bot_id, subject, relation, object)
        )
    """))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_edges_bot "
        "ON knowledge_edges(bot_id)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_edges_subject "
        "ON knowledge_edges(bot_id, subject)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_edges_object "
        "ON knowledge_edges(bot_id, object)"
    ))

    # Seed GraphRAG system_config keys
    for key, value, value_type, description in [
        ("graph_rag_default_mode", '"disabled"', "string", "GraphRAG default mode (disabled/enabled/adaptive)"),
        ("graph_rag_max_hops", "2", "int", "Max graph traversal depth"),
        ("graph_rag_max_triples_per_chunk", "10", "int", "Max triples extracted per chunk"),
        ("graph_rag_entity_extraction_model", '""', "string", "Model for entity extraction (fallback to llm_default_model)"),
    ]:
        op.execute(text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:key, CAST(:val AS jsonb), :vtype, :desc, now())
            ON CONFLICT (key) DO NOTHING
        """).bindparams(key=key, val=value, vtype=value_type, desc=description))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS knowledge_edges"))
    op.execute(text(
        "DELETE FROM system_config WHERE key = ANY(:keys)"
    ).bindparams(keys=[
        "graph_rag_default_mode",
        "graph_rag_max_hops",
        "graph_rag_max_triples_per_chunk",
        "graph_rag_entity_extraction_model",
    ]))
