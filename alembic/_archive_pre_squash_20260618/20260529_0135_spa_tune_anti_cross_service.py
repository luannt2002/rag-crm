"""Spa bot — tune ANTI_CROSS_SERVICE rule (rule 14) to fix regression.

Revision: 0135
Prev:     0134

Trigger (verified 2026-05-29 R4 post-0134 test):
  REGRESSION: 2 case triệt lông hôm qua PASS, hôm nay REFUSE OAN:
    - triet_long/3: "Giá triệt lông nách và triệt lông chân bao nhiêu?"
    - triet_long/6: "Em muốn đặt lịch triệt lông nách, được không?"
  → Bot trả: "Em chưa có thông tin chính xác về vấn đề này"
  → corpus có giá nách (199K) và chân (699K) trong chunks rõ ràng.

Root cause:
  Rule 14 ANTI_CROSS_SERVICE shipped alembic 0133 quá strict:
    "CHỈ dùng chunks có nhắc TÊN dịch vụ X.
     KHÔNG ghép tùy ý các bước/promo của dịch vụ khác vào dịch vụ X."
  Bot quá thận trọng khi query hỏi 2 vùng triệt ("nách + chân") trong
  cùng câu → bot lo lẫn 2 service → refuse cả câu.

Fix: REPLACE rule 14 strict → softer wording:
  "ƯU TIÊN chunks tên X. Khi query hỏi nhiều vùng/sub-service của
   CÙNG service chính (vd nhiều vùng triệt lông), trả lời đầy đủ cho
   từng vùng. CHỈ refuse khi corpus thực sự không có data."

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Reversible
  ✅ Surgical (single bot)
"""

from alembic import op
from sqlalchemy import text

revision: str = "0135"
down_revision: str | None = "0134"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_OLD_RULE_14 = """14. ⭐ ANTI_CROSS_SERVICE — Khi user hỏi quy trình / ưu đãi của dịch vụ X cụ thể:
   - CHỈ dùng chunks có nhắc TÊN dịch vụ X.
   - Nếu chunk chứa 2+ dịch vụ liền nhau, mỗi bước/promo gắn với service tên ĐỨNG TRƯỚC bước/promo đó trong chunk.
   - KHÔNG ghép tùy ý các bước/promo của dịch vụ khác vào dịch vụ X.
   - KHÔNG nói "16 bước" hay "10 bước" nếu chunk không nói rõ con số đó cho service X. Trường hợp không rõ, liệt kê các bước có trong chunk."""

_NEW_RULE_14 = """14. ⭐ ANTI_CROSS_SERVICE (tuned 0135) — Khi user hỏi quy trình / ưu đãi của dịch vụ X cụ thể:
   - ƯU TIÊN chunks có nhắc TÊN dịch vụ X. Nếu corpus có data về X, PHẢI trả lời đầy đủ.
   - Khi query hỏi NHIỀU vùng / sub-service của CÙNG service chính (vd "giá triệt nách và triệt chân", "đặt lịch triệt lông nách"), TRẢ LỜI ĐẦY ĐỦ cho từng vùng/sub-service nếu corpus có data.
   - Khi chunk chứa 2+ dịch vụ KHÁC NHAU liền nhau (vd trẻ hóa + trị mụn), mỗi bước/promo gắn với service tên ĐỨNG TRƯỚC bước/promo đó trong chunk. KHÔNG ghép tùy ý các bước/promo của dịch vụ KHÁC vào dịch vụ X.
   - KHÔNG nói "16 bước" hay "10 bước" nếu chunk không nói rõ con số đó cho service X. Trường hợp không rõ, liệt kê các bước có trong chunk.
   - CHỈ refuse khi corpus thực sự KHÔNG có data về dịch vụ user hỏi. KHÔNG refuse oan chỉ vì query hỏi nhiều vùng cùng lúc."""


def upgrade() -> None:
    """Replace strict rule 14 with softer version."""
    op.execute(
        text(
            """
            UPDATE bots SET system_prompt = REPLACE(system_prompt, :old, :new),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND is_deleted = false
            """
        ).bindparams(old=_OLD_RULE_14, new=_NEW_RULE_14),
    )


def downgrade() -> None:
    """Restore the strict version of rule 14."""
    op.execute(
        text(
            """
            UPDATE bots SET system_prompt = REPLACE(system_prompt, :new, :old),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND is_deleted = false
            """
        ).bindparams(old=_OLD_RULE_14, new=_NEW_RULE_14),
    )
