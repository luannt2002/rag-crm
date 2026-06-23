"""Token-ledger roll-up indexes — per-workspace + per-turn cost reporting.

Adds two indexes that back the new usage-rollup query layer:

* ``ix_token_ledger_ws_started`` (workspace_id, started_at) — the per-workspace
  Σ tokens/cost roll-up (``usage_rollup(dim='workspace')``) groups by
  workspace_id over a started_at window.
* ``ix_token_ledger_request_id`` (request_id) — the per-turn join back to
  ``request_logs`` (CRM reconciliation: SUM(cost_usd) GROUP BY request_id).

Both use ``CREATE INDEX CONCURRENTLY`` so the build does not lock writes on a
hot append-only table; that requires running outside a transaction (autocommit).

Revision ID: token_ledger_rollup_idx_20260624
Revises: enable_vision_gpt41_20260621
"""
from __future__ import annotations

from alembic import op

revision = "token_ledger_rollup_idx_20260624"
down_revision = "enable_vision_gpt41_20260621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside the migration's implicit transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_token_ledger_ws_started "
            "ON token_ledger USING btree (workspace_id, started_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_token_ledger_request_id "
            "ON token_ledger USING btree (request_id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_token_ledger_request_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_token_ledger_ws_started")
