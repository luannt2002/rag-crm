"""Seed rerank_cliff_absolute_floor + mmr_similarity_threshold for clone parity.

Two retrieval defaults drifted between the code constant and the live DB:

  * ``rerank_cliff_absolute_floor`` — constant was 0.05 (a Jina-v3-era
    recalibration), production DB is 0.2 (retuned for the current zerank
    cross-encoder). The constant has been brought to 0.2 in code; this seeds
    the same 0.2 into ``system_config`` so a clone built from migrations only
    (no data dump) resolves the SAME value production does.
  * ``mmr_similarity_threshold`` — constant is 0.98, production DB is 0.88.
    We deliberately do NOT touch the constant here: whether to raise the live
    value to 0.98 is a separate, measured decision (MMR flip). This migration
    only pins the CURRENT production value (0.88) so a fresh clone matches live
    today; the flip, if it ships, will be its own migration.

Both writes are idempotent — ``ON CONFLICT (key) DO NOTHING`` — so on the live
DB (rows already present, set out-of-band) this migration is a pure no-op and
never clobbers an operator's later admin-UI override. On a fresh clone it seeds
the production-parity defaults. This closes the "psql-UPDATE out-of-band drift"
anti-pattern by making the values reproducible from tracked migrations.

Revision ID: seed_cliff_mmr_parity_260709
Revises: innocom_timeout_90s_260708
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from ragbot.shared.constants import DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR

revision = "seed_cliff_mmr_parity_260709"
down_revision = "innocom_timeout_90s_260708"
branch_labels = None
depends_on = None

# Current production value — intentionally the live-parity value, NOT the code
# constant (DEFAULT_MMR_SIMILARITY_THRESHOLD = 0.98). The 0.98 flip is a
# separate measured decision; here we only reproduce what production runs today.
_MMR_SIMILARITY_THRESHOLD_LIVE = 0.88

_CLIFF_KEY = "rerank_cliff_absolute_floor"
_CLIFF_DESC = (
    "Absolute floor for the adaptive rerank cliff cut (negative-relevance "
    "noise gate). Reranker-distribution dependent; 0.2 for the current zerank "
    "cross-encoder. Overridable per-bot via plan_limits."
)
_MMR_KEY = "mmr_similarity_threshold"
_MMR_DESC = (
    "MMR redundancy-suppression similarity threshold. Live production value; "
    "the raise-to-0.98 flip is a separate measured change."
)

_INSERT = text(
    """
    INSERT INTO system_config (key, value, value_type, description)
    VALUES (:key, to_jsonb(CAST(:value AS double precision)), 'float', :description)
    ON CONFLICT (key) DO NOTHING
    """
)


def upgrade() -> None:
    op.execute(
        _INSERT.bindparams(
            key=_CLIFF_KEY,
            value=DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
            description=_CLIFF_DESC,
        )
    )
    op.execute(
        _INSERT.bindparams(
            key=_MMR_KEY,
            value=_MMR_SIMILARITY_THRESHOLD_LIVE,
            description=_MMR_DESC,
        )
    )


def downgrade() -> None:
    # These are platform default rows; a downgrade removes only what this
    # migration would have inserted on a fresh DB. On the live DB the rows
    # pre-date the migration, so a downgrade here would wrongly delete an
    # operator value — leave them in place (no-op) to stay safe.
    pass
