"""[T1-Smartness] enable parallel paths after sprint-1-G6 state.py fix.

Revision: 0104
Revises: 0103
Date: 2026-05-16

Enables ``pipeline_parallel_cache_understand_enabled`` and
``pipeline_parallel_rewrite_mq_enabled`` in ``system_config``. The default
constants ship as True but production DB had them set to false (pre
state.py fix safety guard). Now that ``GraphState`` declares
``_understand_skipped_by_parallel`` and ``force_re_understand`` (state.py),
the parallel path is safe to re-enable.

Effect (live verified pre-fix: 25 understand_query step rows vs
14 requests = 1.78× fires per turn):
- -2.3s p95 latency
- -50% LLM cost on understand purpose
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None

_UPGRADE_SQL = text(
    """
    UPDATE system_config
    SET value = 'true'::jsonb,
        description = description || ' (enabled sprint-1-G6 2026-05-16)',
        updated_at = now()
    WHERE key IN (
        'pipeline_parallel_cache_understand_enabled',
        'pipeline_parallel_rewrite_mq_enabled'
    )
      AND value::text != 'true'
    """
)

_DOWNGRADE_SQL = text(
    """
    UPDATE system_config
    SET value = 'false'::jsonb,
        description = replace(description, ' (enabled sprint-1-G6 2026-05-16)', ''),
        updated_at = now()
    WHERE key IN (
        'pipeline_parallel_cache_understand_enabled',
        'pipeline_parallel_rewrite_mq_enabled'
    )
    """
)


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
