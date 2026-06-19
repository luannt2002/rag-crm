"""[T1-Smartness] Seed per-intent retrieve top_k in system_config.

Revision ID: 0116
Revises: 0115
Create Date: 2026-05-26

The retrieve node in query_graph.py slices the RRF-fused and
lexical-fused candidate lists by a global ``top_k=20`` cap. This cap
is too large for lightweight intents (greeting / chitchat → only 5
chunks needed) and too small for aggregation queries (need 40 raw
candidates so the rerank + MMR funnel retains every matching row).

This migration seeds the ``retrieve_top_k_by_intent`` system_config
row so operators and per-bot overrides can tune the per-intent retrieve
funnel width without redeploying code.

Resolution order at the call site (query_graph retrieve node):
  ``pipeline_config.retrieve_top_k_by_intent``   ← this row
  > ``DEFAULT_TOP_K`` constant fallback           (20)

Unknown intent falls back to ``DEFAULT_TOP_K``.

Pattern mirrors ``rerank_top_n_by_intent`` / ``mmr_similarity_threshold_by_intent``
seeds. Idempotent: ``ON CONFLICT (key) DO UPDATE``.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "0116"
down_revision: str | None = "0115"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_RETRIEVE_TOP_K_BY_INTENT_JSON = (
    '{"greeting":5,"chitchat":5,"vu_vo":5,"feedback":5,'
    '"out_of_scope":5,"factoid":15,"comparison":25,'
    '"multi_hop":30,"aggregation":40}'
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'retrieve_top_k_by_intent',
                CAST(:val AS jsonb),
                'json',
                'Per-intent retrieve top_k cap applied at RRF-fuse and lexical-fuse slice points. Lightweight intents (greeting/chitchat/vu_vo/feedback/out_of_scope) need 5; aggregation needs 40 raw candidates to feed the rerank+MMR funnel.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at = NOW()
            """,
        ).bindparams(val=_RETRIEVE_TOP_K_BY_INTENT_JSON),
    )


def downgrade() -> None:
    op.execute(
        text(
            """
            DELETE FROM system_config
            WHERE key = 'retrieve_top_k_by_intent'
            """,
        ),
    )
