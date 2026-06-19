"""Soft-delete 11 non-demo bots — keep test-spa-id + thong-tu-09 for demo UI.

Revision: 0157
Prev:     0156

Trigger (operator request 2026-05-30):
  Demo UI should only expose 2 bots that have been verified through the
  120-question load test:
    - test-spa-id (web)            — multi-turn HALLU=0 confirmed
    - thong-tu-09-2020-tt-nhnn (web) — 10/10 perfect on legal corpus

  Remaining 11 bots are temporarily hidden from the public demo via the
  existing soft-delete pattern (bots.is_deleted = true).

Sacred-rule alignment:
  ✅ Pure alembic UPDATE (CLAUDE.md rule 7) — no psql hot-fix
  ✅ Soft-delete only — documents/chunks/conversations NOT touched
  ✅ Reversible — downgrade flips is_deleted back to false
  ✅ Domain-neutral — no per-tenant logic in code path
  ✅ Multi-tenant — script targets bot_id explicit, no broad sweep

Note:
  Soft-delete affects EXTERNAL UI listing only. Internal queries (chat,
  ingest, audit) continue to function — internal lookup uses bot_id +
  channel_type + record_tenant_id + workspace_id (4-key identity);
  is_deleted flag is gated at the listing/admin layer.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0157"
down_revision: str | None = "0156"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Demo whitelist — bots that REMAIN visible.
_DEMO_BOTS: tuple[str, ...] = (
    "test-spa-id",
    "thong-tu-09-2020-tt-nhnn",
)


def upgrade() -> None:
    """Soft-delete all bots except the demo whitelist."""
    op.execute(
        text(
            """
            UPDATE bots
            SET is_deleted = true,
                deleted_at = NOW(),
                updated_at = NOW()
            WHERE bot_id NOT IN :keep_bots
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ).bindparams(keep_bots=_DEMO_BOTS),
    )


def downgrade() -> None:
    """Restore visibility for all bots that were hidden by this migration."""
    op.execute(
        text(
            """
            UPDATE bots
            SET is_deleted = false,
                deleted_at = NULL,
                updated_at = NOW()
            WHERE bot_id NOT IN :keep_bots
              AND channel_type = 'web'
              AND is_deleted = true
            """,
        ).bindparams(keep_bots=_DEMO_BOTS),
    )
