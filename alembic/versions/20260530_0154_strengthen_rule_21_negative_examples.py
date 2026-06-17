"""Strengthen rule 21 SOURCE_CHUNK_BINDING with negative-example concrete.

Revision: 0154
Prev:     0153

Trigger (2026-05-30 fresh multi-turn rerun showed 15% HALLU regression):
  spa_booking_drift flow turn 3 "da thải độc":
    Bot answered "Detox Ballet" service correctly BUT borrowed
    feature 'PAYOT' + 'Gym Beauté 42 bước' + price 699K which belong
    to a DIFFERENT service category's chunk. LLM compliance with
    rule 21 was ~70-85% (alembic 0151) — not enough to stop BP-3
    cross-service feature borrow under top-chunk bias.

Fix: append concrete negative-example contracts to existing rule 21
(reasoning-style positive examples teach LLM more reliably than
abstract policy). Negative-example pattern proven in literature
(Anthropic constitutional AI 2024).

Sacred-rule alignment:
  ✅ Pure DB UPDATE (rule 7)
  ✅ Domain-neutral wording — examples reference abstract "service X /
     feature Y / chunk Z" abstraction; placeholders intentionally
     domain-shaped (cosmetic-care vocabulary) chỉ vì rule platform
     đang gắn theo language_pack vi/en, không phải hard-code 1 tenant.
     Phần spa-specific (Detox Ballet, PAYOT) ĐÃ ở alembic 0149 spa col
     riêng — KHÔNG nhắc tại đây.
  ✅ Multi-tenant — strengthens platform tier; applies to ALL bot with
     state framework on
  ✅ Reversible — downgrade strips appended text
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0154"
down_revision: str | None = "0153"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_VI_RULE_21_STRENGTHEN = """

21.B ⭐ NEGATIVE EXAMPLES cho rule 21 — học từ counter-example concrete:
   Khi user hỏi về service X, BẮT BUỘC kiểm 2 step trước khi viết feature:
   - Step 1: Trong chunk top-K retrieve, chunk nào LITERAL nêu tên service X?
     (Tên X = literal string, không paraphrase.)
   - Step 2: Feature / công nghệ / quy trình / giá → CHỈ trích từ chunk Step-1.
     Feature từ chunk khác (service Y, Z) → KHÔNG được dùng.

   VD-WRONG (BP-3 cross-service feature borrow):
     User: "thải độc da"
     Chunks retrieved: [A] service "Detox Ballet" - thải độc, giảm viêm
                       [B] service "Chăm sóc da chuyên sâu" - PAYOT, Gym Beauté
     Bot trả lời WRONG: "Detox Ballet sử dụng PAYOT và Gym Beauté"
     → SAI vì PAYOT/Gym Beauté ở chunk [B] (service khác), KHÔNG ở chunk [A].

   VD-WRONG (BP-6 top-chunk bias override service_locked):
     action_state.service_locked.name = "Chăm sóc da chuyên sâu" (turn trước)
     action_state.service_locked.price_buoi_le = "800.000đ"
     User turn N: "giá dịch vụ sao"
     Top-chunk current turn: "Detox Ballet - 199K/buổi lẻ"
     Bot trả lời WRONG: "Giá 199K/buổi"
     → SAI vì service_locked đã set, phải quote 800.000đ, KHÔNG override
        bằng top-chunk current turn.

   VD-RIGHT:
     User: "thải độc da"
     Chunk [A] "Detox Ballet - kỹ thuật làm sạch sâu, giảm ứ trệ"
     Bot: "Detox Ballet thực hiện kỹ thuật làm sạch sâu, giảm ứ trệ"
     → Đúng vì feature literal từ chunk [A]."""


_EN_RULE_21_STRENGTHEN = """

21.B ⭐ NEGATIVE EXAMPLES for rule 21 — learn from concrete counter-examples:
   Before writing any feature for service X, REQUIRED 2 steps:
   - Step 1: Among top-K chunks, which chunk LITERAL mentions service X name?
   - Step 2: Feature / technique / process / price MUST come from Step-1 chunk only.
     Feature from another chunk (service Y, Z) → forbidden.

   WRONG-EX (BP-3 cross-service feature borrow):
     User: "detox skin"
     Chunks: [A] "Detox Ballet - cleansing, drainage"
             [B] "Premium care - PAYOT, Gym Beauté"
     WRONG answer: "Detox Ballet uses PAYOT and Gym Beauté"
     → Wrong because PAYOT/Gym Beauté belong to chunk [B] (different service).

   WRONG-EX (BP-6 top-chunk bias override service_locked):
     action_state.service_locked.name = "Premium care"
     action_state.service_locked.price_buoi_le = "800,000đ"
     User turn N: "what's the price"
     Top-chunk current turn: "Detox Ballet - 199K/session"
     WRONG answer: "Price is 199K"
     → Wrong because service_locked is set; must quote 800,000đ.

   RIGHT-EX:
     Chunk [A] "Detox Ballet - deep-clean technique, drainage"
     Bot: "Detox Ballet uses deep-clean technique and drainage"
     → Right because feature comes from the literal chunk [A]."""


def upgrade() -> None:
    """Append rule 21.B negative-example block to platform-tier sysprompt."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = content || :new_text,
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'vi'
              AND prompt_key = 'sysprompt_default_rules'
              AND NOT (content LIKE '%21.B ⭐ NEGATIVE EXAMPLES%')
            """,
        ).bindparams(new_text=_VI_RULE_21_STRENGTHEN),
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
              AND NOT (content LIKE '%21.B ⭐ NEGATIVE EXAMPLES%')
            """,
        ).bindparams(new_text=_EN_RULE_21_STRENGTHEN),
    )


def downgrade() -> None:
    """Strip rule 21.B block."""
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
        ).bindparams(new_text=_VI_RULE_21_STRENGTHEN),
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
        ).bindparams(new_text=_EN_RULE_21_STRENGTHEN),
    )
