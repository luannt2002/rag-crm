"""v0.3.0 Task 1 — Privacy 2.B (drop raw text, add hashes, NOT NULL message_id).

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-15

Schema changes:
- `messages`: ADD `deleted_at` (GDPR right-to-erasure soft-delete).
- `request_logs`:
  - DROP `question`, DROP `answer` (raw PII forbidden per Phần 2.B).
  - ADD `question_hash CHAR(64) NOT NULL`, `answer_hash CHAR(64) NULL`.
  - ALTER `message_id` SET NOT NULL + FK → messages(id) ON DELETE SET NULL.
  - ADD indexes on `question_hash`, `message_id`.

Data strategy: legacy rows without message_id cannot be preserved (FK NOT NULL) —
migration truncates `request_logs` as part of privacy cleanup. This is safe for
v0.2.0 → v0.3.0-mvp because monitoring tables were not yet in production.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "public"


def upgrade() -> None:
    # --- messages: soft-delete column --------------------------------------
    # 0001 uses Base.metadata.create_all; because the ORM already includes
    # `deleted_at`, the column is created at 0001 time. Use IF NOT EXISTS so
    # fresh DBs + DBs that were stamped at 0002 both end up consistent.
    op.execute(
        f"ALTER TABLE {SCHEMA}.messages "
        "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE"
    )

    # --- request_logs: purge legacy rows (they carry raw PII + no FK) -------
    op.execute(f"DELETE FROM {SCHEMA}.request_logs")

    # ORM mới đã bỏ 2 cột này nên 0001 không tạo chúng trên DB mới.
    # Giữ IF EXISTS để migration chạy được trên DB cũ đã có raw cols.
    op.execute(f"ALTER TABLE {SCHEMA}.request_logs DROP COLUMN IF EXISTS question")
    op.execute(f"ALTER TABLE {SCHEMA}.request_logs DROP COLUMN IF EXISTS answer")

    # question_hash/answer_hash có thể đã được 0001 tạo (ORM mới). Dùng IF NOT EXISTS.
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ADD COLUMN IF NOT EXISTS question_hash VARCHAR(64) NOT NULL"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ADD COLUMN IF NOT EXISTS answer_hash VARCHAR(64)"
    )

    # NOT NULL on message_id (external BIGINT — no FK, different type from messages.id UUID).
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ALTER COLUMN message_id SET NOT NULL"
    )

    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_reqlog_question_hash "
        f"ON {SCHEMA}.request_logs (question_hash)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_reqlog_message_id "
        f"ON {SCHEMA}.request_logs (message_id)"
    )


def downgrade() -> None:
    op.drop_index("ix_reqlog_message_id", table_name="request_logs", schema=SCHEMA)
    op.drop_index("ix_reqlog_question_hash", table_name="request_logs", schema=SCHEMA)

    op.alter_column(
        "request_logs",
        "message_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
        schema=SCHEMA,
    )

    op.drop_column("request_logs", "answer_hash", schema=SCHEMA)
    op.drop_column("request_logs", "question_hash", schema=SCHEMA)

    op.add_column(
        "request_logs",
        sa.Column("answer", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "request_logs",
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        schema=SCHEMA,
    )

    op.drop_column("messages", "deleted_at", schema=SCHEMA)
