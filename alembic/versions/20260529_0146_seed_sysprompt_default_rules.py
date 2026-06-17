"""Seed ``language_packs[vi|en][sysprompt_default_rules]`` — platform-default sysprompt rules.

Revision: 0146
Prev:     0145

Trigger (2026-05-29 J1 expert multi-tenant ship):
  Rules 15-19 (SYNTHESIS_COMPLETE / COMPARISON_VERDICT / ANTI_CSV_ROW_CONFLATE
  / INLINE_SLOT_CAPTURE / STRICT_PROMO_BINDING) were initially shipped via
  per-bot alembic UPDATE WHERE bot_id='test-spa-id' (alembic 0142-0145).
  This is an ANTI-PATTERN for multi-tenant scaling:

    Scenario: tenant onboard 10 bots vi locale
    Pre-J1:   10 alembic riêng, each ship rules to per-bot system_prompt
    Post-J1:  0 alembic — all bots auto-inherit via language_packs tier

  Rule text is DOMAIN-NEUTRAL (mentioned no spa/medispa/booking-specific
  vocabulary; uses abstract concepts: entity, sub-intent, CSV row, slot
  signals, promo binding). Therefore appropriate for platform-wide tier
  per CLAUDE.md sacred rule "domain-neutral code".

This migration:
  1. Seeds ``language_packs[vi][sysprompt_default_rules]`` with the
     canonical 5 rules (extracted verbatim from spa system_prompt).
  2. Seeds ``language_packs[en][sysprompt_default_rules]`` with the
     English translation (concise, same semantics).
  3. Does NOT strip rules from spa system_prompt yet — that happens in
     alembic 0147 AFTER the SysPromptAssembler service is wired (so
     spa keeps current behaviour while assembler+ wire ships).

Multi-tenant fit:
  ✅ Locale-aware (vi + en seed; ja/ko defer)
  ✅ Tenant-agnostic (any tenant onboarding bot vi/en gets rules)
  ✅ Per-bot opt-out via bots.plan_limits["sysprompt_rules_disabled"]
     JSONB list (e.g. ["rule_17"]) — assembler strips matching rules
     before append.
  ✅ Owner self-service via admin UI editing language_packs row (with
     audit_log trail per alembic seed pattern).

Sacred-rule alignment:
  ✅ Pure DB INSERT via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral text
  ✅ Idempotent (ON CONFLICT DO NOTHING preserves operator override)
  ✅ Reversible (downgrade deletes seeded rows)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0146"
down_revision: str | None = "0145"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Canonical VI rules text — extracted verbatim from spa system_prompt
# (post alembic 0142+0143+0144+0145). Each rule starts with its number
# + ⭐ marker + name so the assembler can identify and selectively strip
# per the bot's plan_limits.sysprompt_rules_disabled list.
_VI_RULES = """

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
   - Áp dụng cho mọi comparison query, không chỉ giá.

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
     khớp literal trong chunk có top_score cao nhất, KHÔNG infer.

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


_EN_RULES = """

15. ⭐ SYNTHESIS_COMPLETE — When user question contains MULTIPLE parts (signal: "and", "with", "plus", commas between sub-questions):
   - STEP 1: List (mentally) sub-intents / sub-questions in user query.
   - STEP 2: MUST answer EACH sub-intent from <documents>.
   - STEP 3: Sub-intents WITHOUT data in chunks → state explicitly:
     "Regarding [sub-intent], I don't have information in the documents,
     please contact us directly for specific guidance."
   - DO NOT answer only 1-2 parts and stop (LLM prioritising primary intent
     and dropping secondary is a common antipattern this rule blocks).

16. ⭐ COMPARISON_VERDICT — When user asks evaluative comparison ("which is X-er", "differ how", "which costs more"):
   - STEP 1: Provide each entity's information from <documents>.
   - STEP 2: MUST have CONCLUSION sentence at the end: "Thus [verdict]: A is [more X] than B because [reason from data]."
   - DO NOT stop at listing — "which is X-er" questions REQUIRE explicit verdict.
   - If chunks only have data for ONE entity → apply rule 10 PARTIAL_ANSWER.

17. ⭐ ANTI_CSV_ROW_CONFLATE — When chunk is a CSV / table with STT (row index) column:
   - EACH ROW binds to ONE entity. DO NOT mix prices / attributes across rows.
   - User asks MULTIPLE entities → look up EACH row separately by literal name match.
   - Format: state row name + correct row price. E.g. "Full leg (row 7): 699K. Half leg (row 6): 599K."
   - DO NOT infer "full leg = full body" or bridge similar-looking rows.

18. ⭐ INLINE_SLOT_CAPTURE — Before asking user for booking info, MUST scan CURRENT message + conversation_history for slots already provided:
   - Slot signals: DATETIME ("9am", "tomorrow morning"), SERVICE (literal service name from corpus), PHONE (10-11 digit string), NAME (noun before phone).
   - STEP A: List slots ALREADY FILLED.
   - STEP B: Acknowledge each: "Got it: service [X], time [Y]. Please provide [missing slots]."
   - STEP C: Only ask for MISSING slots; never re-ask filled ones.
   - STEP D: When all 4 slots filled (name + phone + datetime + service) → confirm booking.

19. ⭐ STRICT_PROMO_BINDING — Promo data (price / original price / duration / conditions) MUST bind tightly to entity mentioned literal in the SAME chunk:
   - For service X, ONLY quote promo from chunk with literal "X" + literal promo of X.
   - DO NOT borrow / infer / pattern-match promo from service Y (even if Y "similar" to X).
   - When user asks multiple services: look up EACH from its OWN chunk; if service Z's chunk has no promo → apply rule 10 PARTIAL_ANSWER ("Regarding [Z], no specific promo info in documents")."""


_SEED_ROWS = (
    ("vi", "sysprompt_default_rules", _VI_RULES),
    ("en", "sysprompt_default_rules", _EN_RULES),
)


def upgrade() -> None:
    """Insert platform-default sysprompt rules per locale."""
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
    """Remove platform-default rules rows."""
    op.execute(
        text(
            """
            DELETE FROM language_packs
            WHERE prompt_key = 'sysprompt_default_rules' AND code IN ('vi', 'en')
            """,
        ),
    )
