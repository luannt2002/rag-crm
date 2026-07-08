"""Enable empty-answer guard for chinh-sach-xe + test-spa-id.

P0.1 (fail-verify 2026-07-07): S-048 / B-050 / B-052 returned a BLANK answer — the
LLM completed but produced no content (silent generation failure). The empty-answer
guard (shipped in guard_output, default OFF) substitutes the bot's OWN
oos_answer_template for a whitespace-only answer — the same governed substitution
numeric-fidelity / brand-scope / grounding use (owner text, never app-injected; an
empty string is not an LLM answer to override → sacred #10 safe). Unit-tested 5/5.

This opts the two active reference bots into the guard so a blank reply becomes the
owner's neutral refusal instead of an empty message.

Sacred #7: tracked alembic, not psql. Reversible: downgrade removes the key.

Revision ID: empty_guard_bots_260708
Revises: claim_fidelity_obs_csx_260708
"""
from __future__ import annotations

from alembic import op

revision = "empty_guard_bots_260708"
down_revision = "claim_fidelity_obs_csx_260708"
branch_labels = None
depends_on = None

_BOTS = (
    ("chinh-sach-xe", "web"),
    ("test-spa-id", "web"),
)


def upgrade() -> None:
    for bot_id, channel in _BOTS:
        op.execute(
            f"""
            UPDATE bots
            SET plan_limits = jsonb_set(
                    COALESCE(plan_limits, '{{}}'::jsonb),
                    '{{empty_answer_guard_enabled}}', 'true'::jsonb, true)
            WHERE bot_id = '{bot_id}' AND channel_type = '{channel}'
            """
        )


def downgrade() -> None:
    for bot_id, channel in _BOTS:
        op.execute(
            f"""
            UPDATE bots
            SET plan_limits = plan_limits - 'empty_answer_guard_enabled'
            WHERE bot_id = '{bot_id}' AND channel_type = '{channel}'
            """
        )
