"""Spa bot — add BOOKING_SLOT rule + ANTI-CROSS-SERVICE rule.

Revision: 0133
Prev:     0132

Trigger (verified 2026-05-29 diagnostic):
  - 2 case booking refuse oan: "đặt lịch gội đầu / buffet" → bot trả
    "Em chưa có thông tin chính xác" (root cause: i18n.py OOS định nghĩa
    "đặt lịch" — đã fix song song ở code).
  - 3 case incomplete booking: bot báo giá nhưng KHÔNG xin tên/SĐT/giờ.
  - 1 case HALLU trẻ hóa: bot trộn "lấy mụn" của trị mụn (root cause:
    Google Sheet 1 tab chứa nhiều service → chunk cắt ngang section).

Fix: append 2 rule mới vào bots.system_prompt cho test-spa-id:
  - Rule 13 BOOKING_SLOT_GUIDE: sau khi báo giá đặt lịch, chủ động xin
    họ tên / SĐT / thời gian / dịch vụ. Confirm khi đủ slot.
  - Rule 14 ANTI_CROSS_SERVICE: khi quy trình/ưu đãi của dịch vụ X, chỉ
    dùng chunks có tên X. Nếu chunk có 2 dịch vụ, gắn promo/step đúng
    service đứng trước.

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Bot-specific patch (chỉ test-spa-id), không inject platform-wide
  ✅ Reversible (downgrade restore prev sysprompt)
"""

from alembic import op
from sqlalchemy import text

revision: str = "0133"
down_revision: str | None = "0132"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_BOOKING_SLOT_RULE = """

13. ⭐ BOOKING_SLOT_GUIDE — Khi user muốn đặt lịch (signal: "đặt lịch", "đặt hẹn", "thử 1 buổi", "muốn qua spa"):
   - BƯỚC 1: Báo giá + thông tin dịch vụ user hỏi (hoặc hỏi user muốn dịch vụ nào nếu chưa rõ).
   - BƯỚC 2: SAU khi báo giá, CHỦ ĐỘNG xin slot booking:
     "Để em hỗ trợ đặt lịch, anh/chị cho em xin: họ tên, số điện thoại, thời gian (giờ/ngày) muốn đến ạ."
   - BƯỚC 3: Nếu user cung cấp đủ tên + SĐT + thời gian → CONFIRM ngay với format:
     "Dr. Medispa xác nhận lịch:\\n- Tên: [name]\\n- SĐT: [phone]\\n- Thời gian: [time]\\n- Dịch vụ: [service]\\n- Địa chỉ: 102 Vũ Trọng Phụng, Thanh Xuân, HN"
   - BƯỚC 4: Nếu thiếu slot → HỎI CỤ THỂ slot nào còn thiếu (chỉ slot thiếu, KHÔNG hỏi lại slot đã có).
   - RULE phụ về tên khách: TÊN khách hàng là noun đứng ngay TRƯỚC chuỗi số điện thoại trong câu user. KHÔNG hiểu thành tên chuyên viên / nhân viên / bác sĩ.

14. ⭐ ANTI_CROSS_SERVICE — Khi user hỏi quy trình / ưu đãi của dịch vụ X cụ thể:
   - CHỈ dùng chunks có nhắc TÊN dịch vụ X.
   - Nếu chunk chứa 2+ dịch vụ liền nhau, mỗi bước/promo gắn với service tên ĐỨNG TRƯỚC bước/promo đó trong chunk.
   - KHÔNG ghép tùy ý các bước/promo của dịch vụ khác vào dịch vụ X.
   - KHÔNG nói "16 bước" hay "10 bước" nếu chunk không nói rõ con số đó cho service X. Trường hợp không rõ, liệt kê các bước có trong chunk.
"""


def upgrade() -> None:
    """Append BOOKING_SLOT_GUIDE + ANTI_CROSS_SERVICE rules to test-spa-id sysprompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :rules,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND is_deleted = false
            """
        ).bindparams(rules=_BOOKING_SLOT_RULE),
    )


def downgrade() -> None:
    """Remove the appended rules."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = REPLACE(system_prompt, :rules, ''),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND is_deleted = false
            """
        ).bindparams(rules=_BOOKING_SLOT_RULE),
    )
