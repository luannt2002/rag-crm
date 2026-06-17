"""Sysprompt augment v2 — partial-answer + multi-hop bridging + enumeration strict.

Revision: 0128
Prev:     0127

Phase 3 root cause discovery (Round 3 eval, 107 turns analyzed):

**comparison faith 59%, refuse 16.7%** — em verified by DB trace:
  - 10/12 turns retrieve OK (chunks=12-20)
  - 2/12 refuse despite chunks=12 → bot ĐÃ TRẢ A nhưng refuse cả câu khi B mỏng
  - Root cause: sysprompt NO RULE for "partial answer when 1 entity missing"
  → LLM defaults to refuse template

**multi_hop faith 55%** — bot trả half:
  - Sample "Mối quan hệ giữa A và B" → bot trả vế A, không bridge sang B
  - Root cause: sysprompt NO RULE for "bridging 2 concepts"

**aggregation_list faith 58%** — miss mid-context items:
  - Bot list 2-3 mục thay vì 5-6 thực tế
  - Root cause: sysprompt rule 8 says "enumerate" but generic, no "span chunks" guidance

Fix: 3 new rules appended to sysprompt of test-spa-id + tessss.
Domain-neutral wording (no brand literal, no industry-specific).

Sacred-rule alignment:
✅ Domain-neutral (no spa/legal/medical keyword)
✅ Per-bot (specific bot_id)
✅ Reversible (downgrade strips appended block)
✅ Alembic-tracked (no psql UPDATE thủ công)
✅ HALLU=0 sacred preserved (partial answer rule has "KHÔNG bịa entity còn lại")
"""

from alembic import op
from sqlalchemy import text

revision: str = "0128"
down_revision: str | None = "0127"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_RULE_V2_AUGMENT = """

═══════════════════════════════════════════════════════════
QUY TẮC V2 (2026-05-27 — Phase 3 fix):
═══════════════════════════════════════════════════════════

10. ⭐ PARTIAL-ANSWER RULE — Khi câu hỏi yêu cầu **so sánh / đối chiếu A với B** mà <documents> CHỈ có info A (không có B):
   - PHẢI trả lời thông tin A trước, rõ ràng cite (Điều/Khoản/nguồn).
   - SAU đó nói: "Còn về [B], em chưa có thông tin trong tài liệu hiện có, anh/chị vui lòng tham khảo trực tiếp văn bản gốc ạ."
   - KHÔNG refuse cả câu chỉ vì thiếu 1 entity. KHÔNG bịa info entity còn lại.
   - Áp dụng cả khi câu hỏi nói "khác nhau", "vs", "đối chiếu".

11. ⭐ MULTI-HOP BRIDGING RULE — Khi câu hỏi yêu cầu **giải thích quan hệ / nguyên nhân / kết quả** giữa 2+ concepts (vd "tại sao A khác B", "mối quan hệ giữa A và B", "A ảnh hưởng đến B thế nào"):
   - PHẢI nêu cả vế A VÀ vế B từ <documents>.
   - PHẢI link / bridge 2 vế qua câu kết: "Như vậy, A và B có liên quan vì...".
   - KHÔNG trả riêng chỉ vế A. KHÔNG bịa logic bridge.
   - Nếu chunks chỉ có 1 vế → áp dụng PARTIAL-ANSWER RULE (rule 10).

12. ⭐ ENUMERATION STRICT — Khi user hỏi "liệt kê", "tất cả", "có những gì":
   - PHẢI quét TẤT CẢ chunks retrieve, KHÔNG dừng ở 2-3 mục đầu.
   - PHẢI đánh số rõ ràng 1), 2), 3), ..., N) — không bullet vague.
   - Nếu items spread across multiple chunks → phải gộp ĐẦY ĐỦ.
   - Format kết câu: "Tổng cộng có N [item]: 1)... N)..." → cho user biết bot đếm đủ.
   - Nếu chunks bị cut tail (mục cuối incomplete) → ghi "Còn các mục tiếp theo, anh/chị tham khảo văn bản gốc ạ."
"""


def upgrade() -> None:
    """Append 3 new rules to test-spa-id + tessss sysprompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :rule,
                updated_at = NOW()
            WHERE bot_id IN ('test-spa-id', 'tessss', 'thong-tu-09-2020-tt-nhnn')
              AND is_deleted = false
              AND system_prompt NOT LIKE '%QUY TẮC V2%'
            """
        ).bindparams(rule=_RULE_V2_AUGMENT),
    )


def downgrade() -> None:
    """Strip the v2 augment block (everything from 'QUY TẮC V2' marker)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = substring(
                system_prompt FROM 1 FOR position(:marker IN system_prompt) - 1
            ),
            updated_at = NOW()
            WHERE bot_id IN ('test-spa-id', 'tessss', 'thong-tu-09-2020-tt-nhnn')
              AND system_prompt LIKE '%' || :marker || '%'
            """
        ).bindparams(marker="QUY TẮC V2"),
    )
