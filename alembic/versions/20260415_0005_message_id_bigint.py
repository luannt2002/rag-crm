"""v0.3.0 — widen `message_id` INTEGER → BIGINT (future-proof).

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-15

Upstream service currently emits ~180k for message_id and is auto-incrementing.
INTEGER (2.1B max) is enough for ~5 years at 1M/day, but bumping to BIGINT
now costs 4 bytes/row and removes the overflow risk forever. No data coercion
needed — Postgres widens INTEGER → BIGINT losslessly.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ALTER COLUMN message_id TYPE BIGINT"
    )
    # feedback may not exist on clean DB (dropped in 0010)
    op.execute(
        f"ALTER TABLE IF EXISTS {SCHEMA}.feedback "
        "ALTER COLUMN message_id TYPE BIGINT"
    )


def downgrade() -> None:
    # Only safe if no row exceeds INT max — check before running.
    op.execute(
        f"ALTER TABLE {SCHEMA}.feedback "
        "ALTER COLUMN message_id TYPE INTEGER"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ALTER COLUMN message_id TYPE INTEGER"
    )
