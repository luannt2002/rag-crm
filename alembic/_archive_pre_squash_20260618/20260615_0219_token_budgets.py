"""token_budgets — per-level token/cost limit + alert threshold (CRM config).

The CRM analytics layer reads existing append-only tables (``request_logs``,
``request_steps``, ``monitoring_log``) — no new event/hot tables are created
(those already capture per-request and per-node tokens/cost/latency). The one
genuinely-missing piece is a *config* table holding the budget a tenant/
workspace/bot is allowed to spend, so ``GET /crm/budget/status`` can report
"used vs limit %".

Scope here is the budget *record* + read path only — hard-cap enforcement at
request time is deliberately deferred (a separate costcap-worker concern), so
this migration adds no answer-path behaviour and cannot affect HALLU/answers.

Domain-neutral, tenant-scoped (``record_tenant_id`` mandatory), append-config
(rows are UPDATEd in place by admin, not the hot-path).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0219"
down_revision = "0218"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_budgets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("record_tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("record_bot_id", UUID(as_uuid=True), nullable=True),
        # 'tenant' | 'workspace' | 'bot' — which level this limit applies to.
        sa.Column("budget_level", sa.String(16), nullable=False),
        # 'daily' | 'monthly' — rollup window the limit is measured over.
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("token_limit", sa.BigInteger(), nullable=False),
        sa.Column("cost_limit_usd", sa.Numeric(12, 4), nullable=True),
        sa.Column("alert_at_pct", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("hard_cap", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "budget_level IN ('tenant','workspace','bot')",
            name="ck_token_budgets_level",
        ),
        sa.CheckConstraint(
            "period_type IN ('daily','monthly')",
            name="ck_token_budgets_period",
        ),
        # One active budget per (tenant, workspace, bot, level, period). NULLs
        # distinct so a tenant-level row (ws/bot NULL) coexists with bot rows.
        sa.UniqueConstraint(
            "record_tenant_id", "workspace_id", "record_bot_id",
            "budget_level", "period_type",
            name="uq_token_budgets_scope",
        ),
    )
    op.create_index(
        "ix_token_budgets_tenant_active",
        "token_budgets",
        ["record_tenant_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_token_budgets_tenant_active", table_name="token_budgets")
    op.drop_table("token_budgets")
