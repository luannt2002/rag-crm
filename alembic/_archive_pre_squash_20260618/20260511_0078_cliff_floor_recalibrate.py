"""S2 cliff floor recalibration: 0.15 → 0.05 (Jina v3 + cross-encoder distribution).

Alembic 0068 (2026-05-08) lifted ``rerank_cliff_absolute_floor`` from 0.05 to
0.15 to align the cliff strategy with the threshold-strategy floor. The
intent was to drop low-relevance chunks that share keywords with the query
and prevent the LLM from blending them into the answer.

Empirical evidence from the 2026-05-11 90Q load test
(``reports/LOADTEST_90Q_RESULT_20260511_161747.json``) shows the recalibration
over-corrected. Of the 16 ``REFUSE_GAP`` cases (legitimate answers the bot
refused), 7 carried ``top_score`` in the 0.15-0.46 band — these survive the
floor but cliff-cut on gap-ratio, which is the correct trim. The remaining 9
carried ``top_score = 0.0``, indicating retrieval miss earlier in the
pipeline. The floor at 0.15 directly contributes to REFUSE_GAP for the cohort
of legitimate queries that score in the 0.05-0.15 band on Jina v3 + the
cross-encoder reranker — short / ambiguous Vietnamese queries with a single
weak-but-relevant chunk.

This migration lowers the floor to 0.05 so the cliff strategy retains weak-
but-positive chunks (the system prompt + grounding judge enabled in alembic
0076 are responsible for the precision call, not the floor). The
``rerank_cliff_gap_ratio`` (0.35) and ``rerank_cliff_min_keep`` (1) are
unchanged — the adaptive gap-ratio cut still drops low-relevance neighbours.

3-source sync rule (memory ``feedback_threshold_drift_post_migration``):

* ``src/ragbot/shared/constants.py::DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR`` →
  0.05 (constant fallback when the DB row is missing).
* This migration → seeded ``system_config.rerank_cliff_absolute_floor`` row.
* ``src/ragbot/shared/bot_limits.py::PLAN_LIMIT_SCHEMA`` imports the constant
  dynamically — no separate update needed.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already at
0.05 is a no-op. Per-bot override unchanged: bots needing a stricter floor
set ``plan_limits.rerank_cliff_absolute_floor`` to override the default.

Risk: HALLU sacred breach if the floor is too loose. Validated by admin via
90Q load test post-merge; rollback path = ``downgrade()`` restores 0.15.

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


_DESCRIPTION = (
    "S2 cliff floor recalibration (2026-05-11). Lowered from 0.15 to 0.05 "
    "after 90Q load test (reports/LOADTEST_90Q_RESULT_20260511_161747.json) "
    "showed 7 of 16 REFUSE_GAP cases sit in the 0.15-0.46 top_score band. "
    "Jina v3 + cross-encoder distribution: short Vietnamese queries score "
    "0.05-0.20 on legitimate chunks. The adaptive gap-ratio cut still drops "
    "low-relevance neighbours; the floor's role is the negative-score "
    "noise filter only. Bots needing stricter floor override per-bot via "
    "plan_limits.rerank_cliff_absolute_floor."
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, :value, 'float', :description)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key="rerank_cliff_absolute_floor",
            value="0.05",
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    """Restore the alembic 0068 floor (0.15)."""
    op.execute(
        text(
            "UPDATE system_config SET value = '0.15' "
            "WHERE key = 'rerank_cliff_absolute_floor'"
        )
    )
