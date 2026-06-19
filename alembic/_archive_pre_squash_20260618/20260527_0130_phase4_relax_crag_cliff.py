"""Phase 4 — relax CRAG fallback + bump rerank cliff min_keep (weak categories).

Revision: 0130
Prev:     0129

Phase 4 root cause (RAGAS LLM judge Round 1, 60Q):
  - 4 weak categories below 75% Good threshold:
      yes_no:      61.1%
      comparison:  63.0%
      range_query: 71.0%
      summary_doc: 71.0%

  - DB trace shows: retrieve returns 20 chunks → rerank zerank-2 → cliff
    keeps top-5 only → CRAG grader rejects half → chunks_used=2-4.
    Eval judge sees only 2-4 chunks for evidence → Faith=0 even when
    bot answered correctly from a chunk that got cut by cliff.

  - For yes_no/summary intents, evidence is OFTEN spread across 6-8
    chunks (multi-paragraph FAQ blocks). cliff min_keep=5 is too tight.

Fix:
  1. Bump rerank_cliff_min_keep 5 → 8 (more chunks survive into CRAG)
  2. Add per-intent crag_min_fallback_score for yes_no/summary_doc
     /range_query at 0.25 (vs default 0.3) — bot has more partial
     evidence to compose answer.

Trade-off:
  - Latency: +0.3s avg (CRAG grades 8 vs 5 chunks)
  - Faith: +5-10pp expected on 3 weak categories
  - HALLU=0 sacred unchanged (sysprompt v6 anti-fake-section + Rule 12
    enumeration-strict still enforce ground-truth-only).

Sacred-rule alignment:
✅ Pure DB UPDATE via alembic (zero hardcode)
✅ Per-intent flag already wired in _crag_min_fallback_for_intent helper
✅ Reversible: downgrade restores 5 / 0.3
"""

from alembic import op
from sqlalchemy import text

revision: str = "0130"
down_revision: str | None = "0129"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Bump min_keep 5→8 + add yes_no/summary_doc/range_query intent thresholds."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = '8'::jsonb,
                updated_at = NOW()
            WHERE key = 'rerank_cliff_min_keep'
            """
        ),
    )
    # Add yes_no/summary_doc/range_query at 0.25; keep existing intents.
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = jsonb_set(
                jsonb_set(
                    jsonb_set(
                        CAST(value AS jsonb),
                        '{yes_no}',
                        '0.25'::jsonb,
                        true
                    ),
                    '{summary_doc}',
                    '0.25'::jsonb,
                    true
                ),
                '{range_query}',
                '0.25'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE key = 'crag_min_fallback_score_by_intent'
            """
        ),
    )


def downgrade() -> None:
    """Restore min_keep=5 + remove the 3 intent overrides."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = '5'::jsonb,
                updated_at = NOW()
            WHERE key = 'rerank_cliff_min_keep'
            """
        ),
    )
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = (CAST(value AS jsonb)
                            - 'yes_no'
                            - 'summary_doc'
                            - 'range_query'),
                updated_at = NOW()
            WHERE key = 'crag_min_fallback_score_by_intent'
            """
        ),
    )
