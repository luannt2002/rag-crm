"""v0.2.0 — monitoring + capability + policy + golden tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from ragbot.infrastructure.db.models import Base

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Side-effect: models_monitoring imported at runtime via models.py adds
    # all v0.2.0 tables (request_logs, request_steps, model_capabilities,
    # tenant_model_policy, policy_audit_log, golden_questions, golden_run_results)
    # to Base.metadata. create_all is idempotent — only creates missing tables.
    Base.metadata.create_all(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    # Drop only v0.2.0 tables (keep v0.1.0 schema intact)
    for table_name in (
        "golden_run_results",
        "golden_questions",
        "policy_audit_log",
        "tenant_model_policy",
        "model_capabilities",
        "request_steps",
        "request_logs",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
