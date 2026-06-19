"""Seed ``system_config.guardrail_provider`` so the registry has a SSoT.

Revision: 0140
Prev:     0139

Trigger (2026-05-29 master consolidated fix-all plan, Phase 3.9):
  Phase 3 introduces ``ragbot.infrastructure.guardrails.registry`` with
  the ``"local"`` / ``"null"`` strategies. Bootstrap currently hardcodes
  ``provider="local"`` in the Factory call; this migration seeds the
  matching row so a future patch (or an operator hot-flip via admin UI)
  can move the choice into DB without a code edit.

  - ``"local"`` keeps the production behaviour: LocalGuardrail walks
    DB-loaded + static input/output rules, persists per-rule audit rows.
  - ``"null"`` is the free-tier / dev opt-out: every check returns no
    hits, nothing is persisted, nothing is raised.

Sacred-rule alignment:
  ✅ Pure DB INSERT via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral (provider names are platform vocabulary)
  ✅ Reversible (downgrade deletes the row)
  ✅ Idempotent (ON CONFLICT DO NOTHING preserves operator override)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0140"
down_revision: str | None = "0139"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Insert the guardrail_provider row (default ``"local"``)."""
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'guardrail_provider',
                '"local"'::jsonb,
                'string',
                'Strategy key for ragbot.infrastructure.guardrails.registry. '
                'Known values: "local" (LocalGuardrail — DB rules + static), '
                '"null" (NullGuardrail — no-op opt-out). Unknown values '
                'degrade to "null" so a typo cannot crash the hot path.',
                NOW()
            )
            ON CONFLICT (key) DO NOTHING
            """,
        ),
    )


def downgrade() -> None:
    """Remove the seeded row (operator overrides preserved if value differs)."""
    op.execute(
        text(
            """
            DELETE FROM system_config
            WHERE key = 'guardrail_provider' AND value = '"local"'::jsonb
            """,
        ),
    )
