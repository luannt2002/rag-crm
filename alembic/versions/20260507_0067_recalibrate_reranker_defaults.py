"""Recalibrate live ``system_config`` for reranker after Jina v3 migration.

The seeded ``reranker_min_score_active`` value (``0.4``) mirrored Cohere's
published "low relevance" boundary. After the migration to Jina v3 the
cross-encoder score distribution differs and the 0.4 floor turned into
a precision trap that silently dropped every chunk for short / ambiguous
queries — bot returned an empty answer with no LLM call. The constant
in ``shared/constants.py`` and the PLAN_LIMIT_SCHEMA default both moved
to 0.15; this migration aligns the live DB row so existing deployments
inherit the safer floor without re-running ``init_system_config.py``.

``rerank_filter_strategy`` is not seeded explicitly in
``init_system_config.py`` — the constant default (now ``cliff``) wins.
This migration leaves the row absent so the new default is honoured;
operators that prefer the strict-cut ``threshold`` strategy override
per-bot via ``bots.threshold_overrides.rerank_filter_strategy``.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running the migration
on a DB already at the new value is a no-op.

Revision ID: 0067
Revises: 0066
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "reranker_min_score_active",
        "0.15",
        "float",
        "Min score floor when reranker is ACTIVE (cross-encoder 0..1). "
        "Matches PLAN_LIMIT_SCHEMA default — bots can raise via "
        "threshold_overrides for stricter cuts.",
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _TUNING_ROWS:
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, :value, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    """Restore the prior Cohere-tuned floor (0.4)."""
    op.execute(
        text(
            "UPDATE system_config SET value = '0.4' "
            "WHERE key = 'reranker_min_score_active'"
        )
    )
