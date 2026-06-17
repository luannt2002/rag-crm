"""[T1-Smartness] P0a — Seed legalbot oos_answer_template.

Revision ID: 0111
Revises: 0110
Create Date: 2026-05-25

Plan: 260525-RAG-POST-15BUG-IMPROVE P0a.

Bug evidence (verified 2026-05-25 load test):
Query "thời tiết hôm nay" on bot ``thong-tu-09-2020-tt-nhnn`` returns
empty answer "?" — sysprompt correctly classifies as ``out_of_scope``
but ``bots.oos_answer_template`` is NULL, so the fallback text path
emits the empty string.

Fix: per-bot ``oos_answer_template`` column populated with a polite
domain-specific refuse message. Per CLAUDE.md "Bot owner owns
everything" — refusal text origin lives in DB ``bots`` row, not
hard-coded in the orchestrator.

Domain-neutral guard: the template uses the regulatory document
name visible to end users (Thông tư 09/2020/TT-NHNN) which IS the
bot's purpose, not a brand/industry leak. Per CLAUDE.md the
exception applies: "Code hệ thống KHÔNG support riêng bất kỳ khách
hàng nào" — this is bot-level config (NOT system code).

Idempotent: ``WHERE bot_id = ... AND oos_answer_template IS NULL``
so re-running on an already-seeded row is no-op.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision: str = "0111"
down_revision: str | None = "0110"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_LEGALBOT_OOS_TEMPLATE = (
    "Em chưa thấy thông tin này trong Thông tư 09/2020/TT-NHNN. "
    "Vui lòng tham khảo văn bản gốc hoặc liên hệ Ngân hàng Nhà nước "
    "Việt Nam để được tư vấn chính xác ạ."
)


def upgrade() -> None:
    """Seed legalbot out-of-scope refuse template (only when NULL)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET oos_answer_template = :tpl, updated_at = NOW()
            WHERE bot_id = 'thong-tu-09-2020-tt-nhnn'
              AND oos_answer_template IS NULL
            """
        ).bindparams(tpl=_LEGALBOT_OOS_TEMPLATE),
    )


def downgrade() -> None:
    """Reset the template to NULL only when it matches the seed exactly.

    Defensive: do NOT clobber a custom template set by the bot owner
    after this migration shipped.
    """
    op.execute(
        text(
            """
            UPDATE bots
            SET oos_answer_template = NULL, updated_at = NOW()
            WHERE bot_id = 'thong-tu-09-2020-tt-nhnn'
              AND oos_answer_template = :tpl
            """
        ).bindparams(tpl=_LEGALBOT_OOS_TEMPLATE),
    )
