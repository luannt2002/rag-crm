"""[T2-Perf] Seed skip_understand_for_greeting knobs (default OFF).

Stream B3 — Phase B GA latency optimisation. Greeting / short-query turns
(~15% of traffic) currently still pay the understand_query LLM round-trip
(~1.5s p95) even though the classifier always lands on "greeting" and
condense has no history worth condensing.

This migration seeds the matching ``system_config`` rows so the gate is
runtime-configurable without redeploy. All three knobs ship default-OFF
(``skip_understand_for_greeting = false``) so legacy behaviour is
byte-identical until bot owner opts in via ``plan_limits``:

* ``skip_understand_for_greeting``: bool, feature flag. False = disabled,
  True = enable the short-circuit branches.
* ``understand_skip_below_tokens``: int, short-query threshold.
  ``len(query.split()) <= N`` qualifies as "short".
* ``understand_greeting_patterns``: list[str], anchored regex patterns
  (case-insensitive). Domain-neutral VN + EN defaults.

3-source sync (memory ``feedback_threshold_drift_post_migration``):

* ``src/ragbot/shared/constants.py`` carries the default constants
  (``DEFAULT_SKIP_UNDERSTAND_FOR_GREETING = False``,
  ``DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS = 3``,
  ``DEFAULT_GREETING_PATTERNS = (...)``).
* This migration seeds the matching ``system_config`` rows.
* ``src/ragbot/shared/bot_limits.py::PLAN_LIMIT_SCHEMA`` imports the
  constants — no separate update needed.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already
seeded is a no-op.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-12
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

from ragbot.shared.constants import (
    DEFAULT_GREETING_PATTERNS,
    DEFAULT_SKIP_UNDERSTAND_FOR_GREETING,
    DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS,
)


revision = "0087e"
down_revision = "0087d"
branch_labels = None
depends_on = None


_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "skip_understand_for_greeting",
        json.dumps(DEFAULT_SKIP_UNDERSTAND_FOR_GREETING),
        "bool",
        (
            "Bypass the understand_query LLM call when the user message is a "
            "short query (≤ understand_skip_below_tokens) or matches one of "
            "understand_greeting_patterns. Default false = disabled (legacy "
            "LLM-driven understand). Bot owner overrides per-domain via "
            "plan_limits.skip_understand_for_greeting."
        ),
    ),
    (
        "understand_skip_below_tokens",
        json.dumps(DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS),
        "int",
        (
            "Token-count threshold for the short-query branch of the "
            "understand_query skip gate. `len(query.split()) <= N` qualifies "
            "as short. Default 3 matches the observed greeting-token "
            "distribution (hi / chào / cảm ơn anh)."
        ),
    ),
    (
        "understand_greeting_patterns",
        json.dumps(list(DEFAULT_GREETING_PATTERNS)),
        "list_str",
        (
            "Regex patterns (case-insensitive, anchored at query start) for "
            "the greeting branch of the understand_query skip gate. Domain-"
            "neutral VN + EN defaults. Empty list disables the regex branch "
            "(only the token-count short-circuit remains active)."
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
