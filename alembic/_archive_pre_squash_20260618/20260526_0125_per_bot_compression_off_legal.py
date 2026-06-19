"""Per-bot prompt_compression OFF cho legal long-form bots.

Revision: 0125
Prev:     0124

Eval round 1 traced: chunk Điều 16 (1232ch, 6 mục) compressed to 500ch →
keep only 2 mục → bot answers "2 mục" (truncation bug, NOT hallucination).

Fix per-bot via plan_limits JSONB (multi-tenant safe):
  - tessss (Thông tư 09/2020 NHNN, 101 chunks): compression OFF
  - thong-tu-09-2020-tt-nhnn: compression OFF
  - test-spa-id (FAQ + price tables, short chunks 200-400ch): keep ON
    (chunks fit under 500ch cap, no truncation; saves cost)

Per-bot override pattern (already wired in pipeline_config builder):
  bots.plan_limits.prompt_compression_enabled = false
    overrides
  system_config.prompt_compression_enabled = true (global)

Sacred-rule alignment:
  - 4-key identity: per-bot override scoped correctly
  - Multi-tenant: each bot opts-in/out independently
  - Reversible: downgrade clears plan_limits override
"""

from alembic import op
from sqlalchemy import text

revision: str = "0125"
down_revision: str | None = "0124"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Turn compression OFF cho legal bot via plan_limits."""
    op.execute(
        text(
            """
            UPDATE bots
            SET plan_limits = jsonb_set(
                COALESCE(plan_limits, '{}'::jsonb),
                '{prompt_compression_enabled}',
                'false'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE bot_id IN ('tessss', 'thong-tu-09-2020-tt-nhnn', 'legalbot')
              AND is_deleted = false
            """
        ),
    )


def downgrade() -> None:
    """Remove per-bot override (falls back to system_config default)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET plan_limits = plan_limits - 'prompt_compression_enabled',
                updated_at = NOW()
            WHERE bot_id IN ('tessss', 'thong-tu-09-2020-tt-nhnn', 'legalbot')
              AND is_deleted = false
            """
        ),
    )
