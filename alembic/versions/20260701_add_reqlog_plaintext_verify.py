"""Add opt-in plaintext ``question_text`` / ``answer_text`` to ``request_logs``.

Trigger (user, verify flow 2026-07-01): ``request_logs`` stores only
``question_hash`` / ``answer_hash`` (Privacy 2.B — no raw text), so a reviewer
cannot read WHAT was asked / answered when auditing a request alongside its cost,
citations, and ``is_correct`` verdict. Plaintext lives in ``chat_histories`` but
that table is not joined to the per-request verify columns and is wiped on
clear-chat.

Adds two NULLABLE TEXT columns to ``request_logs``. They stay NULL unless the
platform opts in (``settings.request_log_store_plaintext`` /
``RAGBOT_REQUEST_LOG_STORE_PLAINTEXT``) — the repository writes them only when the
flag is on, so the privacy-by-default posture is preserved and existing rows are
untouched. Reversible.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "reqlog_plaintext_260701"
down_revision = "seed_anti_variant_260701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("question_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("answer_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "answer_text")
    op.drop_column("request_logs", "question_text")
