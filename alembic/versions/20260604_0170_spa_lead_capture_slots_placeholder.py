"""Demo spa bot: replace verbatim booking examples with {captured_slots} binding.

Revision: 0170
Prev:     0169

Two problems in the spa bot's system_prompt, both rooted in hand-rolled
prompt-based slot-filling (a workaround written while the structured slot
machine was dead — now fixed via Phase 0 + 0169 + conversation_id wiring):

  1. Rule 13 (BOOKING_SLOT_GUIDE) and rule 18 (INLINE_SLOT_CAPTURE) each embed
     a VERBATIM Vietnamese example sentence. The LLM copies them word-for-word
     when asking the customer for booking info; the output ``system_leak``
     guardrail hashes 24-word shingles of the system_prompt and matches the
     echoed sentence → the booking turn is blocked and replaced with the
     refusal template (observed: T4 "muốn đặt lịch" → answer_type=blocked,
     rule_id=system_leak). This is the documented verbatim-example trap.

  2. The prompt never references the structured slot state, so the LLM cannot
     see which slots are already captured and re-asks for them.

Fix (owner self-service, sacred-rule 10 + feedback_sysprompt_verbatim_example):
replace each verbatim example with a BEHAVIOURAL directive (no copyable answer
sentence) and bind the real captured-slot DATA via the ``{captured_slots}``
placeholder, which the generate node substitutes at runtime. The LLM now asks
only for slots in the ``missing:`` list and stops re-asking captured ones, and
produces no verbatim sentence for the shingle detector to match.

Idempotent (REPLACE no-ops if the verbatim string is already gone). Reversible.
Sacred-rule: alembic config (rule 7); bot slug is data; feature generic to any
bot using action_config + the placeholder.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0170"
down_revision: str | None = "0169"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOT = "test-spa-id"

# Exact verbatim example strings currently in the prompt (quotes included).
_OLD_BOOKING = '"Để em hỗ trợ đặt lịch, anh/chị cho em xin: họ tên, số điện thoại, thời gian (giờ/ngày) muốn đến ạ."'
_OLD_INLINE = '"Dạ, em ghi nhận: dịch vụ [X], thời gian [Y]. Anh/chị cho em xin thêm [slot còn thiếu]."'

# Behavioural replacements (no copyable answer sentence; reference live slot
# DATA via the placeholder). ``missing:`` / ``none`` are neutral markers.
_NEW_BOOKING = (
    "Slot khách đã cung cấp: {captured_slots}. CHỈ hỏi các slot sau \"missing:\", "
    "tự diễn đạt tự nhiên bằng lời của em — KHÔNG đọc nguyên văn hướng dẫn này, "
    "KHÔNG hỏi lại slot đã có giá trị."
)
_NEW_INLINE = (
    "Khi \"missing: none\" (đã đủ slot bắt buộc), tóm tắt lại thông tin đặt lịch "
    "để khách xác nhận; nếu còn thiếu, chỉ hỏi slot trong \"missing:\". Tự diễn "
    "đạt, KHÔNG lặp nguyên văn."
)


def upgrade() -> None:
    op.execute(
        text("""
            UPDATE bots SET
                system_prompt = REPLACE(REPLACE(system_prompt, :ob, :nb), :oi, :ni),
                updated_at = NOW()
            WHERE bot_id = :bot
        """).bindparams(ob=_OLD_BOOKING, nb=_NEW_BOOKING, oi=_OLD_INLINE, ni=_NEW_INLINE, bot=_BOT)
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE bots SET
                system_prompt = REPLACE(REPLACE(system_prompt, :nb, :ob), :ni, :oi),
                updated_at = NOW()
            WHERE bot_id = :bot
        """).bindparams(ob=_OLD_BOOKING, nb=_NEW_BOOKING, oi=_OLD_INLINE, ni=_NEW_INLINE, bot=_BOT)
    )
