"""[T1-Smartness] Seed per-intent rerank top_n + context-cap in system_config.

Revision ID: 010x
Revises: 010w
Create Date: 2026-05-25

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 3 of 5.

Aggregation queries ("có mấy X", "liệt kê tất cả X", "có bao nhiêu X")
need a wider rerank funnel + larger context cap to retain every matching
row through to the LLM. Default ``rerank_top_n=7`` plus
``generate_context_chars_cap=2900`` starved the test-spa-id "1tr499 có
mấy dịch vụ" turn (verified 2026-05-21): 20 candidates → rerank kept 10
→ MMR 7 → prompt_build dropped 3 more → LLM saw 4 chunks. Only 1 of 4
ground-truth rows survived; bot answered "1 dịch vụ" instead of 4.

Seed two JSONB rows keyed by intent name. Code reads via
``_pcfg(state, "rerank_top_n_by_intent", None)`` and
``_pcfg(state, "generate_context_chars_cap_by_intent", None)``; unknown
intent or missing dict falls back to the global default.

Pattern mirrors existing per-intent dicts in ``system_config``
(``crag_min_fallback_score_by_intent``, ``grounding_check_threshold_by_intent``).

Bot owner overrides via ``plan_limits.rerank_top_n_by_intent`` (per-bot
JSONB, takes precedence over this seed). Operators flip / tune values
via direct ``UPDATE system_config SET value=...``.

Idempotent: ``ON CONFLICT (key) DO UPDATE``. Re-running on already-
seeded row replays the same value.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010x"
down_revision: str | None = "010w"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_RERANK_TOP_N_BY_INTENT_JSON = (
    '{"factoid":7,"comparison":12,"multi_hop":12,"aggregation":20,'
    '"out_of_scope":5,"greeting":5,"feedback":5,"chitchat":5,"vu_vo":5}'
)

_CONTEXT_CAP_BY_INTENT_JSON = (
    '{"factoid":2900,"comparison":4200,"multi_hop":4200,"aggregation":5500,'
    '"out_of_scope":1500,"greeting":1500,"feedback":1500,"chitchat":1500,"vu_vo":1500}'
)


def upgrade() -> None:
    """Insert two per-intent JSONB rows into ``system_config``.

    Bind via ``text(...).bindparams(...)`` so the JSON colon syntax (``"factoid":7``)
    is not mis-parsed by SQLAlchemy's named-parameter substitution.
    """
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'rerank_top_n_by_intent',
                CAST(:val_topn AS jsonb),
                'json',
                'Per-intent rerank top_n cap. Aggregation (20) gets a wider funnel than factoid (7) so every matching row survives.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at = NOW()
            """,
        ).bindparams(val_topn=_RERANK_TOP_N_BY_INTENT_JSON),
    )
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'generate_context_chars_cap_by_intent',
                CAST(:val_cap AS jsonb),
                'json',
                'Per-intent assembled-context char cap. Aggregation (5500) needs room for many matching rows; factoid (2900) stays under the Chroma 2025 cliff.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at = NOW()
            """,
        ).bindparams(val_cap=_CONTEXT_CAP_BY_INTENT_JSON),
    )


def downgrade() -> None:
    """Remove both per-intent rows so code path reverts to global defaults."""
    op.execute(
        text(
            """
            DELETE FROM system_config
            WHERE key IN (
                'rerank_top_n_by_intent',
                'generate_context_chars_cap_by_intent'
            )
            """
        ),
    )
