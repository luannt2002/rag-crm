"""Seed the 3 demo bots into 3 DISTINCT workspaces (same tenant).

Owner directive 2026-06-13: each demo bot gets its own workspace slug so their
ingest gets an independent per-workspace fairness budget (one bot's bulk
re-upload can't starve the others) and the data is forensically partitioned.

  test-spa-id              → workspace ``spa``
  chinh-sach-xe            → workspace ``xe``
  thong-tu-09-2020-tt-nhnn → workspace ``legal``

The unique constraint ``uq_bots_record_tenant_workspace_bot_channel`` stays
satisfied (each (tenant, ws, bot_id, channel) tuple is distinct). System
prompts (``bots.system_prompt``) are NOT touched. Callers (chat / upload /
eval) must now pass the matching ``workspace_id`` — the test scenarios +
eval_gate + the reinit endpoint are updated in the same change.

Idempotent + reversible: downgrade restores all three to the tenant-default
workspace (``str(record_tenant_id)``), the prior behaviour.
"""
import sqlalchemy as sa
from alembic import op

revision = "0213"
down_revision = "0212"
branch_labels = None
depends_on = None

_WS = {
    "test-spa-id": "spa",
    "chinh-sach-xe": "xe",
    "thong-tu-09-2020-tt-nhnn": "legal",
}


def upgrade() -> None:
    conn = op.get_bind()
    for bot_id, ws in _WS.items():
        conn.execute(sa.text(
            "UPDATE bots SET workspace_id = :ws, updated_at = now() "
            "WHERE bot_id = :bot_id"
        ), {"ws": ws, "bot_id": bot_id})


def downgrade() -> None:
    conn = op.get_bind()
    for bot_id in _WS:
        conn.execute(sa.text(
            "UPDATE bots SET workspace_id = record_tenant_id::text, "
            "updated_at = now() WHERE bot_id = :bot_id"
        ), {"bot_id": bot_id})
