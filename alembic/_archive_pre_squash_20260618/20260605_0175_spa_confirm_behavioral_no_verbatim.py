"""Owner sysprompt: de-verbatim the booking CONFIRM block (system_leak fix).

Revision: 0175
Prev:     0174

Multi-turn forensic (test-spa-id lead-capture flow, evidence in
scripts/measure_booking_confirm.py) found the booking-completion turn fails:
when all four slots are filled ("missing: none"), the model reproduces the
VERBATIM confirmation template embedded in rule 13 BƯỚC 3 —

    "Dr. Medispa xác nhận lịch:\\n- Tên: [name]\\n- SĐT: [phone]
     \\n- Thời gian: [time]\\n- Dịch vụ: [service]
     \\n- Địa chỉ: 102 Vũ Trọng Phụng, Thanh Xuân, HN"

That ~24-word literal matches a shingle of the bot's own system_prompt, so the
OUTPUT guardrail's ``system_prompt_leak`` regex short-circuits the answer to
``answer_type=blocked`` (returns the oos template) — the booking never confirms.
Evidence: answer_reason='Output guardrail blocked'; math_lockdown was warn-only
(ruled out); grounding ratio 0.5 == threshold 0.5 with strict ``>`` (ruled out).
This is the verbatim-example trap (memory feedback_sysprompt_verbatim_example):
a fixed multi-word Vietnamese template in system_prompt that the LLM echoes
verbatim collides with the leak shingle hash.

Fix at the CORRECT layer (CLAUDE.md sacred-rule 10): the bot OWNER's
system_prompt (single source of truth) describes the confirmation BEHAVIOURALLY
— compose it in the model's own words, no fixed template, address/hotline drawn
from the documents — instead of pinning an exact string. No platform inject, no
answer override. The spa address literal also leaves the prompt (it belongs in
the corpus, where the model can ground it).

Idempotent: regex matches the verbatim span only; absent → no-op. Reversible:
downgrade restores the original template. Rule 7 (alembic, not psql).
"""
from __future__ import annotations

import re

from alembic import op
from sqlalchemy import text

revision: str = "0175"
down_revision: str | None = "0174"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOT = "test-spa-id"

# Matches BƯỚC 3 intro + the quoted verbatim template through the address
# literal, tolerant of the stored escaped "\n" + leading whitespace.
_OLD_RE = re.compile(
    r'Nếu user cung cấp đủ tên \+ SĐT \+ thời gian → CONFIRM ngay với format:'
    r'.*?Thanh Xuân, HN"',
    re.DOTALL,
)

# Behavioural replacement — no fixed template, model composes its own wording so
# it cannot shingle-match the system_prompt. Address/hotline come from the docs.
_NEW = (
    'Khi đã đủ tên + SĐT + thời gian + dịch vụ (tức {captured_slots} báo '
    '"missing: none") → CHỐT LỊCH NGAY bằng LỜI CỦA EM: tự diễn đạt tự nhiên, '
    'KHÔNG copy theo mẫu cố định nào, KHÔNG xuống dòng theo khuôn — xác nhận lại '
    'đầy đủ tên khách, SĐT, thời gian và dịch vụ đã chọn, đồng thời nhắc địa chỉ '
    'và hotline spa lấy từ tài liệu. TUYỆT ĐỐI KHÔNG hỏi lại slot đã có giá trị'
)

# Reverse direction restores the original verbatim template (stored escaped \n).
_ORIG_TEMPLATE = (
    'Nếu user cung cấp đủ tên + SĐT + thời gian → CONFIRM ngay với format:\n'
    '     "Dr. Medispa xác nhận lịch:\\n- Tên: [name]\\n- SĐT: [phone]'
    '\\n- Thời gian: [time]\\n- Dịch vụ: [service]'
    '\\n- Địa chỉ: 102 Vũ Trọng Phụng, Thanh Xuân, HN"'
)


def upgrade() -> None:
    bind = op.get_bind()
    sp = bind.execute(
        text("SELECT system_prompt FROM bots WHERE bot_id = :b"), {"b": _BOT}
    ).scalar()
    if not sp or "Dr. Medispa xác nhận lịch:" not in sp:
        return  # already de-verbatim or bot absent — idempotent no-op
    new_sp = _OLD_RE.sub(_NEW, sp, count=1)
    if new_sp == sp:
        return
    bind.execute(
        text("UPDATE bots SET system_prompt = :sp, updated_at = NOW() WHERE bot_id = :b"),
        {"sp": new_sp, "b": _BOT},
    )


def downgrade() -> None:
    bind = op.get_bind()
    sp = bind.execute(
        text("SELECT system_prompt FROM bots WHERE bot_id = :b"), {"b": _BOT}
    ).scalar()
    if not sp or _NEW not in sp:
        return
    new_sp = sp.replace(_NEW, _ORIG_TEMPLATE, 1)
    bind.execute(
        text("UPDATE bots SET system_prompt = :sp, updated_at = NOW() WHERE bot_id = :b"),
        {"sp": new_sp, "b": _BOT},
    )
