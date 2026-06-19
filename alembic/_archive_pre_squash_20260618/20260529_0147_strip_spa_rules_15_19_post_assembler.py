"""Strip rules 15-19 from test-spa-id system_prompt — assembler now ships them.

Revision: 0147
Prev:     0146

Trigger (2026-05-29 J1 final step):
  Rules 15-19 were initially shipped per-bot via alembic 0142-0145
  (UPDATE bots SET system_prompt = system_prompt || rule_NN_text WHERE
  bot_id='test-spa-id'). Alembic 0146 seeded the canonical rules text
  into ``language_packs[vi][sysprompt_default_rules]``, and the
  application-layer ``SysPromptAssembler`` service appends those rules
  to ``bot.system_prompt`` at request time.

  If we leave the rules duplicated in both
  (a) ``bots[test-spa-id].system_prompt`` (per alembic 0142-0145), AND
  (b) ``language_packs[vi].sysprompt_default_rules`` (alembic 0146)
  then the assembler would APPEND the rules a second time → spa LLM
  sees the rules block TWICE → 12k char overhead and rule numbering
  collision (LLM sees "15. ... 15. ...").

  This migration strips the rules-15-19 block from the spa column so
  the assembler runs a clean single append at request time. After this
  migration:

    spa effective system_prompt at LLM input =
        bot[test-spa-id].system_prompt           (rules 1-14 only)
      + language_packs[vi].sysprompt_default_rules  (rules 15-19, from 0146)
      − any rules in bots.plan_limits.sysprompt_rules_disabled

  → identical semantics to pre-0147 + scaling to every other bot with
  language='vi' (luat-giao-thong, vat-ly-11, hoa-hoc-10, etc).

Multi-tenant outcome:
  - test-spa-id: behaviour unchanged at LLM input boundary.
  - Other 12 bots with language='vi': automatically inherit rules 15-19.
  - Tenants onboarding new vi-locale bot: rules auto-apply without ship.
  - Bot owner opts out via ``bots.plan_limits.sysprompt_rules_disabled``
    JSONB list (e.g. ``["rule_17"]``) editable through admin UI.

Sacred-rule alignment:
  ✅ Pure alembic (CLAUDE.md rule 7).
  ✅ Idempotent — strip pattern matches once; second run no-op.
  ✅ Reversible — downgrade re-appends rules 15-19 (semantically same as
     pre-0146 state, since assembler still reads language_packs row; only
     visual duplication risk if downgrade without also downgrading 0146).
  ✅ Per-bot scope (single bot_id touched).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0147"
down_revision: str | None = "0146"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Pattern: rules 15-19 block starts with the rule 15 header and runs to
# end of system_prompt. We anchor on the literal "15. ⭐ SYNTHESIS_COMPLETE"
# header. PostgreSQL POSIX regex (~) is used with [\n\r] = "newline class".
# Equivalent to Python: re.sub(r'\n*15\. ⭐ SYNTHESIS_COMPLETE.*\Z', '', s,
# flags=re.DOTALL).
_STRIP_REGEX = r"\s*15\. ⭐ SYNTHESIS_COMPLETE[\s\S]*$"


def upgrade() -> None:
    """Strip rules 15-19 block from test-spa-id system_prompt."""
    op.execute(
        text(
            r"""
            UPDATE bots
            SET system_prompt = REGEXP_REPLACE(
                system_prompt,
                :pattern,
                '',
                'g'
            ),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND system_prompt ~ '15\. ⭐ SYNTHESIS_COMPLETE'
            """,
        ).bindparams(pattern=_STRIP_REGEX),
    )


def downgrade() -> None:
    """Re-append rules 15-19 from language_packs[vi] back to the spa column.

    NOTE: downgrade is best-effort. Operators downgrading without also
    rolling back alembic 0146 will see rules visible at BOTH places
    (spa column + language_packs); the assembler would then double-append
    at request time. Recommended downgrade order: 0147 → 0146.
    """
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || (
                SELECT content FROM language_packs
                WHERE code = 'vi' AND prompt_key = 'sysprompt_default_rules'
                LIMIT 1
            ),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%15. ⭐ SYNTHESIS_COMPLETE%')
            """,
        ),
    )
