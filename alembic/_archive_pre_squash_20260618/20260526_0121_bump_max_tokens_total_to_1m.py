"""Bump max_tokens_total default 10000 → 1_000_000 + reset tokens_used.

Revision: 0121
Prev:     0120

User mandate 2026-05-26: development phase — quota cap of 10K tokens/period
is hitting on test bots (test-spa-id used 3M cumulative, tessss used 13.5K
already exhausted the cap mid-test). Bump platform default to 1M for the
dev phase + reset ``bots.tokens_used`` to 0 on all active bots so test
sessions can resume.

Scope:
  - ``system_config.max_tokens_total`` 10000 → 1_000_000 (1M)
  - ``bots.tokens_used`` reset → 0 for all bots where is_deleted=false

Rationale: pre-prod test phase needs high headroom for load tests + UI
exploration. Real prod quota should be configured per-bot via
``plan_limits.max_tokens_total`` JSON override when launching a real
tenant. This migration touches the PLATFORM default only.

Sacred rules unaffected:
  - HALLU=0 — quota gate is independent of grounding/guard
  - 4-key identity — no schema change
  - Domain-neutral — no brand literal
  - No psql UPDATE shortcut — this IS the alembic-tracked change

Reversibility: downgrade restores defaults (10K cap, tokens_used unchanged
because we cannot reconstruct the pre-reset values).
"""

from alembic import op
from sqlalchemy import text

revision: str = "0121"
down_revision: str | None = "0120"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Lift platform quota to 1M + reset bot counters to 0."""
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'max_tokens_total',
                CAST('1000000' AS jsonb),
                'int',
                'Platform default token quota per bot per period (1M dev phase). '
                'Override per-bot via bots.plan_limits.max_tokens_total.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = NOW();
            """
        ),
    )
    op.execute(
        text(
            """
            UPDATE bots
            SET tokens_used = 0
            WHERE is_deleted = false;
            """
        ),
    )


def downgrade() -> None:
    """Revert platform quota to 10K. ``tokens_used`` stays at current value."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST('10000' AS jsonb), updated_at = NOW()
            WHERE key = 'max_tokens_total';
            """
        ),
    )
