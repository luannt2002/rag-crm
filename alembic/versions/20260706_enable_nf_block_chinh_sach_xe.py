"""Enable numeric-fidelity BLOCK for bot chinh-sach-xe (owner-approved demo).

Sacred #7 requires bot config-state changes go through a TRACKED alembic
migration or the audited admin UI — never an ad-hoc psql UPDATE. This turns on
the 002-I owner-gated block for the reference bot: a fabricated / misattributed
number in the answer is replaced by the bot's oos_answer_template instead of
reaching the customer.

Two changes on the ``bots`` row (bot_id='chinh-sach-xe', channel_type='web'):
  1. plan_limits.numeric_fidelity_action = "block" (default is "observe").
  2. oos_answer_template — set ONLY IF currently empty, to a neutral PLACEHOLDER
     refusal. ⚠ The owner should replace this with their own wording via the
     admin UI; a substituted answer is customer-facing text and belongs to the
     bot owner (sacred #3). Empty template would substitute a blank answer, so a
     placeholder is required for block to be safe.

Reversible: downgrade flips the action back to "observe" and clears only the
placeholder we set.

Revision ID: nf_block_csx_260706
Revises: served_chunks_260703
"""
from __future__ import annotations

from alembic import op

revision = "nf_block_csx_260706"
down_revision = "served_chunks_260703"
branch_labels = None
depends_on = None

_PLACEHOLDER = (
    "Dạ mặt hàng này em chưa có thông tin giá chính xác trong hệ thống. "
    "Anh/chị để lại số điện thoại, em kiểm tra và báo lại sớm nhất ạ."
)


def upgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                COALESCE(plan_limits, '{}'::jsonb),
                '{numeric_fidelity_action}', '"block"'::jsonb, true),
            oos_answer_template = CASE
                WHEN COALESCE(oos_answer_template, '') = ''
                THEN :ph ELSE oos_answer_template END
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """.replace(":ph", "'" + _PLACEHOLDER.replace("'", "''") + "'")
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = plan_limits - 'numeric_fidelity_action',
            oos_answer_template = CASE
                WHEN oos_answer_template = :ph THEN '' ELSE oos_answer_template END
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """.replace(":ph", "'" + _PLACEHOLDER.replace("'", "''") + "'")
    )
