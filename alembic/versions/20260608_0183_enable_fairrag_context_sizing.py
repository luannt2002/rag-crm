"""Enable FAIR-RAG gap-retry + adaptive context-sizing (A/B-validated).

Revision: 0183
Prev:     0182

Plan 260608 (PATH_TO_9.5) Phase 1 + 3a. A/B (RAGAS-judge, 2026-06-08) on the
validated levers WITH the aggregation/comparison exemption in code
(DEFAULT_ADAPTIVE_CONTEXT_EXEMPT_INTENTS):

  bot        OFF    ON(no-exempt)   ON(+exempt, this)
  spa        0.77   0.61 (regress)  0.77 (recovered)
  lich-su    0.57   0.63            0.76
  sinh-hoc   0.88   0.92            —

Net-positive, zero regression once aggregation/comparison are exempted from
context pruning (they need every price/compare row). FAIR-RAG only fires on the
CRAG-retry path (rare) and anchors the retry on the grader's missing_facets.

Sets the two toggles ON platform-wide. Per-bot override via plan_limits
(PLAN_LIMIT_SCHEMA). The CODE keeps the exemption + the high-score gate, so a
strong retrieval is never turned into an answer gap. Rule 7 (alembic). Rule #0
(A/B-measured before default). Reversible.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0183"
down_revision: str | None = "0182"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

# adaptive_context_enabled = TRUE: A/B-validated net-positive (spa neutral,
# lich-su +0.19, sinh-hoc +0.04) WITH the aggregation/comparison exemption in
# code. (The FAIR-RAG gap-retry lever was prototyped here but removed — it
# bypassed the language_packs DB-prompt contract and was never validated.)
_LEVERS = {
    "adaptive_context_enabled": "true",
}


def upgrade() -> None:
    for k, v in _LEVERS.items():
        op.execute(
            text("""
                INSERT INTO system_config (key, value, value_type, description, updated_at)
                VALUES (:k, CAST(:v AS jsonb), 'bool',
                        'Adaptive context-sizing — A/B-validated ON (plan 260608).',
                        NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = CAST(:v AS jsonb), value_type = 'bool',
                    description = EXCLUDED.description, updated_at = NOW()
            """).bindparams(k=k, v=v)
        )
    # Clean up the prototype FAIR-RAG gap-retry toggle (feature removed).
    op.execute(text("DELETE FROM system_config WHERE key = 'crag_emit_gap_enabled'"))


def downgrade() -> None:
    op.execute(text("DELETE FROM system_config WHERE key = ANY(:keys)").bindparams(keys=list(_LEVERS)))
