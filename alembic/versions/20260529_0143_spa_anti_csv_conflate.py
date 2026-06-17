"""Spa sysprompt — anti CSV-row conflate (rule 17).

Revision: 0143
Prev:     0142

Trigger (2026-05-29 deep audit on test-spa-id retrieve):
  Probe query "Giá triệt lông cả chân VÀ triệt lông 1/2 chân?" returned
  FABRICATED answer: "cả chân (toàn thân) 2.499.000đ" — this is HALLU
  CONFLATE because bot read CSV chunk:

    STT,Vùng triệt,Giá buổi lẻ,Giá Combo 10 buổi
    7,Cả chân,699000,3999000
    10,Bikini,499000,2999000
    11,Toàn thân,2499000

  and conflated row 7 (Cả chân) WITH row 11 (Toàn thân) — produced
  "cả chân = 2.499.000" instead of correct 699.000.

  Rule 14 ANTI_CROSS_SERVICE covers conflate ACROSS service categories
  (e.g. mixing triệt lông + trị mụn promos) but does NOT cover conflate
  ACROSS ROWS in the same CSV table chunk.

  When user query mentions multiple vùng/sub-service in one question,
  LLM tendency is to merge price columns from different rows into the
  same answer line — verified HALLU pattern.

Fix: Append rule 17 ANTI_CSV_ROW_CONFLATE to test-spa-id sysprompt:
  - When chunk is a CSV / table with STT (row index) column, each
    physical row binds to ONE entity. Bot MUST quote ROW-EXACT.
  - If user asks 2+ entities, look up EACH row separately, never mix.
  - When uncertain about which row maps to user's entity name, prefer
    the highest-score chunk + literal STT marker rather than inference.

Sacred-rule alignment:
  ✅ HALLU=0: rule directly closes a verified fabrication pattern.
  ✅ Domain-neutral: text generic (CSV table + STT row index), not
     spa-specific vocabulary.
  ✅ Per-bot scope: only test-spa-id touched.
  ✅ CLAUDE.md rule 7: pure alembic, no psql UPDATE.
  ✅ Reversible.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0143"
down_revision: str | None = "0142"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_NEW_RULE = """

17. ⭐ ANTI_CSV_ROW_CONFLATE — Khi chunk là bảng CSV / table có cột STT (số thứ tự row):
   - MỖI ROW gắn với MỘT entity duy nhất. KHÔNG được trộn giá / đặc điểm
     của row khác vào row đang trả lời.
   - Khi user hỏi NHIỀU entity (vd "cả chân" và "1/2 chân"), tra cứu
     TỪNG ROW riêng theo TÊN VÙNG khớp chính xác, KHÔNG mix column từ
     row lân cận.
   - Format khi trả lời nhiều row: nêu CỤ THỂ tên row + giá đúng row đó.
     Vd "Cả chân (STT 7): 699K. 1/2 chân (STT 6): 599K."
   - KHÔNG được suy luận "cả chân = toàn thân" hoặc bridge các row có
     vẻ liên quan — nếu tên không khớp 100% với row, áp dụng rule 10
     PARTIAL_ANSWER (báo thiếu).
   - Khi không chắc row nào ứng với user entity, ƯU TIÊN row có tên
     khớp literal trong chunk có top_score cao nhất, KHÔNG infer."""


def upgrade() -> None:
    """Append rule 17 to test-spa-id system_prompt (idempotent)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :new_rule,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%17. ⭐ ANTI_CSV_ROW_CONFLATE%')
            """,
        ).bindparams(new_rule=_NEW_RULE),
    )


def downgrade() -> None:
    """Strip rule 17 from test-spa-id system_prompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = REPLACE(system_prompt, :new_rule, ''),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ).bindparams(new_rule=_NEW_RULE),
    )
