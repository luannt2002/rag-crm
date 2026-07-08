"""Flip brand-scope gate OBSERVE→BLOCK for bot chinh-sach-xe (002-B1 remediation).

Follow-up to ``brand_scope_csx_260707`` (which seeded phrases + observe). The
observe gate is DETERMINISTIC and structurally FP-bounded: it substitutes the
bot's oos_answer_template ONLY when (a) a seeded denial phrase matches the answer,
(b) a proper-noun brand token is extracted, AND (c) the DSI stocks that brand
(count > 0). A TRUE refusal of a non-stocked brand (Michelin, DSI count 0) never
fires → no false-block by construction. Verified this session: DSI count Rovelo=50,
Michelin=0.

Trigger: live câu "Lốp Rovelo 195/55R16 giá bao nhiêu?" → bot answers "Dạ bên em
chưa phân phối hãng Rovelo ạ" (active MISINFORMATION — Rovelo IS distributed, the
195/55R16 row just has a NULL price). Block substitutes the owner's neutral
oos_answer_template (sacred #10 governed path, same as numeric-fidelity/grounding
block) — trading an actively-wrong denial for the owner's neutral refusal. This is
an INTERIM net: the root serving fix (surface the price-NULL Rovelo row as
price-absent) is a separate retrieval change; the block removes the misinformation
in the meantime.

Sacred #7: tracked alembic, not psql. Reversible: downgrade restores observe.

Revision ID: brand_scope_block_csx_260708
Revises: stats_brand_csx_260707
"""
from __future__ import annotations

from alembic import op

revision = "brand_scope_block_csx_260708"
down_revision = "stats_brand_csx_260707"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                COALESCE(plan_limits, '{}'::jsonb),
                '{brand_scope_gate_action}', '"block"'::jsonb, true)
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                COALESCE(plan_limits, '{}'::jsonb),
                '{brand_scope_gate_action}', '"observe"'::jsonb, true)
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )
