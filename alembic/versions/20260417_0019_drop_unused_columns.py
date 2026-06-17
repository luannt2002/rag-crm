"""0019 — Drop 12 unused columns + context_snapshots table.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-17
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
from sqlalchemy import text

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- request_logs: drop 7 columns --
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS session_id"))
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS model_config_version"))
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS cost_currency"))
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS payload_sha256"))
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS binding_variant"))
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS agent_id"))
    op.execute(text("ALTER TABLE request_logs DROP COLUMN IF EXISTS prompt_version_id"))

    # -- model_invocations: drop 4 columns --
    op.execute(text("ALTER TABLE model_invocations DROP COLUMN IF EXISTS step_id"))
    op.execute(text("ALTER TABLE model_invocations DROP COLUMN IF EXISTS params"))
    op.execute(text("ALTER TABLE model_invocations DROP COLUMN IF EXISTS system_prompt_version_id"))
    op.execute(text("ALTER TABLE model_invocations DROP COLUMN IF EXISTS retrieved_chunk_ids"))

    # -- bots: drop 1 column --
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS workflow_id"))

    # -- drop entire context_snapshots table --
    op.execute(text("DROP TABLE IF EXISTS context_snapshots"))


def downgrade() -> None:
    # -- re-add request_logs columns --
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS session_id UUID"
    ))
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS model_config_version INTEGER"
    ))
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS cost_currency VARCHAR(8) NOT NULL DEFAULT 'USD'"
    ))
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS payload_sha256 VARCHAR(64)"
    ))
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS binding_variant VARCHAR(16)"
    ))
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"
    ))
    op.execute(text(
        "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS prompt_version_id UUID"
    ))

    # -- re-add model_invocations columns --
    op.execute(text(
        "ALTER TABLE model_invocations ADD COLUMN IF NOT EXISTS step_id UUID"
    ))
    op.execute(text(
        "ALTER TABLE model_invocations ADD COLUMN IF NOT EXISTS params JSONB NOT NULL DEFAULT '{}'"
    ))
    op.execute(text(
        "ALTER TABLE model_invocations ADD COLUMN IF NOT EXISTS system_prompt_version_id UUID"
    ))
    op.execute(text(
        "ALTER TABLE model_invocations ADD COLUMN IF NOT EXISTS retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}'"
    ))

    # -- re-add bots column --
    op.execute(text(
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS workflow_id VARCHAR(128)"
    ))

    # -- re-create context_snapshots table --
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS context_snapshots (
            snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id BIGINT NOT NULL,
            request_id UUID,
            tenant_id UUID,
            retrieved_chunks JSONB NOT NULL DEFAULT '[]',
            retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}',
            retrieval_scores JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ctx_snap_message ON context_snapshots (message_id)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ctx_snap_request ON context_snapshots (request_id)"
    ))
