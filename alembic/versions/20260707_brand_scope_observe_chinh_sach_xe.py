"""Seed brand-scope gate (OBSERVE) for bot chinh-sach-xe (truth-audit 002-B1).

Sacred #7: bot config-state changes go through a TRACKED alembic migration or the
audited admin UI — never an ad-hoc psql UPDATE. This seeds the reference bot with
the data the 002-B1 brand-scope gate needs, in OBSERVE mode (log only, no answer
change), so the false-refusal rate can be measured before any block is enabled.

Two ``plan_limits`` keys on the ``bots`` row (bot_id='chinh-sach-xe', channel='web'):
  1. brand_scope_negation_phrases — the locale phrases that signal a distribution
     denial. GROUNDED in the actual step20 answers (the bot literally replied
     "Dạ bên em chưa phân phối hãng Rovelo ạ" / "chưa có hãng Rovelo ạ" while the
     structured index holds 50+ Rovelo SKUs). These are LANGUAGE data (generic
     Vietnamese, no brand named), the same governed alembic-seeded-text pattern as
     ``language_packs.sysprompt_default_rules`` (ADR-W1-S10). The code default is
     empty (domain-neutral src); the phrases live here as config DATA.
  2. brand_scope_gate_action = "observe" (explicit; also the code default). The
     gate logs ``brand_scope_observe`` when a denied brand IS actually stocked
     (DSI count > 0) and never touches the answer. A follow-up migration flips
     this to "block" ONLY AFTER observe confirms zero false-positives.

Why observe first: a block substitutes the bot's oos_answer_template — measured
false-positive rate must be zero before that ships (constitution remediation
ladder: one change, measure, then enable).

Reversible: downgrade removes both keys.

Revision ID: brand_scope_csx_260707
Revises: nf_block_csx_260706
"""
from __future__ import annotations

import json

from alembic import op

revision = "brand_scope_csx_260707"
down_revision = "nf_block_csx_260706"
branch_labels = None
depends_on = None

# Grounded in step20 observed denials (both verbatim forms) + one tense variant
# for phrasing drift. The DSI existence check (count > 0) is what distinguishes a
# FALSE denial (brand stocked) from a TRUE one (brand absent) — so a loose phrase
# cannot over-fire: it only flags when the corpus actually carries the brand.
_PHRASES = [
    "chưa phân phối hãng",
    "chưa có hãng",
    "không phân phối hãng",
]
_PHRASES_JSON = json.dumps(_PHRASES, ensure_ascii=False).replace("'", "''")


def upgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                jsonb_set(
                    COALESCE(plan_limits, '{}'::jsonb),
                    '{brand_scope_negation_phrases}',
                    '__PHRASES__'::jsonb, true),
                '{brand_scope_gate_action}', '"observe"'::jsonb, true)
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """.replace("__PHRASES__", _PHRASES_JSON)
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = plan_limits
                - 'brand_scope_negation_phrases'
                - 'brand_scope_gate_action'
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )
