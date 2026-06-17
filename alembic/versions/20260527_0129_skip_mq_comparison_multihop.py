"""Skip multi_query for comparison + multi_hop intents (latency fix).

Revision: 0129
Prev:     0128

Phase 3 latency root cause (Round 3 evidence):
  comparison: avg 18.6s, max 55.5s (outlier)
  multi_hop:  avg 16.5s, max 33s

  Trace: multi_query_fanout step ~2-3s × 3 variants Haiku (parallel max),
  but comparison/multi_hop generate prompt is already LONG (context_cap
  bumped to 6000ch). 3 retrieve parallel → noise chunks → LLM "phân tâm"
  → tail latency.

Fix: turn OFF multi_query for these 2 intents. Bot still benefits from:
  - Top_k bump (25 → comparison, 30 → multi_hop) for breadth
  - Reranker zerank-2 for quality cut
  - LITM reorder for lost-in-middle mitigation

Trade-off:
  - Recall: -2-3pp (no parallel variant retrieve)
  - Latency: -3-5s avg, -20s tail (no multi_query waste)
  - Quality NET: positive because variants were adding noise more than signal

Sacred-rule alignment:
✅ Pure DB UPDATE via alembic (zero hardcode)
✅ Per-intent flag already wired (line 5532 pipeline reads via _pcfg)
✅ Reversible: downgrade flips back true
"""

from alembic import op
from sqlalchemy import text

revision: str = "0129"
down_revision: str | None = "0128"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Set multi_query_enabled_by_intent.comparison + multi_hop = false."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = jsonb_set(
                jsonb_set(
                    CAST(value AS jsonb),
                    '{comparison}',
                    'false'::jsonb,
                    true
                ),
                '{multi_hop}',
                'false'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE key = 'multi_query_enabled_by_intent'
            """
        ),
    )


def downgrade() -> None:
    """Re-enable multi_query for comparison + multi_hop."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = jsonb_set(
                jsonb_set(
                    CAST(value AS jsonb),
                    '{comparison}',
                    'true'::jsonb,
                    true
                ),
                '{multi_hop}',
                'true'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE key = 'multi_query_enabled_by_intent'
            """
        ),
    )
