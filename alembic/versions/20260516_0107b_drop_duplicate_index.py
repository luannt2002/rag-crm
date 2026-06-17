"""[T3-Refactor] drop duplicate index ix_semantic_cache_bot

Revision ID: 0107b
Revises: 0107a
Create Date: 2026-05-16

Why
---
Audit finding (mega-sprint, 2026-05-15):
- ``ix_semantic_cache_bot`` indexes ``(record_bot_id)`` alone.
- ``semantic_cache`` already carries two composite indexes whose first
  column is ``record_bot_id`` (the per-bot HNSW vector index plus the
  ``(record_bot_id, question_hash)`` exact-match lookup index).
- A single-column prefix that is fully covered by a wider composite is
  dead weight: the planner never picks it, but every INSERT/UPDATE pays
  the index-maintenance cost and the backup footprint includes it.

Dropping the redundant index reclaims write throughput and disk space
without affecting any read path.

Idempotent: ``DROP INDEX IF EXISTS`` so upgrade is safe to retry.
``downgrade`` defensively recreates the index for completeness; the
recreated index will continue to be a no-op for query planning.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0107b"
down_revision = "0107a"
branch_labels = None
depends_on = None


_UPGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_semantic_cache_bot",
)


_DOWNGRADE_SQL: tuple[str, ...] = (
    (
        "CREATE INDEX IF NOT EXISTS ix_semantic_cache_bot "
        "ON semantic_cache(record_bot_id)"
    ),
)


def upgrade() -> None:
    for stmt in _UPGRADE_SQL:
        op.execute(text(stmt))


def downgrade() -> None:
    for stmt in _DOWNGRADE_SQL:
        op.execute(text(stmt))
