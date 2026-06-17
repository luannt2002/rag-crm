"""[T2-CostPerf] bots token quota columns — tokens_used + extra_max_tokens + extra_output_tokens_per_response + bypass_token_check

Revision ID: 0100
Revises: 0099
Create Date: 2026-05-14

Token Quota Monetization — per-bot token accounting + overage purchase.

Columns added to ``bots``:
- ``tokens_used`` BIGINT — cumulative tokens consumed (response + retrieval).
  BigInt (not Integer) because Integer 32-bit caps at 2.1B which would
  overflow inside a year on a busy tenant; BigInt 9.2×10^18 is ~1000 years
  of headroom even at 10M tokens/day.
- ``extra_max_tokens`` BIGINT — operator-granted overage on top of plan
  quota (paid top-up). BigInt for the same overflow argument — operators
  can purchase large pools.
- ``extra_output_tokens_per_response`` INT — per-response output cap lift.
  Integer is fine — a single response cap stays well below 10K tokens, no
  realistic path to 2.1B.
- ``bypass_token_check`` BOOLEAN DEFAULT false — emergency / paid-tier
  flag to bypass the quota gate at request time. Defaults OFF so existing
  rows enforce quota.

CHECK constraints (``>= 0``) on the three token counters — defence vs
buggy decrements / signed-overflow imports. Bool stays unchecked.

Index ``ix_bots_tokens_used`` — admin "top consumers" dashboard sorts by
consumed tokens DESC; index keeps that O(log n) regardless of fleet size.

Downgrade drops all 4 columns, the 3 CHECK constraints and the index.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE bots
          ADD COLUMN IF NOT EXISTS tokens_used BIGINT NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS extra_max_tokens BIGINT NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS extra_output_tokens_per_response INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS bypass_token_check BOOLEAN NOT NULL DEFAULT false
        """
    )

    op.execute(
        """
        ALTER TABLE bots
          ADD CONSTRAINT ck_bots_tokens_used_nonneg
            CHECK (tokens_used >= 0),
          ADD CONSTRAINT ck_bots_extra_max_tokens_nonneg
            CHECK (extra_max_tokens >= 0),
          ADD CONSTRAINT ck_bots_extra_output_tokens_per_response_nonneg
            CHECK (extra_output_tokens_per_response >= 0)
        """
    )

    op.create_index(
        "ix_bots_tokens_used",
        "bots",
        ["tokens_used"],
    )


def downgrade() -> None:
    op.drop_index("ix_bots_tokens_used", table_name="bots")

    op.execute(
        """
        ALTER TABLE bots
          DROP CONSTRAINT IF EXISTS ck_bots_extra_output_tokens_per_response_nonneg,
          DROP CONSTRAINT IF EXISTS ck_bots_extra_max_tokens_nonneg,
          DROP CONSTRAINT IF EXISTS ck_bots_tokens_used_nonneg
        """
    )

    op.execute(
        """
        ALTER TABLE bots
          DROP COLUMN IF EXISTS bypass_token_check,
          DROP COLUMN IF EXISTS extra_output_tokens_per_response,
          DROP COLUMN IF EXISTS extra_max_tokens,
          DROP COLUMN IF EXISTS tokens_used
        """
    )
