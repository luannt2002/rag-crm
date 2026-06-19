"""[T2-CostPerf] add composite index documents(record_bot_id, state)

Revision ID: 0107a
Revises: 0106
Create Date: 2026-05-16

Why
---
Live evidence (mega-sprint audit, 2026-05-15):
- ``documents`` shows a sequential-scan rate of 54% in ``pg_stat_user_tables``
  on the coder-dev DB.
- The hot retrieval-time predicate is::

      WHERE record_bot_id = :bot_id AND state = 'active'

  Today we have two single-column B-tree indexes (``ix_doc_bot`` on
  ``record_bot_id`` and ``ix_doc_state`` on ``state``) — the planner
  picks one and filters the other in-memory, which costs ~20-40 ms per
  query when a bot has thousands of historical chunks.
- A single composite index over ``(record_bot_id, state)`` lets the
  planner satisfy both equality predicates from one index lookup,
  cutting the query cost to a few ms.

Idempotent: ``DROP INDEX IF EXISTS`` upfront so retry is safe.
Reversible via ``downgrade`` which drops the composite (the two
single-column indexes are untouched and remain available).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0107a"
down_revision = "0106"
branch_labels = None
depends_on = None


_UPGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_doc_bot_state",
    "CREATE INDEX ix_doc_bot_state ON documents(record_bot_id, state)",
)


_DOWNGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_doc_bot_state",
)


def upgrade() -> None:
    for stmt in _UPGRADE_SQL:
        op.execute(text(stmt))


def downgrade() -> None:
    for stmt in _DOWNGRADE_SQL:
        op.execute(text(stmt))
