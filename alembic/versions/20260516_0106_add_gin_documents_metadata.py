"""[T1-Smartness] add GIN index ix_documents_metadata_json_gin

Revision ID: 0106
Revises: 0105
Create Date: 2026-05-16

Why
---
Live evidence (mega-sprint audit, 2026-05-15):
- ``documents.metadata_json`` is queried with the JSONB containment
  operator (``metadata_json @> '{"article_number": 38}'``) by the
  metadata-filter retriever node.
- Without a GIN index, PostgreSQL falls back to a sequential scan over
  every row in ``documents``. On the 'thong-tu-09-2020-tt-nhnn' bot the
  retriever spends ~120 ms on that one filter call.
- Migration 0044 attempted to ``CREATE INDEX IF NOT EXISTS`` on this
  expression but the live ``\\d documents`` shows no GIN index present —
  suspected race during a previous failed deploy left the index in an
  invalid state, then ``IF NOT EXISTS`` silently no-op'd on retry.

This migration defensively drops + recreates the index so the on-disk
state is guaranteed valid post-upgrade.

Idempotent: ``DROP INDEX IF EXISTS`` makes upgrade safe to retry.
Reversible via ``downgrade`` which removes the index.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


_UPGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_documents_metadata_json_gin",
    (
        "CREATE INDEX ix_documents_metadata_json_gin "
        "ON documents USING gin(metadata_json)"
    ),
)


_DOWNGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_documents_metadata_json_gin",
)


def upgrade() -> None:
    for stmt in _UPGRADE_SQL:
        op.execute(text(stmt))


def downgrade() -> None:
    for stmt in _DOWNGRADE_SQL:
        op.execute(text(stmt))
