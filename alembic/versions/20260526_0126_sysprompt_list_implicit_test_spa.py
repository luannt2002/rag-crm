"""Augment test-spa-id sysprompt: Rule 9 "list-implicit" enumeration.

Revision: 0126
Prev:     0125

Eval round 1 trace:
  Q: "có dịch vụ nào có ưu đãi không"
  Corpus có 6+ dịch vụ ưu đãi (Massage 99K, CSD 199K, Cấp nước 299K, ...)
  Bot answer: "Massage cổ vai gáy 99K..." (chỉ 1 dịch vụ)
  → AnsRel cao nhưng Coverage thấp

Root cause:
  - Sysprompt rule 8 (aggregation enumeration) chỉ trigger khi
    intent classifier tag = "aggregation".
  - Câu yes/no có entity list ("có ... không") tag = factoid →
    không trigger rule 8.

Industry pattern: Anthropic + LangChain best practice — sysprompt
must explicitly handle "implicit list" queries.

Fix: Append Rule 9 hướng dẫn LLM nhận biết câu yes/no có entity list
→ enumerate hết. Không sửa code, KHÔNG động intent classifier.
"""

from alembic import op
from sqlalchemy import text

revision: str = "0126"
down_revision: str | None = "0125"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_LIST_IMPLICIT_RULE = """

═══════════════════════════════════════════════════════════
QUY TẮC LIỆT KÊ NGẦM (list-implicit, MỚI 2026-05-26):
═══════════════════════════════════════════════════════════

9. ⭐ LIST-IMPLICIT RULE — Khi user hỏi câu YES/NO nhưng câu trả lời
   yêu cầu liệt kê nhiều entity (vd "có dịch vụ nào X không",
   "có gói nào...", "có ai làm...", "có khuyến mãi nào", "có ưu đãi gì"):
   - PHẢI liệt kê TẤT CẢ entity có trong <documents>, KHÔNG chỉ 1-2 ví dụ.
   - Format: "Có N dịch vụ X: 1) ..., 2) ..., ..., N) ..." rồi mới "ạ".
   - KHÔNG được trả 1 entity rồi dừng dù chunks chứa nhiều.
   - Áp dụng cho mọi keyword: ưu đãi, khuyến mãi, giảm giá, free, tặng,
     gói, combo, voucher, mã, deal.
   - Khi chunks chỉ có 1 entity match → trả 1 entity + ghi "Em hiện
     chỉ tìm thấy 1 dịch vụ X, nếu anh/chị cần thêm vui lòng liên hệ".
"""


def upgrade() -> None:
    """Append Rule 9 to test-spa-id sysprompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :rule,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND is_deleted = false
              AND system_prompt NOT LIKE '%LIST-IMPLICIT RULE%'
            """
        ).bindparams(rule=_LIST_IMPLICIT_RULE),
    )


def downgrade() -> None:
    """Strip Rule 9 from test-spa-id sysprompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = substring(
                system_prompt FROM 1 FOR position(:marker IN system_prompt) - 1
            ),
            updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND is_deleted = false
              AND system_prompt LIKE '%' || :marker || '%'
            """
        ).bindparams(marker="QUY TẮC LIỆT KÊ NGẦM"),
    )
