"""Seed ``system_config`` rows for the ZeroEntropy reranker strategy.

Adds the registry-friendly metadata rows so operators can flip the active
reranker from ``jina`` to ``zeroentropy`` with a single UPDATE without
redeploying. The default ``reranker_provider`` value is **deliberately
left untouched** — this migration only introduces the strategy's
discoverable configuration surface.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already
holding the rows is a no-op. Downgrade deletes the seeded rows so the
schema-side defaults take over.

Revision ID: 0083
Revises: 0082
Create Date: 2026-05-11
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


# (key, value, value_type, description) — all rows JSON-encoded so the
# scalar string lands inside system_config.value (jsonb).
_SEED_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "zeroentropy_api_url",
        "https://api.zeroentropy.dev/v1/models/rerank",
        "string",
        "ZeroEntropy rerank endpoint. Ops can override per-environment "
        "via env (RERANKER_ZEROENTROPY_API_URL) when a proxy or "
        "regional gateway is in use. Adapter falls back to the constant "
        "default when this row is absent.",
    ),
    (
        "zeroentropy_model",
        "zerank-2",
        "string",
        "Active ZeroEntropy reranker model. zerank-2 is the multilingual "
        "instruction-following flagship (state-of-the-art); flip to "
        "zerank-1-small for lower cost / lower latency on simple queries.",
    ),
    (
        "zeroentropy_latency_mode",
        "fast",
        "string",
        "ZeroEntropy latency knob. 'fast' = guaranteed sub-second, lower "
        "RPM ceiling — recommended for user-blocking reranking. 'slow' = "
        ">10s expected latency but higher RPM, suitable for batch / "
        "offline reranking runs.",
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _SEED_ROWS:
        json_value = json.dumps(value) if value_type == "string" else value
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
                value=json_value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    """Remove the seeded rows so schema-side defaults take over again."""
    op.execute(
        text(
            "DELETE FROM system_config "
            "WHERE key IN ("
            "'zeroentropy_api_url', "
            "'zeroentropy_model', "
            "'zeroentropy_latency_mode'"
            ")"
        )
    )
