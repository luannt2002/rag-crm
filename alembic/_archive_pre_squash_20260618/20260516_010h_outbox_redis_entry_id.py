"""[T1-Smartness] outbox.redis_entry_id — durability join key for forensic replay

Revision ID: 010h
Revises: 010g
Create Date: 2026-05-16

Per UPLOAD_FLOW_CASE_STUDY_20260516 Phase 1 (P0):
``publish_raw`` previously returned ``None`` — a Redis blip between XADD
and the outbox row's ``mark_processed`` update silently marked the row
processed while no entry existed on the Stream. Live evidence (outbox
row ``4248e92a`` processed at 16:12:14, Stream XLEN=0 at the same
instant) confirmed the silent fail.

Fix surface:

1. ``publish_raw`` / ``publish`` now return the XADD-assigned entry id
   and raise :class:`BusError` when XADD raises or returns falsy.
2. The publisher loop threads the entry id into
   ``mark_processed_in_session(session, rec.id, redis_entry_id=...)``.
3. This migration stores the entry id alongside the outbox row so
   operators can ``JOIN outbox.redis_entry_id`` against the actual
   Stream entry for forensic replay.

Nullable on purpose: legacy rows processed before this migration have
``NULL`` and that signal itself is informative ("row processed but no
durability anchor recorded"). Future rows that successfully publish
will always carry the id.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010h"
down_revision = "010f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox",
        sa.Column("redis_entry_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outbox", "redis_entry_id")
