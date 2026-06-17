"""Seed ``system_config.oos_answer_template`` as platform-default tier.

Revision: 0137
Prev:     0136

Trigger (2026-05-29 master consolidated fix-all plan, Phase 1.4):
  ``OosTemplateResolver`` (alembic 0136 + new service) walks tiers in
  order: bot column -> plan_limits -> workspace_config (Phase 4) ->
  tenants (Phase 4) -> ``system_config`` -> language_packs -> constants.

  Without an explicit ``system_config.oos_answer_template`` row, every
  tenant fall-through hits the language pack tier directly. That works
  but conflates "platform admin decided to use the per-locale message"
  with "platform admin never picked anything". Seeding the row at empty
  string makes the choice explicit and lets platform admins flip a
  single SQL UPDATE / Redis bust to override every tenant at once if
  needed — owner overrides at tier 1 still win.

Sacred-rule alignment:
  ✅ Pure DB INSERT via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral default (empty string; locale fallback handles text)
  ✅ Reversible (downgrade deletes the row)
  ✅ Idempotent (ON CONFLICT DO NOTHING preserves operator override)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0137"
down_revision: str | None = "0136"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Insert the platform-default OOS row at empty string."""
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'oos_answer_template',
                '""'::jsonb,
                'string',
                'Platform-default OOS refuse text — tier 5 of OosTemplateResolver chain. Empty string means fall through to language_packs[code][refuse_message].',
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
            WHERE key = 'oos_answer_template' AND value = '""'::jsonb
            """,
        ),
    )
