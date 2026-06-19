"""v0.3.0 Task 3 — guardrail_events table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-15

Ghi log mọi lần guardrail (input/output/tool) kích hoạt. KHÔNG lưu raw content;
chỉ lưu rule_id + severity + action + JSONB details (ví dụ match_count, patterns).

Columns:
- event_id UUID PK default gen_random_uuid()
- message_id BIGINT (ID khách — verbatim, không FK cross-service)
- request_id UUID nullable (join RequestLogModel)
- tenant_id UUID nullable
- step_id UUID nullable (join RequestStepModel)
- guardrail_type VARCHAR(32)  # input|output|tool
- rule_id VARCHAR(64)         # prompt_injection|pii_vi_phone|sql_injection|...
- severity VARCHAR(16)        # info|warn|block
- action_taken VARCHAR(16)    # allow|redact|block|hitl
- details JSONB default '{}'
- detected_at TIMESTAMPTZ default now()

Indexes:
- (message_id)
- (tenant_id, detected_at)
- (rule_id, severity)
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.guardrail_events (
            event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id BIGINT NOT NULL,
            request_id UUID,
            tenant_id UUID,
            step_id UUID,
            guardrail_type VARCHAR(32) NOT NULL,
            rule_id VARCHAR(64) NOT NULL,
            severity VARCHAR(16) NOT NULL,
            action_taken VARCHAR(16) NOT NULL,
            details JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_guardrail_events_message "
        f"ON {SCHEMA}.guardrail_events (message_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_guardrail_events_tenant_time "
        f"ON {SCHEMA}.guardrail_events (tenant_id, detected_at)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_guardrail_events_rule_severity "
        f"ON {SCHEMA}.guardrail_events (rule_id, severity)"
    )


def downgrade() -> None:
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_guardrail_events_rule_severity"
    )
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_guardrail_events_tenant_time"
    )
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_guardrail_events_message"
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.guardrail_events")
