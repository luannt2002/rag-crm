"""Restore 8 bots (un-hide) for 10-bot load test.

Revision: 0159
Prev:     0158

Trigger (operator request 2026-06-03):
  Need 10 bots for parallel load test (RAGAS evaluation):
    - test-spa-id + thong-tu-09-2020-tt-nhnn (already visible)
    - 8 additional curriculum/legal bots (un-hide from alembic 0157)
  Keeps testbed bots hidden (tessss, 1111, 1774946011723, get-by-id-*,
  huybot, luannt-test-v2, meta-aware-*, legalbot).

Sacred-rule alignment:
  ✅ Pure alembic UPDATE; reversible
  ✅ Multi-tenant safe (bots already exist, just flip is_deleted)
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0159"
down_revision: str | None = "0158"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# The 8 curriculum bots to restore for 10-bot load test
_RESTORE_BOTS = (
    "dia-ly-vn",
    "hoa-hoc-10",
    "kinh-te-vi-mo",
    "lich-su-vn",
    "luat-giao-thong",
    "sinh-hoc-12",
    "tin-hoc-co-ban",
    "toan-hoc-12",
    "vat-ly-11",
    "y-te-co-ban",
)


def upgrade() -> None:
    """Restore 8 curriculum bots."""
    op.execute(
        text(
            """
            UPDATE bots
            SET is_deleted = false,
                deleted_at = NULL,
                updated_at = NOW()
            WHERE bot_id IN :restore_bots
              AND channel_type = 'web'
              AND is_deleted = true
            """,
        ).bindparams(restore_bots=_RESTORE_BOTS),
    )


def downgrade() -> None:
    """Re-hide the 8 bots."""
    op.execute(
        text(
            """
            UPDATE bots
            SET is_deleted = true,
                deleted_at = NOW(),
                updated_at = NOW()
            WHERE bot_id IN :restore_bots
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ).bindparams(restore_bots=_RESTORE_BOTS),
    )
