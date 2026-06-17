"""Spa sysprompt — synthesis_complete + comparison_verdict rules (Tier 1).

Revision: 0142
Prev:     0141

Trigger (2026-05-29 post-Phase-5 30Q + LLM judge audit on test-spa-id):
  LLM judge gpt-4.1-mini verdict cho test-spa-id sau Phase 0-4 + Phase 5
  hot-fix: 12/12 answered, 0 HALLU, 0 EMPTY — sacred holds. BUT 4/12
  partial verdict because of POLICY gap trong system_prompt:

  Partial #1 — Complex sub-intent drop:
    Q: "Triệt lông cần mấy buổi, ĐAU KHÔNG, có ưu đãi không?"
    A: covers buổi (3-5) + ưu đãi (mua 10 tặng 5) but skip "đau không"
    Reason: LLM ưu tiên primary intent, drop secondary. Sysprompt không
    enforce "câu hỏi nhiều phần phải address ALL parts".

  Partial #3 — Comparison verdict missing:
    Q: "So sánh triệt nách vs triệt chân, vùng nào ĐẮT HƠN?"
    A: liệt kê giá nách 199K + 1/2 chân 599K but missing verdict
    Reason: Rule 10 (PARTIAL-ANSWER) covers "thiếu data → nói rõ" but
    does NOT enforce "comparison phải có kết luận when data complete".

Fix: Append 2 new rules to test-spa-id sysprompt:

  Rule 15 — SYNTHESIS_COMPLETE: enumerate sub-intents in user question,
  address each, mark missing-data ones explicitly. Builds on rule 10
  (partial-answer) by enforcing the multi-part-question audit BEFORE
  answer composition.

  Rule 16 — COMPARISON_VERDICT: when user explicitly asks "cái nào X
  hơn" (which one is more X) AND data is complete for BOTH sides,
  conclude with explicit verdict. When data missing one side, defer to
  rule 10. Generic across "đắt hơn / rẻ hơn / nhanh hơn / phù hợp hơn /
  khác nhau ở đâu".

Multi-tenant fit:
  - Per-bot: only test-spa-id touched. Other bots' sysprompt unchanged.
  - Bot owner self-service via admin UI can edit either rule.
  - Domain-neutral text — applies to any comparison query the bot
    receives, not spa-specific phrasing.

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral (rules generic, no brand reference)
  ✅ Zero-hardcode (text in DB, not in code)
  ✅ Per-bot scope (single bot_id touched)
  ✅ HALLU=0 — rules INCREASE correctness by forcing explicit "thiếu data"
     marker instead of allowing silent drop
  ✅ Reversible — downgrade strips rules 15+16 only
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0142"
down_revision: str | None = "0141"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_NEW_RULES = """

15. ⭐ SYNTHESIS_COMPLETE — Khi câu hỏi user chứa NHIỀU phần (signal: "và", "với", "kèm", "cùng", dấu phẩy giữa các sub-question):
   - BƯỚC 1: Liệt kê (mentally) các sub-intent / sub-question trong câu user.
     Vd "Triệt lông cần mấy buổi, đau không, có ưu đãi?" → 3 sub-intent:
     [số buổi] [đau không] [ưu đãi].
   - BƯỚC 2: PHẢI trả lời ĐỦ TỪNG sub-intent dựa <documents>.
   - BƯỚC 3: Sub-intent nào KHÔNG có data trong chunks → ghi rõ:
     "Về [sub-intent], em chưa có thông tin trong tài liệu, anh/chị
     vui lòng liên hệ trực tiếp để được tư vấn cụ thể ạ."
   - KHÔNG được trả lời chỉ 1-2 phần rồi dừng (LLM ưu tiên primary intent
     và drop secondary là antipattern phổ biến — rule này chặn nó).
   - Áp dụng cho mọi câu hỏi có structure "X, Y, Z?" hoặc "X và Y và Z?".

16. ⭐ COMPARISON_VERDICT — Khi user hỏi so sánh có CÂU HỎI ĐÁNH GIÁ (signal: "cái nào ... hơn", "khác nhau ở đâu", "vùng nào đắt hơn", "loại nào tốt hơn", "phù hợp hơn", "nhanh hơn", "rẻ hơn"):
   - BƯỚC 1: Trả lời thông tin từng entity dựa <documents> (số liệu, đặc điểm).
   - BƯỚC 2: PHẢI có CÂU KẾT LUẬN cuối comparison, format:
     "Như vậy [verdict cụ thể]: A [đắt/rẻ/nhanh] hơn B vì [lý do từ data]."
     hoặc "Như vậy A và B có sự khác biệt ở [điểm A vs B]."
   - KHÔNG dừng ở liệt kê thuần — câu hỏi "cái nào X hơn" CẦN trả lời
     "[entity] hơn vì [data]", KHÔNG để user tự suy luận.
   - Khi chunks CHỈ có data 1 entity (entity còn lại không có trong corpus) →
     áp dụng rule 10 PARTIAL-ANSWER (báo thiếu B, không bịa).
   - Áp dụng cho mọi comparison query, không chỉ giá."""


def upgrade() -> None:
    """Append rules 15+16 to test-spa-id system_prompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :new_rules,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%15. ⭐ SYNTHESIS_COMPLETE%')
            """,
        ).bindparams(new_rules=_NEW_RULES),
    )


def downgrade() -> None:
    """Strip rules 15+16 from test-spa-id system_prompt."""
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
