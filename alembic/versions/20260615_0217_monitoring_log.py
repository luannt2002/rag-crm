"""Durable monitoring ledger — per-request timing + tokens + cost.

Owner feature 2026-06-15: every answered request must be logged with start /
finish / duration (3 time columns) + token usage + cost, and the data must
SURVIVE bot deletion / per-bot clear so it can back day-by-day monitoring.

``request_logs`` already captures the same fields but its FK to ``bots`` is
ON DELETE CASCADE (and a per-bot clear DELETEs it), so cost/usage history is
wiped on reset — the 2026-06-15 $200 audit could not be reconstructed in-DB
for that reason. This append-only mirror has NO foreign keys, so nothing
cascades into it: it is the durable source of truth for monitoring/billing.

Written alongside ``finalize_request_log`` (one cheap INSERT in the same txn).
"""
import sqlalchemy as sa
from alembic import op

revision = "0217"
down_revision = "0216"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # External request id (reference only — NO FK so a request_logs/bot
        # delete never cascades here).
        sa.Column("request_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("record_tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("record_bot_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bot_id", sa.String(255), nullable=True),       # denormalized slug
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("channel_type", sa.String(32), nullable=True),
        # The 3 time columns the owner asked for:
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        # Token + cost:
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    # Monitoring queries: by bot over time, and global daily rollups.
    op.create_index("ix_monitoring_log_bot_started", "monitoring_log",
                    ["record_bot_id", "started_at"])
    op.create_index("ix_monitoring_log_started", "monitoring_log", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_monitoring_log_started", table_name="monitoring_log")
    op.drop_index("ix_monitoring_log_bot_started", table_name="monitoring_log")
    op.drop_table("monitoring_log")
