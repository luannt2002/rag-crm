"""[T1-Smartness] S1 Pipeline-Opt — raise CRAG smart-skip default 0.0 -> 0.7.

The smart-skip CRAG knob was seeded by migration 0084 with value ``0.0``
(disabled — legacy behaviour). Production trace
``fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4`` showed a turn with
``top_score=0.91`` wasted **10683ms** on a CRAG rewrite + retrieve + grade
retry that produced the same answer set.

S1 fix flips the default to ``0.7`` so the gate is ENABLED out of the box:

* Grade node short-circuits when pass-1 top reranker score >= 0.7 —
  skips the grade-LLM call AND the rewrite_retry loop.
* HALLU=0 sacred preserved by the downstream ``grounding_check``
  guardrail (still runs on every answer).
* Bot owner can re-disable per-bot via
  ``plan_limits.crag_skip_retry_above_score = 1.1`` (any value > 1.0).
* Bot owner can tighten per-bot via
  ``plan_limits.crag_skip_retry_above_score = 0.85`` etc.

Savings (S1 spec): ~-10s latency / ~-22% cost on requests where pass-1
already cleared the confidence bar.

3-source sync (single source of truth):

* ``src/ragbot/shared/constants.py``
  → ``DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE = 0.7``
* This migration updates the matching ``system_config`` row.
* ``src/ragbot/shared/bot_limits.py::PLAN_LIMIT_SCHEMA`` imports the
  constant — no separate update needed.

Idempotent: ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already
holding any value is safe; downgrade restores legacy ``0.0``.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0087b"
down_revision = "0087a"
branch_labels = None
depends_on = None


_KEY = "crag_skip_retry_above_score"
_NEW_VALUE = "0.7"
_LEGACY_VALUE = "0.0"
_VALUE_TYPE = "float"
_DESCRIPTION = (
    "Smart-skip CRAG grade-LLM call + rewrite_retry when pass-1 top "
    "retrieval score clears this floor. Default 0.7 (S1 Pipeline-Opt: "
    "trace fa7983c2 wasted 10683ms on retry at top_score=0.91). Set > "
    "1.0 to disable. HALLU=0 sacred preserved by grounding_check. Bot "
    "owner overrides per-domain via "
    "plan_limits.crag_skip_retry_above_score."
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, (:value)::jsonb, :value_type, :description)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key=_KEY,
            value=_NEW_VALUE,
            value_type=_VALUE_TYPE,
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    # Restore the legacy disabled default seeded by migration 0084.
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, (:value)::jsonb, :value_type, :description)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value
            """
        ).bindparams(
            key=_KEY,
            value=_LEGACY_VALUE,
            value_type=_VALUE_TYPE,
            description=_DESCRIPTION,
        )
    )
