"""[T2-CostPerf] AdapChunk-reorg — seed/flip 4 cost knobs + Layer-5 cross-check.

This migration is the DB half of the constants.py flip in commit-pair
``[T2-CostPerf] reorg(cost-knobs)``. It seeds/updates ``system_config`` rows
so the runtime override path (DB-backed Redis cache) matches the new
constants.py defaults. Order of precedence at runtime:

    system_config (DB) → plan_limits (per-bot) → constants.py default

so DB values take precedence — without this seed the codebase would carry
the new defaults but production DB would still hand back the legacy values.

Knobs flipped (per debug doc ``luannt-debug-rag-git.md``):

* ``adapchunk_layer5_cross_check_enabled`` → ``true``   (Phần 22.4 LF / Layer 5)
* ``multi_query_n_variants``               → ``3``      (Phần 22.4 LF1; was 5)
* ``multi_query_model``                    → ``haiku``  (Phần 21.3 W5; was auto)
* ``crag_skip_retry_above_score``          → ``0.65``   (Phần 22.4 LF3; was 0.7)
* ``zeroentropy_reranker_timeout_s``       → ``5.0``    (Phần 22.4 LF2; was 30.0)

Idempotent: ``ON CONFLICT (key) DO UPDATE`` so rerun on a populated DB is
safe; downgrade restores legacy values.

Revision ID: 0095
Revises: 0094a
Create Date: 2026-05-14

down_revision retargeted to 0094a during Wave K1 sequential merge — the
sibling 0094 source-allowlist seed was renumbered to 0094a to resolve a
multi-head collision (both seeds carried revision "0094"). Chain now:
0093 → 0094 (model_invocations) → 0094a (source_allowlist) → 0095.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0095"
down_revision = "0094a"
branch_labels = None
depends_on = None


# (key, new_value_json, legacy_value_json, value_type, description)
_KNOBS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "adapchunk_layer5_cross_check_enabled",
        "true",
        "false",
        "bool",
        "AdapChunk Layer 5 — 5 rule cross-check active by default (reorg 2026-05-14).",
    ),
    (
        "multi_query_n_variants",
        "3",
        "5",
        "int",
        "Multi-Query N variant count (cost-tuned 5 → 3 per debug doc Phần 22.4 LF1).",
    ),
    (
        "multi_query_model",
        '"haiku"',
        '"auto"',
        "string",
        "MQ model explicit (no auto-resolve spike to Sonnet/Opus) — Phần 21.3 W5.",
    ),
    (
        "crag_skip_retry_above_score",
        "0.65",
        "0.7",
        "float",
        "CRAG smart-skip threshold lifted 0.7 → 0.65 (−1.5-2s p95) — Phần 22.4 LF3.",
    ),
    (
        "zeroentropy_reranker_timeout_s",
        "5.0",
        "30.0",
        "float",
        "ZE reranker hard cap 30s → 5s (bound tail latency) — Phần 22.4 LF2.",
    ),
)


_UPSERT_SQL = text(
    """
    INSERT INTO system_config (key, value, value_type, description)
    VALUES (:key, (:value)::jsonb, :value_type, :description)
    ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value,
        value_type = EXCLUDED.value_type,
        description = EXCLUDED.description
    """
)


def upgrade() -> None:
    for key, new_value, _legacy, value_type, description in _KNOBS:
        op.execute(
            _UPSERT_SQL.bindparams(
                key=key,
                value=new_value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    # Restore legacy values (pre-reorg defaults).
    for key, _new, legacy_value, value_type, description in _KNOBS:
        op.execute(
            _UPSERT_SQL.bindparams(
                key=key,
                value=legacy_value,
                value_type=value_type,
                description=description,
            )
        )
