"""Extend JSONB intent maps with missing intent keys (range_query, cross_compare).

Revision: 0138
Prev:     0137

Trigger (2026-05-29 master consolidated fix-all plan, Phase 1.5):
  30Q diverse audit + DB scan revealed:

  ``multi_query_enabled_by_intent`` JSONB lacks ``range_query``. The
  ``range_query`` intent (dưới X / từ X đến Y / above Y) is the canonical
  case where multi-query fanout DOES help: rephrasing as "list prices
  below X", "prices under X", "lowest X" recovers chunks the literal
  phrasing misses. Missing key means the runtime falls back to the global
  ``multi_query_enabled`` toggle without per-intent gating.

  ``crag_min_fallback_score_by_intent`` lacks ``cross_compare``.
  Cross-service comparison queries (A vs B, both services co-existing)
  need a lower CRAG floor than aggregation because the grader's
  per-chunk scoring penalises chunks that contain only ONE side of the
  comparison even though both sides combined answer the question.

  ``comparison`` in multi_query map intentionally LEFT FALSE — that was a
  per-bot policy decision (memory project_v15_stream_z_done.md) and
  flipping it platform-wide would regress HALLU=0 sacred. Owners that
  want comparison fanout enable it via plan_limits override.

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral (intent labels are RAG-architecture concepts)
  ✅ Per-bot override remains tier 1 (bots.plan_limits JSONB)
  ✅ Reversible (downgrade removes the keys we added)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0138"
down_revision: str | None = "0137"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add missing intent keys to two existing JSONB maps."""
    # multi_query_enabled_by_intent: add range_query=true (fanout helps).
    # ``||`` is right-biased merge — existing keys (incl. comparison=false)
    # are preserved; we only fill the gap.
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = value || '{"range_query": true}'::jsonb,
                updated_at = NOW()
            WHERE key = 'multi_query_enabled_by_intent'
              AND NOT (value ? 'range_query')
            """,
        ),
    )

    # crag_min_fallback_score_by_intent: add cross_compare=0.20.
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = value || '{"cross_compare": 0.20}'::jsonb,
                updated_at = NOW()
            WHERE key = 'crag_min_fallback_score_by_intent'
              AND NOT (value ? 'cross_compare')
            """,
        ),
    )


def downgrade() -> None:
    """Strip the keys we added (preserves other operator edits)."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = value - 'range_query',
                updated_at = NOW()
            WHERE key = 'multi_query_enabled_by_intent'
            """,
        ),
    )
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = value - 'cross_compare',
                updated_at = NOW()
            WHERE key = 'crag_min_fallback_score_by_intent'
            """,
        ),
    )
