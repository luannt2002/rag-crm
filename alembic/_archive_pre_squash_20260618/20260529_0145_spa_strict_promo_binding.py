"""Spa sysprompt — STRICT_PROMO_BINDING (rule 19, anti-fabrication for promo cross-service).

Revision: 0145
Prev:     0144

Trigger (2026-05-29 deep manual ground-truth audit on test-spa-id Q#8):
  Probe Q: "Anh Linh muốn đặt massage thư giãn"
  Bot answer included 3 services with promo:
    - Massage cổ vai gáy 60p: 99K (gốc 400K)   ← corpus TRUE
    - Massage body 70p:        99K (gốc 350K)   ← FABRICATED
    - Massage chân 70p:        99K (gốc 350K)   ← corpus TRUE

  Ground truth corpus chunks:
    Cổ vai gáy promo: 99K/buổi (gốc 400K) 60p   — chunk 1
    Body promo:       299K/buổi (gốc 600K) 60p   — chunk 2 (NOT in retrieve top-K)
    Chân promo:       99K/buổi (gốc 350K) 70p   — chunk 3

  Retrieve top-K returned chunks for cổ vai gáy + chân but MISSED the body
  promo chunk. Bot then INFERRED body promo by averaging the two chunks
  it had — produced "99K/350K/70p" for body, which is the chân chunk's
  copy/paste, not body's data.

  Existing rules 10 (PARTIAL_ANSWER), 14 (ANTI_CROSS_SERVICE), 17
  (ANTI_CSV_ROW_CONFLATE) all address conflate WITHIN A CHUNK. None
  addresses conflate ACROSS CHUNKS when the chunk for entity X is
  missing from retrieve top-K.

Fix: Append rule 19 STRICT_PROMO_BINDING — instruct LLM that promo data
(promo price, original price, duration, conditions) MUST be quoted
literally from a chunk that mentions the EXACT service name. When the
chunk for service X has no promo content, fall through to rule 10
PARTIAL_ANSWER (admit "chưa có thông tin khuyến mãi cho X") instead of
borrowing promo from a similar service.

Domain-neutral design (multi-tenant native):
- Rule text references abstract concepts ("entity", "promo / khuyến
  mãi / ưu đãi", "literal name match", "neighbouring chunk") — applies
  to any bot with promotion-priced products: spa services, e-commerce
  SKUs, legal-services consultation fees, edu-tutor packages, finance
  loan APRs.
- Per-bot scope: alembic UPDATE WHERE bot_id='test-spa-id' AND
  channel_type='web'. Other bots/tenants/workspaces unchanged.
- Owner self-service: rule editable via admin UI.

Sacred-rule alignment:
- HALLU=0: directly closes the cross-chunk promo fabrication pattern
  empirically verified 2026-05-29 in spa Q#8.
- Domain-neutral: rule text generic (no spa / massage / industry term).
- Zero-hardcode: text in DB (bots.system_prompt), not in code.
- Per-bot scope: single bot_id touched; other tenants unaffected.
- Multi-tenant scaling: same rule can be appended to any tenant's bot
  via separate alembic when their corpus exhibits promo cross-service
  pattern.
- CLAUDE.md rule 7: pure alembic.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0145"
down_revision: str | None = "0144"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_NEW_RULE = """

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
    """Append rule 19 to test-spa-id system_prompt (idempotent)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = system_prompt || :new_rule,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
              AND NOT (system_prompt LIKE '%19. ⭐ STRICT_PROMO_BINDING%')
            """,
        ).bindparams(new_rule=_NEW_RULE),
    )


def downgrade() -> None:
    """Strip rule 19 from test-spa-id system_prompt."""
    op.execute(
        text(
            """
            UPDATE bots
            SET system_prompt = REPLACE(system_prompt, :new_rule, ''),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ).bindparams(new_rule=_NEW_RULE),
    )
