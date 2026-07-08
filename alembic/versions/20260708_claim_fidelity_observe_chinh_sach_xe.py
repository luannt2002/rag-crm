"""Seed claim-fidelity scope-affirmation phrases (OBSERVE) for chinh-sach-xe.

Deep-analysis 2026-07-08: warranty corpus scopes to "lốp xe du lịch (PCR)"
(`xe tải`=0 hits) but the bot affirms "…bao gồm cả lốp xe tải" — a false
AFFIRMATIVE scope claim that numeric_fidelity (number-only) and brand_scope
(denial-only) cannot see. The claim-fidelity gate flags a scope-affirmation
phrase whose affirmed object token is absent from the served context.

Seeds two ``plan_limits`` keys on the bot row (bot_id='chinh-sach-xe',
channel='web'):
  1. claim_fidelity_scope_phrases — generic Vietnamese INCLUSION phrases that
     directly precede the affirmed object ("bao gồm cả X"). LANGUAGE data (no
     brand / service / vehicle-type literal — the OBJECT "lốp xe tải" is never
     named here; the gate discovers it at runtime by membership). Code default
     is empty (domain-neutral src), same governed contract as
     brand_scope_negation_phrases / language_packs.sysprompt_default_rules.
  2. claim_fidelity_action = "observe" — log ``claim_fidelity_observe`` when an
     affirmed object token is unsupported; NEVER touches the answer (sacred #10).
     A follow-up flips to "block" ONLY AFTER observe confirms low false-positive
     rate (constitution remediation ladder: one change, measure, then enable).

Sacred #7: tracked alembic, not psql. Reversible: downgrade removes both keys.

Revision ID: claim_fidelity_obs_csx_260708
Revises: brand_scope_block_csx_260708
"""
from __future__ import annotations

import json

from alembic import op

revision = "claim_fidelity_obs_csx_260708"
down_revision = "brand_scope_block_csx_260708"
branch_labels = None
depends_on = None

# Precise inclusion phrases: each is DIRECTLY followed by the affirmed object, so
# the object tokens (not glue) drive the membership check. Generic VN, no domain
# literal. A loose phrase like "áp dụng cho tất cả" is intentionally excluded —
# it is followed by category glue ("các loại"), which would inflate FP.
_PHRASES = [
    "bao gồm cả",
    "bao gồm thêm",
    "kể cả",
    "áp dụng cho cả",
]
_PHRASES_JSON = json.dumps(_PHRASES, ensure_ascii=False).replace("'", "''")


def upgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                jsonb_set(
                    COALESCE(plan_limits, '{}'::jsonb),
                    '{claim_fidelity_scope_phrases}',
                    '__PHRASES__'::jsonb, true),
                '{claim_fidelity_action}', '"observe"'::jsonb, true)
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """.replace("__PHRASES__", _PHRASES_JSON)
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = plan_limits
                - 'claim_fidelity_scope_phrases'
                - 'claim_fidelity_action'
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )
