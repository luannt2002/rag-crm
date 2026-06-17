"""[T1-Smartness] Default ON: cr_enhanced_enabled (WA-3 contextual retrieval).

Revision ID: 010r
Revises: 010q
Create Date: 2026-05-20

Phase 3 of LEGAL-RETRIEVAL-FIX. WA-3 "enhanced CR" path (chunk_context
column + structural-anchor prefix in ``content``) was implemented in
``DocumentService.ingest()`` but gated behind ``plan_limits.cr_enhanced_enabled``
with ``system_default=False`` — defeating the platform-wide goal that
legal / regulatory / structured corpora should pick up structural context
without per-bot opt-in.

This migration seeds ``system_config.cr_enhanced_enabled = true`` so the
new 3-tier lookup in ``DocumentService`` (per-bot column > plan_limits >
system_config > constants default) resolves to ON for all bots without
explicit override. Cost-sensitive tenants stay opt-out via
``plan_limits.cr_enhanced_enabled = false``.

Cost note: enrichment runs 1 LLM call per chunk (~$0.0003/chunk on
``gpt-4.1-mini``). 80-chunk document → ≈$0.024 incremental ingest cost.
Document this in admin onboarding.

Idempotent: ``ON CONFLICT DO UPDATE SET value=EXCLUDED.value`` overwrites
on re-run, so a prior manual disable (operator UPDATE) gets restored
after an alembic re-run. Operator who really wants ``false`` should
write per-bot ``plan_limits`` instead.
"""

from __future__ import annotations

import logging

from alembic import op


logger = logging.getLogger(__name__)

revision: str = "010r"
down_revision: str | None = "010q"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Seed ``cr_enhanced_enabled = true`` into system_config."""
    op.execute(
        """
        INSERT INTO system_config (key, value, value_type, description, updated_at)
        VALUES (
            'cr_enhanced_enabled',
            'true'::jsonb,
            'bool',
            'WA-3 contextual-retrieval enrichment default ON. Per-bot disable '
            'via plan_limits.cr_enhanced_enabled=false. Adds ~$0.0003 per '
            'chunk via the configured enrichment_model.',
            NOW()
        )
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = NOW()
        """
    )


def downgrade() -> None:
    """Drop the system-default seed. Bots without a per-bot override fall
    back to the ``shared/constants`` default — keep that in mind before
    rolling back a production environment that was depending on the seed.
    """
    op.execute("DELETE FROM system_config WHERE key='cr_enhanced_enabled'")
