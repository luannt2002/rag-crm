"""v0.4.0 — Cleanup dead tables + merge audit logs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-16

Drops 8 dead tables, merges `feedback` -> `request_logs` (adds
`feedback_comment`), merges `ai_config_audit_log` + `policy_audit_log` into a
new unified `audit_log` table, and drops wired-but-unused columns in
`ai_providers` and `documents`.

Safety: 0 production data — uses DROP ... CASCADE. Idempotent via IF EXISTS /
DO blocks.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def _drop_col(tbl: str, col: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{tbl} DROP COLUMN IF EXISTS {col}")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. request_logs: add feedback_comment (merge target for feedback.comment)
    # ------------------------------------------------------------------
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ADD COLUMN IF NOT EXISTS feedback_comment TEXT"
    )

    # ------------------------------------------------------------------
    # 2. Create unified audit_log table (replaces ai_config_audit_log +
    #    policy_audit_log).
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NULL,
            actor_user_id VARCHAR(128) NOT NULL,
            action VARCHAR(32) NOT NULL,
            resource_type VARCHAR(64) NOT NULL,
            resource_id VARCHAR(128) NOT NULL,
            before_json JSONB NULL,
            after_json JSONB NULL,
            reason TEXT NULL,
            trace_id VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_audit_log_tenant_time "
        f"ON {SCHEMA}.audit_log (tenant_id, resource_type, created_at DESC)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_audit_log_resource "
        f"ON {SCHEMA}.audit_log (resource_type, resource_id, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # 3. Drop dead tables (CASCADE handles FKs including
    #    golden_run_results -> golden_questions,
    #    payload_blobs -> model_invocations).
    # ------------------------------------------------------------------
    for tbl in (
        "golden_run_results",
        "golden_questions",
        "ai_config_audit_log",
        "policy_audit_log",
        "intent_routes",
        "bot_ai_tools",
        "payload_blobs",
        "feedback",
    ):
        op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{tbl} CASCADE")

    # ------------------------------------------------------------------
    # 4. Drop dead columns from retained tables.
    # ------------------------------------------------------------------
    _drop_col("ai_providers", "credentials_vault_path")
    _drop_col("documents", "authority_score")
    _drop_col("documents", "superseded_by")
    _drop_col("documents", "valid_from")
    _drop_col("documents", "valid_until")


def downgrade() -> None:
    # One-way cleanup — keep a skeletal rollback that restores structure
    # (empty) so Alembic can step back. Data is gone.
    _drop_col("request_logs", "feedback_comment")

    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_audit_log_resource")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_audit_log_tenant_time")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.audit_log")

    # Restore columns as NULL-able stubs so ORM code targeting older
    # revisions keeps parsing; values are lost.
    op.execute(
        f"ALTER TABLE {SCHEMA}.ai_providers "
        "ADD COLUMN IF NOT EXISTS credentials_vault_path TEXT"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.documents "
        "ADD COLUMN IF NOT EXISTS authority_score NUMERIC(3,2) NOT NULL DEFAULT 0.5"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.documents "
        "ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.documents "
        "ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.documents "
        "ADD COLUMN IF NOT EXISTS superseded_by UUID"
    )
    # Dropped tables are not recreated in downgrade — call out explicitly.
