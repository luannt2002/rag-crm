"""Enable LLM-judge grounding check by default for retrieval intents.

The grounding judge (``llm_grounding_check`` in ``infrastructure/guardrails/
local_guardrail.py``) was wired in 2026-Q2 but shipped OFF
(``DEFAULT_GROUNDING_CHECK_ENABLED = False``) while the eval harness
warmed up. Wave-2 load tests (Cluster B 90Q × 3 round) showed the
factoid + comparison intents are the dominant HALLU contributors —
weak retrieval (top_score 0.18..0.30 gray zone) survives rerank cliff
and the LLM downstream confabulates rather than refuses. The grounding
judge re-checks every ANSWERED turn against retrieved chunks and demotes
ungrounded answers to refuse.

The constant flipped to ``True`` in ``shared/constants.py``; this
migration aligns deployments that already wrote ``grounding_check_enabled
= false`` into ``system_config`` (``init_system_config.py`` historical
seed) so operators inherit the new default without manual intervention.
Per-bot opt-out remains via ``bots.plan_limits.grounding_check_enabled``
(precedence: bot column > plan_limits > system_config > schema default).

Intent gate ``DEFAULT_GROUNDING_INTENTS`` already restricts the judge to
``{factoid, comparison, aggregation, multi_hop}`` (Stream S2-PERF Lever 4)
so chitchat / greeting / OOS turns pay zero tail latency.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running the migration
on a DB already at the new value is a no-op.

Revision ID: 0076
Revises: 0074
Create Date: 2026-05-09
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0076"
down_revision = "0074"
branch_labels = None
depends_on = None


_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "grounding_check_enabled",
        "true",
        "bool",
        "Enable LLM-judge grounding check on guard_output. Gated by "
        "DEFAULT_GROUNDING_INTENTS (factoid/comparison/aggregation/"
        "multi_hop) so non-retrieval intents pay no tail latency.",
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
    """Restore the prior OFF default (grounding judge disabled)."""
    op.execute(
        text(
            "UPDATE system_config SET value = 'false' "
            "WHERE key = 'grounding_check_enabled'"
        )
    )
