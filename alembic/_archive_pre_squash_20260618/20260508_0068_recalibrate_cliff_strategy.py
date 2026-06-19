"""Formalize cliff strategy + lifted absolute floor for fresh deployments.

The 2026-05-07 silent-bot fix bundle changed two reranker defaults via
direct ``UPDATE system_config`` against the live UAT database:

* ``rerank_filter_strategy = 'cliff'`` — strategy switch from threshold
  so the filter never returns an empty list when input was non-empty
  (cliff's ``force_min_keep=True`` preserves at least one chunk).
* ``rerank_cliff_absolute_floor = 0.15`` — floor lifted from 0.05 so
  cliff drops low-relevance chunks that share keywords with the query
  and otherwise let the LLM blend them into the answer.

Both rows existed in the live DB from the original seeds and the
constant fallback never fires once a row exists. This migration is the
formal channel: fresh deployments inherit the recalibrated values,
and operators reading ``system_config`` see the change reason in the
``description`` column rather than chasing a chat log.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB
already at the new values is a no-op.

Revision ID: 0068
Revises: 0067
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "rerank_filter_strategy",
        '"cliff"',
        "string",
        "Cliff-detect strategy with force_min_keep=True prevents zero-chunks "
        "short-circuit. Bots preferring strict-cut behaviour override per-bot "
        "via threshold_overrides.rerank_filter_strategy='threshold'.",
    ),
    (
        "rerank_cliff_absolute_floor",
        "0.15",
        "float",
        "Floor lifted from 0.05 to match the threshold-strategy floor. A 0.05 "
        "floor lets cliff retain low-relevance chunks that share keywords "
        "with the query and the LLM blends them into the answer, fabricating "
        "numbers that don't exist for the actual service.",
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
    """Restore the prior threshold-strategy + 0.05 floor pair."""
    op.execute(
        text(
            "UPDATE system_config SET value = '\"threshold\"' "
            "WHERE key = 'rerank_filter_strategy'"
        )
    )
    op.execute(
        text(
            "UPDATE system_config SET value = '0.05' "
            "WHERE key = 'rerank_cliff_absolute_floor'"
        )
    )
