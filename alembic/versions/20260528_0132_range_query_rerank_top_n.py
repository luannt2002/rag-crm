"""Add range_query to rerank_top_n_by_intent (extend Phase 3 commit 4289687).

Revision: 0132
Prev:     0131

Trigger (verified 2026-05-28 manual verify):
  Q4 "Giá triệt lông 100K-700K có những vùng nào?" expected 7 vùng, bot
  returned 3 (Mép/Mặt/Nách).

Root cause (verified after git history audit):
  Phase 3 of plan 260521-CHUNK-AGGREGATION-UNIVERSAL (commit 4289687) already
  shipped per-intent rerank_top_n_by_intent. Current DB value:
    aggregation: 20  ← shipped
    comparison: 12
    multi_hop: 12
    factoid: 7
    chitchat/feedback/greeting/vu_vo/out_of_scope: 5
  But `range_query` MISSING → falls back to global default rag_rerank_top_n=10.
  Range queries (list items in price/numeric range) need wider top_n similar
  to aggregation — 10 chunks insufficient for 7+ items in range.

Fix: extend the same Phase 3 pattern — add range_query=15 (mid-way between
factoid 7 and aggregation 20). Not re-architecting; just filling missing
key in existing config knob.

Sacred-rule alignment:
  ✅ Leverages existing pattern (commit 4289687)
  ✅ Pure DB UPDATE via alembic (rule 7)
  ✅ Per-bot override preserved (plan_limits.rerank_top_n_by_intent)
  ✅ Reversible (downgrade removes the key)
"""

from alembic import op
from sqlalchemy import text

revision: str = "0132"
down_revision: str | None = "0131"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add range_query=15 to rerank_top_n_by_intent."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = jsonb_set(
                CAST(value AS jsonb),
                '{range_query}',
                '15'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE key = 'rerank_top_n_by_intent'
            """
        ),
    )


def downgrade() -> None:
    """Remove range_query key (revert)."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = (CAST(value AS jsonb) - 'range_query'),
                updated_at = NOW()
            WHERE key = 'rerank_top_n_by_intent'
            """
        ),
    )
