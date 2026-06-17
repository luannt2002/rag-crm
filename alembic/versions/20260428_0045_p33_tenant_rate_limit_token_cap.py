"""P33 + C.5 — Per-tenant rate-limit & monthly token cap columns.

Adds three columns to ``tenants``:

* ``bypass_rate_limit BOOLEAN NOT NULL DEFAULT FALSE`` — Luồng A bypass
  (platform-admin set, applies to ALL bots of the tenant). Distinct from
  ``bots.bypass_rate_limit`` (Luồng B, P18-5) which is per-bot. The
  middleware OR-gathers both.
* ``rate_limit_per_min INT NULL`` — per-tenant Layer-1 override. NULL =
  use ``system_config.tenant_rate_limit_per_min`` (defaults to
  ``DEFAULT_TENANT_RATE_LIMIT_PER_MIN``). 0 = soft-unlimited. Positive =
  custom limit.
* ``monthly_token_cap INT NULL`` — C.5 hard cap on prompt+completion
  tokens per calendar month. NULL = no cap. 0 = block (admin can lock
  account immediately). Positive = limit.

Defensive: skips column add if it already exists, so re-applying the
migration on a partially-stamped DB is safe.

Revision: 0045
Down revision: 0044
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ).bindparams(t=table, c=column)
    )
    return bool(result.scalar())


def upgrade() -> None:
    if not _column_exists("tenants", "bypass_rate_limit"):
        op.add_column(
            "tenants",
            sa.Column(
                "bypass_rate_limit",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    if not _column_exists("tenants", "rate_limit_per_min"):
        op.add_column(
            "tenants",
            sa.Column("rate_limit_per_min", sa.Integer(), nullable=True),
        )
    if not _column_exists("tenants", "monthly_token_cap"):
        op.add_column(
            "tenants",
            sa.Column("monthly_token_cap", sa.Integer(), nullable=True),
        )

    # CHECK constraints — non-negative values only.
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS "
        "ck_tenants_rate_limit_per_min_nonneg"
    )
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT "
        "ck_tenants_rate_limit_per_min_nonneg CHECK "
        "(rate_limit_per_min IS NULL OR rate_limit_per_min >= 0)"
    )
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS "
        "ck_tenants_monthly_token_cap_nonneg"
    )
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT "
        "ck_tenants_monthly_token_cap_nonneg CHECK "
        "(monthly_token_cap IS NULL OR monthly_token_cap >= 0)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS "
        "ck_tenants_monthly_token_cap_nonneg"
    )
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS "
        "ck_tenants_rate_limit_per_min_nonneg"
    )
    if _column_exists("tenants", "monthly_token_cap"):
        op.drop_column("tenants", "monthly_token_cap")
    if _column_exists("tenants", "rate_limit_per_min"):
        op.drop_column("tenants", "rate_limit_per_min")
    if _column_exists("tenants", "bypass_rate_limit"):
        op.drop_column("tenants", "bypass_rate_limit")
