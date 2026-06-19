"""add feature_name to model_invocations — per-feature cost rollup.

Revision ID: 0094
Revises: 0093
Create Date: 2026-05-14

Adds ``feature_name VARCHAR(64)`` to ``model_invocations`` so cost audit can
group LLM/embed/rerank calls by the high-level feature / subsystem that
issued them (e.g. ``query.generation``, ``ingest.enrich``,
``router.classify``).

Why a new column rather than reusing ``purpose``:
  * ``purpose`` is a logical pipeline-stage tag (``generation``,
    ``rewrite``, ``grader``) — cardinality ~10, low granularity.
  * ``feature_name`` is a product-level tag for cost attribution
    (``query.generation``, ``ingest.enrich.parse``, ``observe.narrate``)
    — cardinality ~30, the dimension product owners actually budget on.

Column:
  * ``feature_name VARCHAR(64) NULL`` — nullable; legacy rows + callers
    not yet threading the kwarg show up as ``NULL`` and roll up under
    the ``unset`` bucket in cost audit (constant
    ``DEFAULT_FEATURE_NAME_UNSET``).

Index ``ix_model_inv_feature_started`` supports the common
``feature_name + time-range`` audit query without forcing a partial seq
scan.
"""
from __future__ import annotations

from alembic import op


revision = "0094a"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE model_invocations
          ADD COLUMN IF NOT EXISTS feature_name VARCHAR(64)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_model_inv_feature_started
          ON model_invocations (feature_name, started_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_model_inv_feature_started")
    op.execute(
        """
        ALTER TABLE model_invocations
          DROP COLUMN IF EXISTS feature_name
        """
    )
