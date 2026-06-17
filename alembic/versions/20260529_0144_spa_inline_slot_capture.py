"""Spa sysprompt — INLINE slot capture (rule 18).

Revision: 0144
Prev:     0143

Trigger (2026-05-29 post-Tier 1 LLM judge verdict):
  Booking partial still present after rule 13 + rule 15 (synthesis):

  Q: "Đặt buổi gội đầu sáng mai 9h"
  A: "Dịch vụ gội đầu dưỡng sinh tại Dr. Medispa có ưu đãi cho khách
     hàng mới với giá 99K/buổi trong 60 phút...
     Anh/chị cho em xin thông tin để đặt lịch ạ"

  User provided 2 slots IN-LINE in the same message:
    - service: "gội đầu"
    - datetime: "sáng mai 9h"

  Bot reply did NOT acknowledge the datetime, asked for info again.
  Rule 13 BƯỚC 4 says "KHÔNG hỏi lại slot đã có" but bot did NOT
  recognise that "sáng mai 9h" IS a datetime slot. Missing rule:
  parse CURRENT user message for inline slots BEFORE asking for info.

Fix: Append rule 18 INLINE_SLOT_CAPTURE — instruct LLM to scan the
CURRENT user turn (plus conversation_history) for already-supplied
slots and acknowledge them explicitly before asking for missing ones.

Lightweight alternative to full Tier 2 conversation_slots JSONB
column (4h scope) — pure prompt rule, no schema change. Tradeoff:
relies on LLM compliance (less robust than typed slot extractor) but
ships in 5 minutes and removes the most visible UX failure.

Sacred-rule alignment:
  ✅ Per-bot scope: only test-spa-id touched
  ✅ Domain-neutral text (rule mentions generic slot names, not
     Vietnamese-specific phrases)
  ✅ HALLU=0: rule does NOT introduce fabrication; only enforces
     acknowledgement of explicit user input
  ✅ CLAUDE.md rule 7: pure alembic
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0144"
down_revision: str | None = "0143"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_NEW_RULE = """

18. ⭐ INLINE_SLOT_CAPTURE — Trước khi hỏi user "anh/chị cho em xin thông tin", PHẢI quét CURRENT user message + conversation_history để tìm slots user ĐÃ cung cấp:
   - Slot DATETIME signals: "9h", "sáng mai", "chiều thứ 7", "tối nay", "tuần sau", "ngày mai", giờ:phút, dạng "[H]:00", "thứ N", "[ngày] tháng [tháng]".
   - Slot SERVICE signals: tên dịch vụ literal đã nhắc trong corpus (gội đầu, triệt lông, massage, trị mụn, trẻ hóa, v.v.).
   - Slot PHONE signals: chuỗi 10-11 số liền (dạng SĐT VN bắt đầu 0).
   - Slot NAME signals: noun đứng ngay TRƯỚC chuỗi SĐT trong câu user.

   QUY TRÌNH:
   - BƯỚC A: Liệt kê (mentally) slots ĐÃ FILL từ message + history.
   - BƯỚC B: Acknowledge từng slot đã có format:
     "Dạ, em ghi nhận: dịch vụ [X], thời gian [Y]. Anh/chị cho em xin thêm [slot còn thiếu]."
   - BƯỚC C: CHỈ hỏi slot CÒN THIẾU, KHÔNG hỏi lại slot đã ack.
   - BƯỚC D: Khi đủ 4 slot (name + phone + datetime + service) → trigger CONFIRM block rule 13 BƯỚC 3.

   KHÔNG dump info dịch vụ generic rồi mới hỏi info — luôn acknowledge slot trước, info dịch vụ chỉ khi user CHƯA chọn service."""


def upgrade() -> None:
    """Append rule 18 to test-spa-id system_prompt (idempotent)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :new_rule,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%18. ⭐ INLINE_SLOT_CAPTURE%')
            """,
        ).bindparams(new_rule=_NEW_RULE),
    )


def downgrade() -> None:
    """Strip rule 18 from test-spa-id system_prompt."""
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
