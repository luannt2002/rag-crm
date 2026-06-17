"""Phase-C C5 — adaptive reranker weight by intent (seed system_config).

Seeds the two ``system_config`` rows that gate the per-intent RRF blend
landed in this stream:

* ``adaptive_rerank_weight_enabled`` (bool, default ``false``)
* ``rerank_weights_by_intent`` (JSON mapping intent → {vector, bm25, reranker})

Both keys are *seeded only* — the runtime resolution chain in
``application/services/adaptive_rerank_weight.py`` falls back to the
constants SSoT (``DEFAULT_RERANK_WEIGHTS_BY_INTENT`` /
``DEFAULT_ADAPTIVE_RERANK_WEIGHT_ENABLED``) when the rows are absent, so
behaviour is unchanged whether this migration ran or not. Seeding the
rows lets operators flip the feature on / tune the blend via DB without
redeploy.

Defaults reflect HANDOFF §C5 intuition: factoid keeps a balanced split
with slight reranker boost (precision-bearing), multi-hop / aggregation /
comparison lean vector for paraphrase recall, the ``default`` bucket
preserves the historical 50/50 split as the safe fallback for unknown
intents.

Idempotent ``ON CONFLICT (key) DO UPDATE`` — re-running the migration on
a DB that already has the row is a no-op.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-12
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


_WEIGHTS_BY_INTENT: dict[str, dict[str, float]] = {
    "factoid": {"vector": 0.5, "bm25": 0.3, "reranker": 0.2},
    "multi_hop": {"vector": 0.6, "bm25": 0.2, "reranker": 0.2},
    "aggregation": {"vector": 0.6, "bm25": 0.2, "reranker": 0.2},
    "comparison": {"vector": 0.6, "bm25": 0.2, "reranker": 0.2},
    "default": {"vector": 0.5, "bm25": 0.5, "reranker": 0.0},
}

_WEIGHTS_DESCRIPTION = (
    "Phase-C C5: per-intent RRF blend (vector / bm25 / reranker). Active "
    "only when adaptive_rerank_weight_enabled = true. Missing intent falls "
    "back to the 'default' bucket; missing 'default' falls back to flat "
    "DEFAULT_HYBRID_RRF_*_WEIGHT constants."
)

_FLAG_DESCRIPTION = (
    "Phase-C C5: feature flag for per-intent RRF blend. False keeps the "
    "historical flat 0.5 / 0.5 fusion; True activates resolution of "
    "rerank_weights_by_intent at retrieve time."
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, :value, 'bool', :description)
            ON CONFLICT (key) DO UPDATE
            SET value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key="adaptive_rerank_weight_enabled",
            value="false",
            description=_FLAG_DESCRIPTION,
        )
    )
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, :value, 'json', :description)
            ON CONFLICT (key) DO UPDATE
            SET value_type = EXCLUDED.value_type,
                description = EXCLUDED.description
            """
        ).bindparams(
            key="rerank_weights_by_intent",
            value=json.dumps(_WEIGHTS_BY_INTENT, sort_keys=True),
            description=_WEIGHTS_DESCRIPTION,
        )
    )


def downgrade() -> None:
    """Remove both seed rows; runtime falls back to constants SSoT."""
    op.execute(
        text(
            "DELETE FROM system_config WHERE key IN "
            "('adaptive_rerank_weight_enabled', 'rerank_weights_by_intent')"
        )
    )
