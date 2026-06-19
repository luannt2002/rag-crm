"""Fix factoid hybrid-fusion weights (smart default for exact-fact retrieval).

Revision: 0182
Prev:     0181

adaptive_rerank_weight_enabled is LIVE (system_config=true), so
rerank_weights_by_intent is applied at fusion. The factoid bucket shipped
{vector:0.5, bm25:0.3, reranker:0.2} — but (a) the `reranker` weight is dead
(forward-compat, never applied at fusion → effective sum 0.8, unnormalised),
and (b) bm25=0.3 is BACKWARDS for factoid: an exact-fact / identifier / number
lookup ("Điều 56", "Thông tư 18/2018", a price) is exactly where lexical BM25
exact-match should carry at least equal weight, not less than vector.

Fix the DEFAULT (every bot, incl. new ones — "default must be smart"): factoid
→ balanced {vector:0.5, bm25:0.5, reranker:0.0}, normalised. Synthesis intents
(multi_hop/comparison/aggregation) stay vector-lean (semantic), but the dead
reranker weight is folded out and they are renormalised so the applied weights
sum to 1.0 instead of 0.8.

Idempotent (ON CONFLICT upsert). Reversible. Rule 7 (alembic).
"""
from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

revision: str = "0182"
down_revision: str | None = "0181"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_WEIGHTS = {
    "default":     {"vector": 0.5, "bm25": 0.5, "reranker": 0.0},
    "factoid":     {"vector": 0.5, "bm25": 0.5, "reranker": 0.0},
    "multi_hop":   {"vector": 0.65, "bm25": 0.35, "reranker": 0.0},
    "comparison":  {"vector": 0.65, "bm25": 0.35, "reranker": 0.0},
    "aggregation": {"vector": 0.6, "bm25": 0.4, "reranker": 0.0},
}

_OLD = {
    "default":     {"bm25": 0.5, "vector": 0.5, "reranker": 0.0},
    "factoid":     {"bm25": 0.3, "vector": 0.5, "reranker": 0.2},
    "multi_hop":   {"bm25": 0.2, "vector": 0.6, "reranker": 0.2},
    "comparison":  {"bm25": 0.2, "vector": 0.6, "reranker": 0.2},
    "aggregation": {"bm25": 0.2, "vector": 0.6, "reranker": 0.2},
}


def upgrade() -> None:
    op.execute(
        text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES ('rerank_weights_by_intent', CAST(:v AS jsonb), 'json',
                    'Per-intent hybrid fusion weights — factoid balanced 0.5/0.5 for exact-fact; dead reranker weight folded out + renormalised (forensic 2026-06-05).',
                    NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value, value_type = 'json',
                description = EXCLUDED.description, updated_at = NOW()
        """).bindparams(v=json.dumps(_WEIGHTS, sort_keys=True))
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE system_config SET value = CAST(:v AS jsonb), updated_at = NOW()
            WHERE key = 'rerank_weights_by_intent'
        """).bindparams(v=json.dumps(_OLD, sort_keys=True))
    )
