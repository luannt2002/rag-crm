"""token_ledger — per-call token-log-center for ingest + query (provider-agnostic).

Durable, decoupled audit of EVERY token-spending action (LLM / embedding /
rerank), across the upload (ingest) and query flows. One row per LLM/embed/
rerank call, classified by ``mode`` ('ingest'|'query') + ``action`` + ``purpose``.

Design decisions (see token-log-center design doc):
  - NO foreign keys → rows SURVIVE bot/document delete (cost/usage history must
    remain for reporting + verification). Mirrors monitoring_log (0217) which is
    likewise FK-free + durable.
  - 4-key identity SNAPSHOT at log-time (record_tenant_id + record_bot_id +
    bot_id + workspace_id + channel_type) — value-copied, not joined, so the
    identity is immutable even after the bot row is deleted. record_bot_id (the
    internal UUID, 1-1 with the 4-key tuple) is the primary report key, so a
    slug (bot_id, channel_type) reused across workspaces never conflates.
  - Per-model unit-price SNAPSHOT (input/output/cached) so historical cost is
    frozen when ai_models prices later change. cost_usd is NULLABLE — computed
    on demand (owner: money not urgent yet, but the columns are ready).
  - status: 'active' default; bot-delete bulk-tags rows 'deleted' (never wipes).

This is the per-CALL audit center; monitoring_log stays the per-REQUEST billing
mirror for the query flow. They answer different questions and cross-check.
"""
import sqlalchemy as sa
from alembic import op

revision = "0226"
down_revision = "0225"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # ── classification ──
        sa.Column("mode", sa.String(16), nullable=False),       # ingest | query
        sa.Column("action", sa.String(32), nullable=False),     # llm | embedding | rerank
        sa.Column("purpose", sa.String(64)),                    # cr_enrichment|narrate|generate|...
        # ── provider-agnostic ──
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(128)),
        # ── 4-key identity snapshot (NO FK) ──
        sa.Column("record_tenant_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("record_bot_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("bot_id", sa.String(255)),
        sa.Column("workspace_id", sa.String(64)),
        sa.Column("channel_type", sa.String(32)),
        sa.Column("request_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("document_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("trace_id", sa.String(128)),
        # ── 3 token counts ──
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer, nullable=False, server_default="0"),
        # ── 2 timestamps ──
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        # ── cost (snapshot unit-price, cost nullable) ──
        sa.Column("input_unit_price", sa.Numeric(12, 6)),
        sa.Column("output_unit_price", sa.Numeric(12, 6)),
        sa.Column("cached_unit_price", sa.Numeric(12, 6)),
        sa.Column("cost_usd", sa.Numeric(14, 8)),
        # ── status ──
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("finish_reason", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_token_ledger_bot_started", "token_ledger",
                    ["record_bot_id", "started_at"])
    op.create_index("ix_token_ledger_started", "token_ledger", ["started_at"])
    op.create_index("ix_token_ledger_mode_started", "token_ledger", ["mode", "started_at"])
    op.create_index("ix_token_ledger_provider", "token_ledger", ["provider", "started_at"])
    op.create_index("ix_token_ledger_tenant_started", "token_ledger",
                    ["record_tenant_id", "started_at"])
    # Seed the provider config knob (governed via alembic, not psql).
    op.execute(
        "INSERT INTO system_config (key, value) VALUES "
        "('token_ledger_provider', '\"db\"') ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key = 'token_ledger_provider'")
    op.drop_index("ix_token_ledger_tenant_started", table_name="token_ledger")
    op.drop_index("ix_token_ledger_provider", table_name="token_ledger")
    op.drop_index("ix_token_ledger_mode_started", table_name="token_ledger")
    op.drop_index("ix_token_ledger_started", table_name="token_ledger")
    op.drop_index("ix_token_ledger_bot_started", table_name="token_ledger")
    op.drop_table("token_ledger")
