"""Seed sysprompt rules 20+21+22 into ``language_packs[vi|en].sysprompt_default_rules``.

Revision: 0151
Prev:     0150b

Trigger (2026-05-30 X2 BUNDLED ship step 10):
  Tier 2 conversation state introduces ``state["action_state"]`` dict
  with ``service_locked`` + ``slots_filled`` fields. LLM must reference
  these literally via existing sysprompt template pattern (no app-side
  prompt prepend — sacred-rule preservation).

Rules added (platform tier — apply to ALL bot bật action_config):

  20 STATE_ENFORCEMENT — service_locked + slots_filled literal lock.
     When action_state.service_locked.name is set, MUST quote literal
     service name + price across all turns. NO override from current
     turn's top-chunk bias.

  21 SOURCE_CHUNK_BINDING — feature/technique attribution.
     Feature (PAYOT, Diode Laser, AI 17 chỉ số, etc.) MUST bind to the
     chunk that LITERAL mentions service X. NO cross-service feature
     borrow. Fixes BP-3 (cross-service feature gán catch in 2026-05-29
     evening UI test: PAYOT/Gym Beauté gán cho Thải độc da thực là
     của Detox Ballet).

  22 ALLOWED_FACTS_PASSTHROUGH — basic info quote (per-bot custom_vocabulary).
     Address, hours, hotline literal from bots.custom_vocabulary.allowed_facts.
     Fixes BP-5 (wrong refuse địa chỉ).

Sacred-rule alignment:
  ✅ Pure DB UPDATE (CLAUDE.md rule 7)
  ✅ Domain-neutral text — abstract concepts (service, feature, fact)
     not spa/medispa/booking-specific
  ✅ Multi-tenant — rules apply when bot has state framework enabled
  ✅ Reversible — downgrade re-seeds prior content
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0151"
down_revision: str | None = "0150b"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_VI_NEW_RULES = """

20. ⭐ STATE_ENFORCEMENT — Khi ``action_state.service_locked`` đã set (LITERAL từ turn trước):
   - Dịch vụ đã chốt = ``action_state.service_locked.name``
   - Giá đã quote = ``action_state.service_locked.price_buoi_le``
   - Source chunk = ``action_state.service_locked.source_chunk_id``
   - LUẬT NGHIÊM: MỌI câu trả lời về "dịch vụ này / cái này / nó" PHẢI giữ chính xác tên + giá literal trên.
   - KHÔNG override bằng chunk top-score current turn nếu state đã set.
   - KHÔNG fabricate service name lai (merge 2 service đã nhắc trong history).
   - Chỉ thay đổi ``service_locked`` khi user EXPLICIT yêu cầu ("tôi muốn dịch vụ X khác", "đổi sang Y", "thôi không muốn X nữa").
   - Khi service_locked chưa set + user chưa chọn rõ → áp dụng rule 13 (hỏi clarify) thay vì tự chọn.

21. ⭐ SOURCE_CHUNK_BINDING — Đặc tính / feature / công nghệ / quy trình của service X PHẢI lấy từ chunk LITERAL mention tên service X:
   - VD: "PAYOT", "Gym Beauté 42 bước", "Diode Laser", "AI 17 chỉ số", "10 bước chuẩn y khoa" CHỈ apply cho service mà chunk literal nêu.
   - KHÔNG copy feature từ chunk service Y áp cho service X (kể cả service "tương tự" về category).
   - Khi user hỏi nhiều service trong 1 response, tra cứu feature TỪNG service từ chunk RIÊNG có literal tên service đó.
   - Service không có chunk match → áp dụng rule 10 PARTIAL_ANSWER (báo thiếu thông tin).

22. ⭐ ALLOWED_FACTS_PASSTHROUGH — Thông tin cơ bản về spa (địa chỉ, giờ mở cửa, hotline, link bản đồ):
   - Nếu ``bots.custom_vocabulary.allowed_facts`` có key tương ứng (address, hours, hotline, maps):
     → Quote LITERAL từ allowed_facts.{key} khi user hỏi.
     → KHÔNG refuse, KHÔNG modify, KHÔNG paraphrase.
   - Nếu key không có trong allowed_facts → áp dụng rule 10 PARTIAL_ANSWER (báo chưa có info, mời liên hệ).
   - Multi-tenant: bot owner declare allowed_facts per-bot qua admin UI; chính sách chính thức nằm trong DB không hardcode."""


_EN_NEW_RULES = """

20. ⭐ STATE_ENFORCEMENT — When ``action_state.service_locked`` is set (LITERAL from earlier turn):
   - Locked service name = ``action_state.service_locked.name``
   - Locked price = ``action_state.service_locked.price_buoi_le``
   - All subsequent answers about "this service / it" MUST keep the exact locked name + price.
   - Do NOT override with top-chunk of current turn when state is set.
   - Do NOT fabricate hybrid service names by merging two earlier-mentioned services.
   - Change service_locked ONLY when user EXPLICITLY requests switch.

21. ⭐ SOURCE_CHUNK_BINDING — Feature / technique / procedure of service X must come from a chunk LITERAL mentioning X:
   - Do NOT copy features from chunk Y to service X (even similar-category).
   - When listing multiple services, fetch features per-service from its OWN chunk.
   - Service with no matching chunk → apply rule 10 PARTIAL_ANSWER.

22. ⭐ ALLOWED_FACTS_PASSTHROUGH — Basic facts (address, hours, hotline, maps):
   - If ``bots.custom_vocabulary.allowed_facts`` carries the key → quote literally.
   - Otherwise apply rule 10 PARTIAL_ANSWER.
   - Owner declares facts per-bot via admin UI."""


def upgrade() -> None:
    """Append rules 20+21+22 to platform-tier sysprompt_default_rules."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = content || :new_rules,
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'vi'
              AND prompt_key = 'sysprompt_default_rules'
              AND NOT (content LIKE '%20. ⭐ STATE_ENFORCEMENT%')
            """,
        ).bindparams(new_rules=_VI_NEW_RULES),
    )
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = content || :new_rules,
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'en'
              AND prompt_key = 'sysprompt_default_rules'
              AND NOT (content LIKE '%20. ⭐ STATE_ENFORCEMENT%')
            """,
        ).bindparams(new_rules=_EN_NEW_RULES),
    )


def downgrade() -> None:
    """Strip rules 20+21+22 from platform-tier."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = REPLACE(content, :new_rules, ''),
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'vi'
              AND prompt_key = 'sysprompt_default_rules'
            """,
        ).bindparams(new_rules=_VI_NEW_RULES),
    )
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = REPLACE(content, :new_rules, ''),
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'en'
              AND prompt_key = 'sysprompt_default_rules'
            """,
        ).bindparams(new_rules=_EN_NEW_RULES),
    )
