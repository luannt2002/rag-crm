"""[T1-Smartness] Bug #10 — Seed per-intent MMR similarity threshold in system_config.

Revision ID: 0110
Revises: 010z
Create Date: 2026-05-25

Plan: 260525-4BUG-INGEST-PIPELINE Bug #10 follow-up.

After Bug #9 ship the data layer was correct (4 chunks containing
``1499000`` retrievable in DB). Live test still returned "1 dịch vụ"
because MMR dedup collapsed 20 retrieved chunks → 3. The 3 surviving
chunks all described the same service (Râu nam triệt lông). Row-
shape CSV chunks sharing column structure but different data values
were treated as duplicates at the default ``mmr_similarity_threshold
=0.88``.

Fix: per-intent threshold dict — aggregation gets 0.98 (only drop near-
identical), comparison / multi_hop 0.95, factoid / greeting keep 0.88.

Used by ``query_graph.mmr_dedup`` via ``_pcfg(state,
"mmr_similarity_threshold_by_intent", None)``; unknown intent falls
back to ``DEFAULT_MMR_SIMILARITY_THRESHOLD``.

Pattern mirrors existing ``rerank_top_n_by_intent`` /
``generate_context_chars_cap_by_intent`` /
``grounding_check_threshold_by_intent`` seeds.

Idempotent: ``ON CONFLICT (key) DO UPDATE``.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "0110"
down_revision: str | None = "010z"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_MMR_THRESH_BY_INTENT_JSON = (
    '{"factoid":0.88,"comparison":0.95,"multi_hop":0.95,"aggregation":0.98,'
    '"out_of_scope":0.88,"greeting":0.88,"feedback":0.88,'
    '"chitchat":0.88,"vu_vo":0.88}'
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'mmr_similarity_threshold_by_intent',
                CAST(:val AS jsonb),
                'json',
                'Per-intent MMR similarity threshold. Aggregation (0.98) loosens dedup so row-shape CSV chunks with same column structure but different data values survive MMR.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at = NOW()
            """,
        ).bindparams(val=_MMR_THRESH_BY_INTENT_JSON),
    )


def downgrade() -> None:
    op.execute(
        text(
            """
            DELETE FROM system_config
            WHERE key = 'mmr_similarity_threshold_by_intent'
            """,
        ),
    )
