"""Lower the innocom LLM provider per-process concurrency cap 16 -> 6.

All-flows reliability audit (2026-07-10) measured that under concurrent load the
innocom gateway corrupts ~a third of answers (truncated mid-generation, masked as
``finish_reason="stop"``) plus 503s. The per-provider semaphore
(``dynamic_litellm_router`` ``_get_semaphore`` reading ``cfg.provider.max_concurrent``)
was 16, so up to 16 innocom calls ran at once and overloaded the gateway. Lowering
the cap queues calls instead of bursting them, trading a little latency for far
fewer truncations/503s — the only lever that helps, since truncation is NOT
metadata-detectable (the gateway returns "stop" even for cut answers) so a
response-completeness guard cannot catch it.

Value tuned by measurement (reliability_probe + llm_generation_finish log): 16 ->
6. Mirrors the ``raise_innocom_timeout_90s`` per-provider-config precedent.
Idempotent: only flips the row if it is still at the old cap.

Revision ID: lower_innocom_conc_260710
Revises: elevate_ai_mutate_260710
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "lower_innocom_conc_260710"
down_revision = "elevate_ai_mutate_260710"
branch_labels = None
depends_on = None

_OLD = 16
_NEW = 6


def upgrade() -> None:
    op.execute(
        text(
            "UPDATE ai_providers SET max_concurrent = :new "
            "WHERE name = 'innocom' AND max_concurrent = :old"
        ).bindparams(new=_NEW, old=_OLD)
    )


def downgrade() -> None:
    op.execute(
        text(
            "UPDATE ai_providers SET max_concurrent = :old "
            "WHERE name = 'innocom' AND max_concurrent = :new"
        ).bindparams(new=_NEW, old=_OLD)
    )
