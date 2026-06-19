"""Disable the legal-hybrid chunking flag — failed A/B + domain-neutral revert.

Revision: 0190
Prev:     0189

Clean A/B (API + worker on fresh code, 2026-06-09) showed the legal-hybrid
chunking path is a NET NEGATIVE on the only corpus it touches (luat): Coverage
swung 0.60/0.72/0.78 (within eval noise — no real lift) while faithfulness fell
0.72 -> 0.57/0.69 and TWO questions (Q1 conditional, Q5 aggregation) turned
HALLU in BOTH clean runs — a real, reproducible HALLU=0 breach (sacred).

It is also a mindset violation: the feature was named after a DOMAIN ("legal")
and chased a single bot, against the platform's domain-neutral + multi-bot /
multi-tenant rule. Drop-fact is a GENERAL problem (any multi-fact question, any
bot) whose correct lever is the generation-layer structured sub-answer
(structured_subanswer_enabled), not per-content-type chunking.

This migration turns the platform flag back OFF (default). The flag-gated code
stays inert (revisit only with a structural, non-domain name + a clean A/B that
holds HALLU=0). luat must be re-ingested under HDT to clear the hybrid chunks.
Reversible. Rule 7 (alembic). Rule #0 (reverted on measured HALLU breach).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0190"
down_revision: str | None = "0189"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_KEY = "adapchunk_legal_hybrid_enabled"


def upgrade() -> None:
    op.execute(
        text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:k, CAST('false' AS jsonb), 'bool',
                    'AdapChunk legal-hybrid OFF — failed A/B (HALLU breach) + domain-neutral revert.',
                    NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = CAST('false' AS jsonb), value_type = 'bool',
                description = EXCLUDED.description, updated_at = NOW()
        """).bindparams(k=_KEY)
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE system_config SET value = CAST('true' AS jsonb), updated_at = NOW()
            WHERE key = :k
        """).bindparams(k=_KEY)
    )
