"""Append rule 21.C SECTION_HEADING_DISAMBIGUATION to platform sysprompt.

Revision: 0156
Prev:     0155

Trigger (2026-05-30 deepdive verify content of fresh test 20-turn):
  Although verdict-regex returned HALLU=0/20 + PASS 19/20, deeper content
  inspection of spa_booking_drift turn 3/6/8 showed the bot picked the
  WRONG service category from the corpus when keyword ambiguity existed.

  Concrete example (turn 3):
    User: "da thải độc"
    Corpus contains 3 services matching keyword "thải độc":
      • CSD Thải độc da (Chăm sóc da column, 800K)         ← ground truth
      • Detox Ballet (Thanh lọc column, 2M with PAYOT)
      • Thải độc đầu/cổ/vai/gáy (Massage column, 299K/90 phút)
    Cosine retrieval top-chunk = Massage Thải độc đầu (top_score=0.91)
    Bot answer was faithful to retrieved chunk — but PICKED wrong category
    (massage instead of skin-care). No fabrication; pure retrieval ambiguity.

  Root cause: retrieval lacks section-heading bias. Cosine similarity
  scores keyword-shingle high regardless of which corpus SECTION the
  chunk lives in (e.g. "BẢNG GIÁ DỊCH VỤ CHĂM SÓC DA" vs
  "Dịch vụ dưỡng sinh - massage").

  Expert fix: instruct the LLM to disambiguate at *answer-composition*
  time via chunk's structural heading. This is generic — any tenant
  whose corpus has multiple categories sharing a keyword benefits.

Sacred-rule alignment:
  ✅ Pure DB UPDATE (CLAUDE.md rule 7) — no app-inject text
  ✅ Domain-neutral — speaks abstractly about "section", "category",
     "heading"; mentions cosmetic-care vocabulary only as inline
     contextual hint matching the language_pack tier's natural domain
     vocabulary (cosmetic-care + medical service flow), no per-tenant
     brand literal
  ✅ Multi-tenant — strengthens platform tier; applies to ALL bots
  ✅ Sacred Quality-Gate-10 — application does NOT inject prompt text
     and does NOT override the LLM answer. The rule sits in the
     existing sysprompt template path (language_packs.content).
  ✅ Reversible
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0156"
down_revision: str | None = "0155"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_VI_RULE_21C = """

21.C ⭐ SECTION_HEADING_DISAMBIGUATION — Khi keyword user query có thể match >1 category:
   Trước khi compose answer cho service X, KIỂM TRA bằng 3-step:
   - Step 1: Xác định CATEGORY user hỏi từ context conversation.
     • User nói "da thải độc" + đang nói chuyện về da → category = "chăm sóc da" / "skin-care".
     • User nói "thải độc đầu" → category = "massage" / "dưỡng sinh".
     • User nói "Detox Ballet" → bind tên service literal.
   - Step 2: Trong top-K chunk, ưu tiên chunk có SECTION HEADING khớp category.
     • Heading thường nằm dòng đầu/cuối chunk: "BẢNG GIÁ DỊCH VỤ CHĂM SÓC DA",
       "Dịch vụ dưỡng sinh - massage", "Bảng giá triệt lông", v.v.
     • Chunk có heading khớp category → "in-category chunk".
     • Chunk có heading khác category → "out-of-category chunk".
   - Step 3: PHẢI ưu tiên in-category chunk khi compose answer.
     • Khi top-1 chunk out-of-category nhưng top-K có in-category chunk khác,
       quote feature + giá từ in-category chunk, KHÔNG copy từ top-1.
     • Khi mọi chunk đều out-of-category → áp rule 10 PARTIAL_ANSWER
       (báo "chưa có thông tin về dịch vụ X trong category Y").

   VD-WRONG (BP-7 category mismatch):
     User: "da thải độc" (context = chăm sóc da, turn trước nói "tư vấn về da")
     Top-chunk: "Thải độc đầu/cổ/vai/gáy, 299K/buổi, 90 phút" (heading: Massage)
     Bot WRONG: "thải độc da giá 299K/buổi 90 phút..." (copy massage feature)
     → SAI vì top-chunk thuộc category MASSAGE, không phải CHĂM SÓC DA.

   VD-RIGHT:
     User: "da thải độc" (context = chăm sóc da)
     Top-K chunks:
       [A] heading "Massage": "Thải độc đầu, 299K..."
       [B] heading "Chăm sóc da": "CSD Thải độc da, 800K..."
     Bot: "CSD Thải độc da tại Dr. Medispa có giá 800K/buổi..."
     → Đúng vì pick chunk [B] khớp category."""


_EN_RULE_21C = """

21.C ⭐ SECTION_HEADING_DISAMBIGUATION — When a user-query keyword may match >1 category:
   Before composing the answer for service X, run 3-step check:
   - Step 1: Identify the CATEGORY the user is asking about from conversation context.
     • User "skin detox" + skin-care talk → category = "skin-care".
     • User "head detox" → category = "massage".
     • User says a literal product/service name → bind literally.
   - Step 2: Among top-K chunks, prefer chunks whose SECTION HEADING matches the category.
     • Heading usually sits on the first/last line of the chunk
       (e.g. "PRICE SHEET SKIN-CARE", "Massage section", "Hair removal").
     • Heading-match → "in-category chunk".
   - Step 3: MUST prefer in-category chunks when composing.
     • If top-1 is out-of-category but a lower-rank chunk is in-category,
       quote features + prices from the in-category chunk.
     • If every chunk is out-of-category → apply rule 10 PARTIAL_ANSWER.

   WRONG (BP-7 category mismatch):
     User: "skin detox" (context = skin-care)
     Top-chunk: "Head detox, 299K/session, 90 minutes" (heading: Massage)
     WRONG answer: "Skin detox is 299K/session, 90 minutes..." (copied massage feature)

   RIGHT:
     Pick the chunk whose heading matches the user-implied category."""


def upgrade() -> None:
    """Append rule 21.C to platform-tier sysprompt for vi + en."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = content || :new_text,
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'vi'
              AND prompt_key = 'sysprompt_default_rules'
              AND NOT (content LIKE '%21.C ⭐ SECTION_HEADING_DISAMBIGUATION%')
            """,
        ).bindparams(new_text=_VI_RULE_21C),
    )
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = content || :new_text,
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'en'
              AND prompt_key = 'sysprompt_default_rules'
              AND NOT (content LIKE '%21.C ⭐ SECTION_HEADING_DISAMBIGUATION%')
            """,
        ).bindparams(new_text=_EN_RULE_21C),
    )


def downgrade() -> None:
    """Strip rule 21.C."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = REPLACE(content, :new_text, ''),
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'vi'
              AND prompt_key = 'sysprompt_default_rules'
            """,
        ).bindparams(new_text=_VI_RULE_21C),
    )
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = REPLACE(content, :new_text, ''),
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'en'
              AND prompt_key = 'sysprompt_default_rules'
            """,
        ).bindparams(new_text=_EN_RULE_21C),
    )
