"""Create event_inbox — transactional inbox for exactly-once handlers.

Revision: 0198
Prev:     0197

ADR-W1-D8b (process-then-mark exactly-once): the event-bus consumer
writes one ``event_inbox`` row per ``(subscriber_id, msg_id)`` AFTER the
handler succeeds — inside the same DB transaction as the handler's
side-effects when the handler accepts the ``inbox_tx`` hook. The
composite PK makes a duplicate mark a no-op (``ON CONFLICT DO
NOTHING``), so at-least-once delivery x idempotent apply = effective
exactly-once. XACK happens only after this row commits.

``subscriber_id`` = ``{subject}:{consumer-group}`` — one message can be
processed independently by multiple subscribers.

Retention: rows older than ``DEFAULT_INBOX_RETENTION_DAYS`` (mirrors the
Redis dedup-hint TTL) are safe to DELETE; ``ix_event_inbox_processed_at``
supports that sweep.

Numbering note: revisions 0196/0197 (API-key backfill stream) land in
the same wave from a parallel worktree — this file chains after 0197 by
wave agreement even though it ships from a separate branch.

Sacred-rule alignment:
  - Pure DDL via alembic (no psql hot-fix)
  - Reversible — downgrade drops the table
  - Domain-neutral — generic event-bus infrastructure table
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0198"
down_revision: str | None = "0197"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Create event_inbox with composite PK (subscriber_id, msg_id)."""
    op.create_table(
        "event_inbox",
        sa.Column("subscriber_id", sa.String(length=255), nullable=False),
        sa.Column("msg_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "subscriber_id", "msg_id", name="pk_event_inbox",
        ),
    )
    # Retention sweep scans by age only — index keeps the periodic
    # DELETE from seq-scanning a busy inbox.
    op.create_index(
        "ix_event_inbox_processed_at", "event_inbox", ["processed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_inbox_processed_at", table_name="event_inbox")
    op.drop_table("event_inbox")
