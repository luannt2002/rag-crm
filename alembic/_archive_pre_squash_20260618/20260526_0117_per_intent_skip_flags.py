"""[T2-CostPerf] 0117 — Per-intent skip flags for rewrite + multi_query.

Revision ID: 0117
Revises: 0115
Create Date: 2026-05-26

Seeds two ``system_config`` JSONB rows that gate the ``rewrite`` and
``multi_query_fanout`` LLM calls based on the resolved intent.

Lightweight intents (greeting / chitchat / factoid / feedback / vu_vo /
out_of_scope) skip both calls — saving ~3.5s wall time on the critical
path (1.2s rewrite + 2.3s multi-query) without any T1 quality regression.
Aggregation / comparison / multi_hop keep both calls enabled because
paraphrase diversity and query reformulation materially lift recall for
those compound-intent types.

The values match ``DEFAULT_REWRITE_ENABLED_BY_INTENT`` and
``DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT`` in ``shared/constants.py``
(the code SSoT); seeding them into DB makes operator override visible
and auditable via the admin UI.

Pattern mirrors ``rerank_top_n_by_intent`` / ``mmr_similarity_threshold_by_intent``.
Idempotent: ``ON CONFLICT (key) DO UPDATE``.
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "0117"
down_revision: str | None = "0116"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# JSON values — must match DEFAULT_REWRITE_ENABLED_BY_INTENT /
# DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT in shared/constants.py.
_REWRITE_JSON = (
    '{"greeting":false,"chitchat":false,"factoid":false,'
    '"feedback":false,"vu_vo":false,"out_of_scope":false,'
    '"aggregation":true,"comparison":true,"multi_hop":true}'
)

_MULTI_QUERY_JSON = (
    '{"greeting":false,"chitchat":false,"factoid":false,'
    '"feedback":false,"vu_vo":false,"out_of_scope":false,'
    '"aggregation":true,"comparison":true,"multi_hop":true}'
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'rewrite_enabled_by_intent',
                CAST(:val AS jsonb),
                'json',
                'Per-intent boolean dict controlling the rewrite LLM call. Lightweight intents (greeting/chitchat/factoid/feedback/vu_vo/out_of_scope) are false — skip saves ~1.2s/turn. Aggregation/comparison/multi_hop remain true.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value       = EXCLUDED.value,
                value_type  = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at  = NOW()
            """
        ).bindparams(val=_REWRITE_JSON),
    )
    conn.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'multi_query_enabled_by_intent',
                CAST(:val AS jsonb),
                'json',
                'Per-intent boolean dict controlling multi_query paraphrase fanout. Lightweight intents are false — skip saves ~2.3s/turn. Aggregation/comparison/multi_hop remain true.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value       = EXCLUDED.value,
                value_type  = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at  = NOW()
            """
        ).bindparams(val=_MULTI_QUERY_JSON),
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            "DELETE FROM system_config WHERE key IN "
            "('rewrite_enabled_by_intent', 'multi_query_enabled_by_intent')"
        ),
    )
