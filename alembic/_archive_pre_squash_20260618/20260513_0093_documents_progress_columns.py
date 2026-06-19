"""documents progress columns — current_step + progress_percent + chunks_total + chunks_processed

Revision ID: 0093
Revises: 0092
Create Date: 2026-05-13

Adds per-document ingest progress tracking so UI / clients can render a
progress bar while the worker is chunking / enriching / embedding /
indexing. Without these columns the only signal is `state` (DRAFT vs
active) — opaque for the ~6 minute Haiku enrich window on large docs.

Columns:
- current_step   VARCHAR(32)  — chunking|enriching|embedding|indexing|active|failed
- progress_percent INT        — 0-100
- chunks_total   INT          — total chunks expected (set after split)
- chunks_processed INT        — chunks done in current step
- progress_updated_at TIMESTAMPTZ — heartbeat for ETA calc

All nullable; existing rows backfilled NULL and read as "no progress
data" by the endpoint (UI falls back to chunk_count signal).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
          ADD COLUMN IF NOT EXISTS current_step VARCHAR(32),
          ADD COLUMN IF NOT EXISTS progress_percent INT,
          ADD COLUMN IF NOT EXISTS chunks_total INT,
          ADD COLUMN IF NOT EXISTS chunks_processed INT,
          ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMP WITH TIME ZONE
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
          DROP COLUMN IF EXISTS progress_updated_at,
          DROP COLUMN IF EXISTS chunks_processed,
          DROP COLUMN IF EXISTS chunks_total,
          DROP COLUMN IF EXISTS progress_percent,
          DROP COLUMN IF EXISTS current_step
        """
    )
