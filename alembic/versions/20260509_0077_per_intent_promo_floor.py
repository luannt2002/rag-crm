"""Wave-2 Cluster C1 — per-intent promo/sale/voucher fallback floor + factoid bump.

Wave-2 90Q load tests (post-22-task,
``reports/LOADTEST_POST_22TASKS_20260509_025928.json`` Q7 Black Friday)
identified pricing/promo questions as the dominant HALLU CONFLATE
contributor: top_score 0.181 lands inside the gray zone between
``DEFAULT_RERANKER_MIN_SCORE_ACTIVE`` (0.15) and
``DEFAULT_CRAG_MIN_FALLBACK_SCORE`` (0.30) — the chunk passes the rerank
filter, the CRAG grader marks it AMBIGUOUS, and the LLM downstream
confabulates a Black Friday promo by conflating combo pricing.

Two defences ship together:

1. ``DEFAULT_GROUNDING_CHECK_ENABLED`` flipped True (Cluster C2,
   alembic 0076) — the LLM-judge demotes ungrounded answers to refuse
   on every ANSWERED + factoid/comparison/aggregation/multi_hop turn.
2. This migration tightens the fallback floor for the gray-zone case:
   - factoid floor 0.30 → 0.35 (active — the classifier emits this label)
   - promo / sale / voucher = 0.40 (forward-compat — dormant until the
     intent classifier or vocabulary router emits these labels)

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running the migration
on a DB already at the new value is a no-op.

Revision ID: 0077
Revises: 0076
Create Date: 2026-05-09
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


_NEW_INTENT_FLOOR: dict[str, float] = {
    "factoid": 0.35,
    "comparison": 0.20,
    "multi_hop": 0.15,
    "aggregation": 0.20,
    "out_of_scope": 0.30,
    "greeting": 0.30,
    "feedback": 0.30,
    "promo": 0.40,
    "sale": 0.40,
    "voucher": 0.40,
}

_PRIOR_INTENT_FLOOR: dict[str, float] = {
    "factoid": 0.30,
    "comparison": 0.20,
    "multi_hop": 0.15,
    "aggregation": 0.20,
    "out_of_scope": 0.30,
    "greeting": 0.30,
    "feedback": 0.30,
}

_DESCRIPTION = (
    "Per-intent CRAG fallback score floor. factoid bumped 0.30→0.35 "
    "to harden the gray zone (top_score 0.18..0.30). promo/sale/voucher "
    "0.40 are forward-compatible dormant gates pending intent classifier "
    "extension; the active HALLU defence on those topics is the "
    "grounding judge enabled in alembic 0076."
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, :value, 'json', :description)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key="crag_min_fallback_score_by_intent",
            value=json.dumps(_NEW_INTENT_FLOOR, sort_keys=True),
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    """Restore the prior baseline (factoid 0.30, no promo/sale/voucher keys)."""
    op.execute(
        text(
            "UPDATE system_config "
            "SET value = :value, "
            "    description = 'Per-intent CRAG fallback score floor (baseline).' "
            "WHERE key = 'crag_min_fallback_score_by_intent'"
        ).bindparams(value=json.dumps(_PRIOR_INTENT_FLOOR, sort_keys=True))
    )
