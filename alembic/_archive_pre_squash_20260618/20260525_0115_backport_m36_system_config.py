"""[T3-Refactor] 0115 — Backport Wave M3.6 system_config drift keys.

Revision ID: 0115
Revises: 0114
Create Date: 2026-05-25

Wave M3.6 (commit d10bf19, 2026-05-20) shipped 4 system_config keys via
**psql UPSERT** outside any alembic migration — DB drift. Re-cloning the
DB from alembic history alone misses these keys; production parity
breaks silently. This migration backports the keys with their
live-production values so the DB state is fully reproducible from
alembic head.

Per CLAUDE.md Application MINDSET rule 7 (added 2026-05-25): **CẤM
HOT-FIX qua psql UPDATE** vào ``system_config.value``. Mọi DB content
state đi qua alembic HOẶC admin UI có audit_log trail. This migration
closes the existing drift; future drift = pre-commit / runbook break.

Backported keys (values lifted from production 2026-05-25):

* ``speculative_streaming_enabled = true`` (M3.6 F1) — flips Apple-paper
  speculative streaming chain ON (TTFB -50% verified Wave L1/L3).
* ``grounding_check_async_enabled = true`` (M3.6 G3) — moves the
  grounding gate off the serial generate path.
* ``pipeline_parallel_output_guards_enabled = true`` (M3.6 V1) —
  parallelises the output-guard step set (-100-200ms).
* ``grounding_check_threshold_by_intent`` (M3.6 L4) — per-intent
  grounding threshold map; comparison/multi_entity loosened to 0.4
  (default keeps 0.5).

Idempotent: every row uses INSERT ... ON CONFLICT (key) DO UPDATE so
re-running on a DB that already has the live values is a no-op.

Downgrade: DELETE the four keys. Server behaviour reverts to the
pre-M3.6 defaults (speculative OFF, grounding sync, sequential guards,
default 0.5 threshold for every intent).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision: str = "0115"
down_revision: str | None = "0114"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_BACKPORT_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "speculative_streaming_enabled",
        "true",
        "bool",
        "Wave M3.6 F1 — Apple-paper speculative streaming TTFB optimisation.",
    ),
    (
        "grounding_check_async_enabled",
        "true",
        "bool",
        "Wave M3.6 G3 — async grounding gate off serial generate path.",
    ),
    (
        "pipeline_parallel_output_guards_enabled",
        "true",
        "bool",
        "Wave M3.6 V1 — parallelise output guard step set (-100-200ms).",
    ),
    (
        "grounding_check_threshold_by_intent",
        '{"comparison": 0.4, "multi_entity": 0.4}',
        "jsonb",
        "Wave M3.6 L4 — per-intent grounding threshold map; "
        "comparison/multi_entity loosened to 0.4 (default keeps 0.5).",
    ),
)


def upgrade() -> None:
    """Backport four M3.6 keys with production-current values."""
    for key, value_json, value_type, description in _BACKPORT_ROWS:
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description, updated_at)
                VALUES (:key, CAST(:value AS jsonb), :value_type, :description, NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description,
                    updated_at = NOW()
                """
            ).bindparams(
                key=key,
                value=value_json,
                value_type=value_type,
                description=description,
            ),
        )


def downgrade() -> None:
    """Remove the four backported keys; server reverts to pre-M3.6 defaults."""
    op.execute(
        text(
            """
            DELETE FROM system_config
            WHERE key IN (
                'speculative_streaming_enabled',
                'grounding_check_async_enabled',
                'pipeline_parallel_output_guards_enabled',
                'grounding_check_threshold_by_intent'
            )
            """
        )
    )
