"""Seed ``language_packs[vi|en][refuse_message]`` for OOS 7-tier resolver.

Revision: 0136
Prev:     0135

Trigger (2026-05-29 master consolidated fix-all plan, Phase 0.5):
  ``language_packs`` table has 12 prompt_keys per locale (generator, grader,
  understand, condense, rewriter, reflector, decompose, greeting_answer,
  multi_query_*) but NO ``refuse_message`` key. The OOS template resolver
  (Phase 1) walks chain:

    bot.oos_answer_template → bot.plan_limits[oos_answer_template]
    → workspace_config[oos_answer_template] → tenant_config
    → system_config[oos_answer_template]
    → language_packs[code][refuse_message]    ← THIS TIER (new)
    → constants.DEFAULT_OOS_ANSWER_TEMPLATE = ""

  Without this seed, 10/13 bots with ``oos_answer_template=''`` fall straight
  to constants empty → user sees blank answer (verified: 14/30 EMPTY R15,
  3/30 EMPTY 30Q diverse).

Sacred-rule alignment:
  ✅ Pure DB INSERT via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral text (no brand / industry reference)
  ✅ Owner can override via ``bots.oos_answer_template`` (higher tier)
  ✅ Locale-aware (vi/en separate; ja/ko deferred to business case)
  ✅ Reversible
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0136"
down_revision: str | None = "0135"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_VI_REFUSE_MESSAGE = (
    "Em chưa có thông tin chính xác về vấn đề này trong tài liệu. "
    "Anh/chị có thể đặt câu hỏi khác hoặc liên hệ trực tiếp để được hỗ trợ cụ thể hơn ạ."
)

_EN_REFUSE_MESSAGE = (
    "I don't have accurate information on this in the available documents. "
    "Please rephrase your question or contact us directly for more specific assistance."
)


_SEED_ROWS = (
    ("vi", "refuse_message", _VI_REFUSE_MESSAGE),
    ("en", "refuse_message", _EN_REFUSE_MESSAGE),
)


def upgrade() -> None:
    """Insert refuse_message rows; preserves any prior operator override."""
    conn = op.get_bind()
    for code, prompt_key, content in _SEED_ROWS:
        conn.execute(
            text(
                """
                INSERT INTO language_packs (code, prompt_key, content)
                VALUES (:c, :k, :v)
                ON CONFLICT (code, prompt_key) DO NOTHING
                """,
            ),
            {"c": code, "k": prompt_key, "v": content},
        )


def downgrade() -> None:
    """Remove the seeded refuse_message rows."""
    op.execute(
        text(
            """
            DELETE FROM language_packs
            WHERE prompt_key = 'refuse_message' AND code IN ('vi', 'en')
            """,
        ),
    )
