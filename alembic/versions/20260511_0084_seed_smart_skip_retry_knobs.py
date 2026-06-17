"""[T2-Perf] Seed smart-skip CRAG + reflect retry knobs (default OFF).

Stream P1 fix for p95 latency hot path. 90Q load-test trace (request_steps
table, slowest 35.1s turn) showed:

* CRAG retry loop (rewrite → retrieve → grade) ran 2× full pass for ~17s
  even when pass-1 chunks were already high-confidence.
* Reflect → generate → guard loop added ~5.8s when the answer was
  grounded but the Self-RAG judge requested a rewrite anyway.

Both knobs ship default-OFF (preserves byte-identical legacy behaviour).
Bot owner flips per-domain via ``plan_limits``:

* ``crag_skip_retry_above_score``: float, 0.0 = disabled, 0.3..0.6 = enable
  smart-skip when pass-1 top retrieval score clears the threshold.
* ``reflect_skip_if_grounded``: bool, False = disabled, True = honour the
  answer when the grounding-check guardrail did not fire on pass-1 AND
  the top retrieval score clears the floor.

3-source sync (memory ``feedback_threshold_drift_post_migration``):

* ``src/ragbot/shared/constants.py`` carries the default constants
  (``DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE = 0.0``,
  ``DEFAULT_REFLECT_SKIP_IF_GROUNDED = False``,
  ``DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR = 0.30``).
* This migration seeds the matching ``system_config`` rows.
* ``src/ragbot/shared/bot_limits.py::PLAN_LIMIT_SCHEMA`` imports the
  constants — no separate update needed.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already
seeded is a no-op.

Revision ID: 0083
Revises: 0082
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "crag_skip_retry_above_score",
        "0.0",
        "float",
        (
            "Smart-skip CRAG rewrite_retry when pass-1 top retrieval score "
            "clears this floor. Default 0.0 = disabled (retry fires per "
            "max_grade_retries). Bot owner overrides per-domain via "
            "plan_limits.crag_skip_retry_above_score."
        ),
    ),
    (
        "reflect_skip_if_grounded",
        "false",
        "bool",
        (
            "Smart-skip reflect rewrite when guardrail grounding-check passed "
            "AND top retrieval score >= reflect_skip_top_score_floor. Default "
            "false = disabled (retry fires per max_reflect_retries). Bot owner "
            "overrides per-domain via plan_limits.reflect_skip_if_grounded."
        ),
    ),
    (
        "reflect_skip_top_score_floor",
        "0.30",
        "float",
        (
            "Top retrieval score floor used by the reflect smart-skip gate. "
            "A grounded answer below this floor is still a retry candidate "
            "(thin chunks justify the rewrite pass). Default 0.30 mirrors the "
            "Jina v3 + cross-encoder cliff floor distribution."
        ),
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _SEEDS:
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
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    for key, _value, _value_type, _description in _SEEDS:
        op.execute(
            text("DELETE FROM system_config WHERE key = :key").bindparams(key=key)
        )
