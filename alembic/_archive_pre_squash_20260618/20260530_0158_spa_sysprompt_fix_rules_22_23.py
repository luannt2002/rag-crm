"""Append rules 22 + 23 to test-spa-id system_prompt — hotline literal + service exact-match.

Revision: 0158
Prev:     0157

Trigger (load test 120Q verify 2026-05-30):
  test-spa-id had 2 remaining issues in the 120-question load test
  (everything else perfect, faithfulness = 1.0):

  spa-05 "dr. medispa hotline số nào" → bot refused honest
    Cause: hotline literal "0926.559.268" exists in REFUSAL TEMPLATE
    (rule 5) but no rule instructs the bot to quote it in a direct
    answer when user asks. Platform-tier rule 22
    ALLOWED_FACTS_PASSTHROUGH was shipped (alembic 0151) but the
    template variable wire is missing in the sysprompt-builder code
    path — see commit `f0c88b4` note. Until that wire ships, the
    per-bot sysprompt needs explicit hotline-quote instruction.

  spa-07 "chăm sóc da chuyên sâu giá bao nhiêu" → bot answered 800K
    Cause: 5 services in corpus share substring "Chăm sóc da":
      - Chăm sóc da chuyên sâu (700K)        ← user asked
      - Trị mụn chuyên sâu (700K)
      - Chăm sóc da cấp ô xi tươi (800K)
      - Chăm sóc da thải độc da (800K)
      - Chăm sóc da cấp nước đa tầng (800K)
    Cosine retrieval returned all 5; LLM biased by majority (3/5 = 800K).
    Rule 14 ANTI_CROSS_SERVICE prefers chunks with service literal but
    "chăm sóc da" matches all 5. Need rule for EXACT FULL LITERAL match.

Sacred-rule alignment:
  ✅ Pure alembic UPDATE (CLAUDE.md rule 7) — no psql hot-fix
  ✅ Per-bot scope (only test-spa-id)
  ✅ Reversible — downgrade strips appended rules
  ✅ NO app-inject — rule lives in bots.system_prompt (DB column,
     owner-editable via admin UI)
  ✅ NO app-override — rule guides LLM compose, not application code

Notes:
  - These per-bot rules are TEMPORARY mitigation. Long-term fix is:
    (a) Wire {{ allowed_facts }} into sysprompt-builder template
        (eliminates spa-05 root cause; covers all tenants).
    (b) Ship platform-tier rule 21.D NAME_EXACT_MATCH in language_packs
        (eliminates spa-07 root cause; covers all tenants).
  - Hotline literal `0926.559.268` already exists in test-spa-id
    sysprompt (rule 5 REFUSAL TEMPLATE) — appending it again here
    creates a duplicate but ensures the literal is visible to the LLM
    in the "answer-directly" code path, not just the refuse path.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0158"
down_revision: str | None = "0157"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_APPEND_RULES = """

═══════════════════════════════════════════════════════════
QUY TẮC FACT_QUOTE + EXACT_SERVICE_MATCH (2026-05-30):
═══════════════════════════════════════════════════════════

22. ⭐ FACT_QUOTE_LITERAL — Khi user hỏi info cơ bản về Dr. Medispa:
   - Hotline / Số điện thoại / SĐT → trả lời LITERAL: "0926.559.268"
   - Địa chỉ / địa điểm / "ở đâu" → trả lời LITERAL: "Số 102 Vũ Trọng Phụng, Thanh Xuân, Hà Nội (đi thang bộ lên tầng 2)"
   - Giờ mở cửa / mở cửa lúc nào → trả lời LITERAL: "9-21h, từ Thứ Hai đến Chủ Nhật"
   - Maps / link bản đồ → "https://maps.app.goo.gl/Vo5sw3iHtZZWbVN9A"

   QUY TẮC:
   - KHÔNG refuse các câu hỏi info cơ bản trên dù <documents> không có chunk match.
   - KHÔNG paraphrase, KHÔNG modify giá trị literal.
   - Cite ngắn cuối câu: "(theo thông tin Dr. Medispa)".

   VD ĐÚNG:
   - User: "hotline số nào" → "Hotline Dr. Medispa là 0926.559.268 ạ (theo thông tin Dr. Medispa)."
   - User: "địa chỉ ở đâu" → "Dr. Medispa ở số 102 Vũ Trọng Phụng, Thanh Xuân, Hà Nội ạ."

23. ⭐ EXACT_SERVICE_NAME_MATCH — Khi user nêu LITERAL tên dịch vụ ĐẦY ĐỦ:
   Câu hỏi có tên service literal nhiều từ (vd "chăm sóc da chuyên sâu", "chăm sóc da cấp ô xi tươi", "trị mụn chuyên sâu", "trẻ hóa da", "Detox Ballet", "Hydra Ballet").

   QUY TẮC:
   - Trong top-K chunks, MATCH chunk có chứa TOÀN BỘ literal tên service user nêu (sequence chính xác, không tách rời).
   - CHỈ quote giá / quy trình / feature từ chunk khớp tên đầy đủ đó.
   - KHÔNG bias theo majority — nếu 4/5 chunks có giá 800K cho service KHÁC nhưng 1 chunk có literal "Chăm sóc da chuyên sâu 700K" → PHẢI quote 700K.
   - KHÔNG dùng substring match (vd "chăm sóc da" trùng nhiều service) — tên đầy đủ literal mới valid.
   - Khi không có chunk nào khớp tên đầy đủ literal user nêu → áp dụng rule 10 PARTIAL_ANSWER (refuse honest).

   VD ĐÚNG:
   - Top-K chunks:
     [A] "1, Chăm sóc da chuyên sâu, 700.000"
     [B] "3, Chăm sóc da cấp ô xi tươi, 800.000"
     [C] "4, Chăm sóc da thải độc da, 800.000"
   - User: "giá chăm sóc da chuyên sâu"
   - ĐÚNG: "Dịch vụ chăm sóc da chuyên sâu giá 700.000đ/buổi ạ (theo bảng giá CSD công nghệ cao)."
   - SAI: "Giá 800K/buổi" ← bias theo majority chunks B+C.

   VD ĐÚNG khi không khớp literal:
   - User: "giá chăm sóc da deluxe" (KHÔNG có service tên "deluxe" trong corpus)
   - ĐÚNG: "Em chưa có thông tin về dịch vụ 'chăm sóc da deluxe' tại Dr. Medispa. Anh/chị vui lòng cung cấp thêm thông tin hoặc liên hệ trực tiếp 0926.559.268 ạ."
"""


def upgrade() -> None:
    """Append rules 22 + 23 to test-spa-id system_prompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :rules,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%22. ⭐ FACT_QUOTE_LITERAL%')
            """,
        ).bindparams(rules=_APPEND_RULES),
    )


def downgrade() -> None:
    """Strip rules 22 + 23."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = REPLACE(system_prompt, :rules, ''),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
            """,
        ).bindparams(rules=_APPEND_RULES),
    )
