"""[T1-Smartness] 0118 — Stats Index schema: document_service_index + documents.summary_json.

Revision ID: 0118
Revises: 0115
Create Date: 2026-05-26

Context (Stats Index pattern):
Aggregation / range queries ("dưới 2tr có bao nhiêu dịch vụ?",
"liệt kê tất cả") recall 28-40% because the pipeline top_k=20 cap
hides the remaining 16+ matching chunks. Industry pattern (Pinecone /
AI21): parse table/CSV chunks → structured row table with metadata
filters → SQL count/filter, no LLM round-trip. HALLU=0 preserved
because the count is deterministic Python, not an LLM summary.

Schema shipped here:
1. ``document_service_index`` — one row per extracted entity (service /
   product / item) from a table/CSV chunk. Indexed on (bot, price)
   pairs for fast range queries. RLS tenant-scoped.
2. ``documents.summary_json`` — per-doc aggregate blob (entity_count,
   price_min/max, price_buckets, categories). Written by Agent B2
   (ingest pipeline wire) and read by Agent B3 (query router).

Domain-neutral design:
- No column names contain domain terms (spa, medispa, service names).
- ``entity_name`` / ``entity_category`` are opaque VARCHAR — the parser
  in ``shared/document_stats.py`` decides their values at ingest time.
- ``attributes_json`` JSONB carries extra columns without schema churn.

RLS policy mirrors ``audit_log``:
    app.tenant_id setting → record_tenant_id column check.
    Admin queries bypass via ``SET app.tenant_id = '<admin-uuid>'``.

Downgrade: DROP TABLE document_service_index CASCADE;
           DROP INDEX idx_documents_summary_json;
           ALTER TABLE documents DROP COLUMN IF EXISTS summary_json;
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision: str = "0118"
down_revision: str | None = "0117"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. document_service_index
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS document_service_index (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            record_tenant_id    UUID        NOT NULL,
            workspace_id        VARCHAR(64) NOT NULL,
            record_bot_id       UUID        NOT NULL
                                            REFERENCES bots(id) ON DELETE CASCADE,
            record_document_id  UUID        NOT NULL
                                            REFERENCES documents(id) ON DELETE CASCADE,
            record_chunk_id     UUID
                                            REFERENCES document_chunks(id) ON DELETE SET NULL,
            entity_name         TEXT        NOT NULL,
            entity_category     TEXT,
            price_primary       NUMERIC,
            price_secondary     NUMERIC,
            attributes_json     JSONB       NOT NULL DEFAULT '{}',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    # Composite price-range index: most common query shape is
    #   WHERE record_bot_id = ? AND price_primary < ?
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dsi_bot_price1 "
        "ON document_service_index(record_bot_id, price_primary)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dsi_bot_price2 "
        "ON document_service_index(record_bot_id, price_secondary)"
    ))
    # Document-scoped lookup (re-ingest invalidation)
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dsi_doc "
        "ON document_service_index(record_document_id)"
    ))
    # GIN for JSONB attribute queries
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dsi_attrs "
        "ON document_service_index USING gin (attributes_json)"
    ))

    # RLS — mirrors audit_log pattern
    conn.execute(text(
        "ALTER TABLE document_service_index ENABLE ROW LEVEL SECURITY"
    ))
    # Drop first in case migration is re-run after a failed partial upgrade
    conn.execute(text(
        "DROP POLICY IF EXISTS tenant_isolation ON document_service_index"
    ))
    conn.execute(text("""
        CREATE POLICY tenant_isolation ON document_service_index
            USING (
                record_tenant_id = current_setting('app.tenant_id')::uuid
            )
            WITH CHECK (
                record_tenant_id = current_setting('app.tenant_id')::uuid
            )
    """))

    # ------------------------------------------------------------------
    # 2. documents.summary_json column
    # ------------------------------------------------------------------
    conn.execute(text(
        "ALTER TABLE documents "
        "ADD COLUMN IF NOT EXISTS summary_json JSONB"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_documents_summary_json "
        "ON documents USING gin (summary_json) "
        "WHERE summary_json IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # Remove summary_json index + column first (no cascade dependency)
    conn.execute(text(
        "DROP INDEX IF EXISTS idx_documents_summary_json"
    ))
    conn.execute(text(
        "ALTER TABLE documents DROP COLUMN IF EXISTS summary_json"
    ))

    # document_service_index — CASCADE removes dependent indexes + policies
    conn.execute(text(
        "DROP TABLE IF EXISTS document_service_index CASCADE"
    ))
