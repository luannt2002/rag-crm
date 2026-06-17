"""Enable structured sub-answer generation — A/B switch (multi-bot, domain-neutral).

Revision: 0192
Prev:     0191

Turns ON ``structured_subanswer_enabled`` platform-wide to A/B-measure the
generation-layer fix for multi-fact drop-fact: for aggregation/comparison/
multi_hop intents the LLM enumerates each {facet, value, citation} before
synthesizing the final answer (reasoning-first), so multi-fact answers stop
dropping facts. SHAPE-only (response_format) — no answer text injected.

Domain-neutral + multi-bot: gated on INTENT (present for every bot), not on
content-type/bot/domain — the correct lever (unlike the removed legal-hybrid
chunking experiment). Kept behind a flag so the A/B can roll it back if any bot
shows HALLU>0 or no Coverage lift (rule #0, HALLU=0 sacred).

A/B: load-test all 12 bots → keep ON only if multi-fact Coverage lifts AND
HALLU=0 holds on EVERY bot; else downgrade (back to flat generation).
Reversible.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0192"
down_revision: str | None = "0191"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_KEY = "structured_subanswer_enabled"


def upgrade() -> None:
    op.execute(
        text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:k, CAST('true' AS jsonb), 'bool',
                    'Structured sub-answer generation for multi-fact intents — A/B ON (plan 260608).',
                    NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = CAST('true' AS jsonb), value_type = 'bool',
                description = EXCLUDED.description, updated_at = NOW()
        """).bindparams(k=_KEY)
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE system_config SET value = CAST('false' AS jsonb), updated_at = NOW()
            WHERE key = :k
        """).bindparams(k=_KEY)
    )
