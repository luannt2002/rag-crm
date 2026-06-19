"""Stream S8 — multi-stage retrieval fallback (default OFF feature flag).

Seeds ``retrieval_multistage_enabled = false`` plus the four stage-slot
config rows so the operator can override any single stage without
inserting fresh keys at runtime. All keys idempotent — re-running this
migration on an already-seeded DB is a no-op.

Default chain (matches ``shared/constants.DEFAULT_RETRIEVAL_STAGES``):
1. ``hybrid_stage1``         — vector + BM25 (RRF at DB layer)
2. ``bm25_only_stage2``      — sparse-only, embedder-outage safe
3. ``keyword_stage3``        — structural-anchor regex (Điều/Khoản/...)
4. ``parent_expand_stage4``  — parent-chunk lift for context recall

Rollback (downgrade): toggles the flag back to ``false`` only. The
stage-slot rows are left in place so a re-enable does not lose any
operator-tweaked stage ordering.

Revision ID: 0080
Revises: 0077
Create Date: 2026-05-11
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0081"
down_revision = "0080a"
branch_labels = None
depends_on = None


_FLAG_KEY = "retrieval_multistage_enabled"
_FLAG_VALUE = "false"
_FLAG_DESCRIPTION = (
    "Stream S8 — when true, the retrieve node walks the multi-stage "
    "fallback chain (retrieval_stage_1..4) on low-confidence single-shot "
    "results. Default false for backward compatibility."
)

_STAGE_DEFAULTS: dict[str, str] = {
    "retrieval_stage_1": "hybrid_stage1",
    "retrieval_stage_2": "bm25_only_stage2",
    "retrieval_stage_3": "keyword_stage3",
    "retrieval_stage_4": "parent_expand_stage4",
}
_STAGE_DESCRIPTION = (
    "Stream S8 — retrieval fallback stage name (one of: hybrid_stage1, "
    "bm25_only_stage2, keyword_stage3, parent_expand_stage4, null). "
    "Set to 'null' to skip a slot without breaking the chain."
)

_THRESHOLD_KEY = "retrieval_early_exit_threshold"
_THRESHOLD_VALUE = "0.35"
_THRESHOLD_DESCRIPTION = (
    "Stream S8 — chain early-exits once the highest-scoring chunk crosses "
    "this floor. Lower = aggressive stop, higher = exhaustive."
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, (:value)::jsonb, 'boolean', :description)
            ON CONFLICT (key) DO UPDATE
            SET value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key=_FLAG_KEY,
            value=_FLAG_VALUE,
            description=_FLAG_DESCRIPTION,
        )
    )
    for stage_key, stage_value in _STAGE_DEFAULTS.items():
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, (:value)::jsonb, 'string', :description)
                ON CONFLICT (key) DO UPDATE
                SET value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=stage_key,
                value=json.dumps(stage_value),
                description=_STAGE_DESCRIPTION,
            )
        )
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, (:value)::jsonb, 'float', :description)
            ON CONFLICT (key) DO UPDATE
            SET value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key=_THRESHOLD_KEY,
            value=_THRESHOLD_VALUE,
            description=_THRESHOLD_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "UPDATE system_config SET value = 'false'::jsonb WHERE key = :key"
        ).bindparams(key=_FLAG_KEY)
    )
