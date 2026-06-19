"""Re-append rule 18 (INLINE_SLOT_CAPTURE) + 19 (STRICT_PROMO_BINDING) to test-spa-id system_prompt.

Revision: 0149
Prev:     0148

Trigger (2026-05-29 sacred-rule audit fix continuation):
  Alembic 0148 stripped rules 18+19 from platform-tier
  ``language_packs[vi|en].sysprompt_default_rules`` because their text
  contains VN spa-specific signals (gội đầu, triệt lông, massage, SĐT VN)
  inappropriate for the platform tier (every vi-locale bot inherits).

  These rules ARE still valuable for spa-like bots (service-booking with
  promo). The fix preserves their effect on test-spa-id by re-appending
  to the per-bot system_prompt column. Other vi-locale bots
  (luat-giao-thong, vat-ly-11, hoa-hoc-10, etc.) no longer inherit the
  spa-specific text — clean separation of platform-tier vs per-bot tier.

Result:
  - test-spa-id behaviour identical to pre-J1 (rules 15-19 all active)
  - other 12 vi-locale bots receive only rule 15+16+17 via assembler
  - rule 17 (ANTI_CSV_ROW_CONFLATE) stays at platform tier — fully
    domain-neutral text about CSV row binding semantics
  - rule 18+19 confined to per-bot scope where domain text appropriate

Spa rule text re-appended is verbatim from alembic 0144 (rule 18) +
alembic 0145 (rule 19) — pre-J1 canonical source.

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Per-bot scope (only test-spa-id touched)
  ✅ Idempotent — NOT (system_prompt LIKE ...) guard
  ✅ Reversible — downgrade strips
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0149"
down_revision: str | None = "0148"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_NEW_RULES = """

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

   KHÔNG dump info dịch vụ generic rồi mới hỏi info — luôn acknowledge slot trước, info dịch vụ chỉ khi user CHƯA chọn service.

19. ⭐ STRICT_PROMO_BINDING — Khuyến mãi (promo price / giá gốc / thời lượng / điều kiện áp dụng) PHẢI bind chặt với entity được nêu literal trong CÙNG chunk:
   - Khi user hỏi promo của SERVICE X, CHỈ quote giá / thời gian / điều kiện từ chunk có literal tên "X" + literal nêu promo của X.
   - KHÔNG được borrow / suy luận / pattern-match promo từ service Y (kể cả Y "tương tự" X về category).
   - Khi user hỏi nhiều service (X, Y, Z) cùng lúc:
     * Look up TỪNG service riêng từ chunk RIÊNG có literal tên service đó.
     * Mỗi service nêu promo CỦA NÓ thôi (NOT promo của service khác).
     * Service nào trong retrieved chunks KHÔNG có promo literal → áp dụng rule 10 PARTIAL_ANSWER:
       "Về [service X], em chưa có thông tin khuyến mãi cụ thể trong tài liệu, anh/chị vui lòng liên hệ trực tiếp để được tư vấn ạ."
   - KHÔNG được output promo "99K", "giá gốc Y", "thời gian Z" cho 1 service nếu chunk service đó KHÔNG nêu literal các số đó.
   - Pattern antifragile: nếu thấy bản thân đang "infer" promo dựa similarity giữa 2 service → STOP và áp dụng rule 10 PARTIAL_ANSWER.

   Example đúng (giả định):
   - Chunk A: "Massage X: 99K khuyến mãi, gốc 400K, 60 phút"
   - Chunk B: "Massage Y" (không nói promo)
   - User: "giá massage X và Y?"
   - Đúng: "Massage X: 99K (gốc 400K) 60 phút. Massage Y em chưa có thông tin khuyến mãi cụ thể, anh/chị liên hệ trực tiếp ạ."
   - Sai: "Massage X: 99K. Massage Y: 99K (gốc 400K) 60 phút." ← bịa từ chunk A."""


def upgrade() -> None:
    """Append rule 18+19 to test-spa-id system_prompt (idempotent)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :new_rules,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%18. ⭐ INLINE_SLOT_CAPTURE%')
            """,
        ).bindparams(new_rules=_NEW_RULES),
    )


def downgrade() -> None:
    """Strip rule 18+19 from test-spa-id system_prompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = REPLACE(system_prompt, :new_rules, ''),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ).bindparams(new_rules=_NEW_RULES),
    )
