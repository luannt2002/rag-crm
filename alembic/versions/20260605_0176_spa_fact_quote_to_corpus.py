"""Owner sysprompt: de-literalize FACT_QUOTE rule (facts → corpus grounding).

Revision: 0176
Prev:     0175

Booking-confirm forensic (instrumented system_prompt_leak, evidence
2026-06-05) captured the EXACT leaking 24-word shingle on a blocked booking
confirmation:

    "102 Vũ Trọng Phụng, Thanh Xuân, Hà Nội (đi thang bộ lên tầng 2)"

The confirmation itself was flawless (echoed the 4 captured slots + address +
hotline). It was blocked because rule 22 (FACT_QUOTE_LITERAL) instructs the bot
to answer contact questions by quoting a VERBATIM address/hotline/hours literal
that lives in the system_prompt. When the model reproduces that literal — in a
contact answer OR a booking confirmation — it collides with the 24-word
system_prompt shingle and the output guardrail `system_prompt_leak` blocks it
(answer_reason='Output guardrail blocked'). Multi-agent audit confirmed the leak
guard has no action-bot exemption and cannot tell owner-fact-relay from
instruction-leak (local_guardrail.py system_prompt_leak).

Fix at the CORRECT layer (CLAUDE.md Application MINDSET + sacred-rule 10):
contact facts are DATA — they belong in the corpus, not as verbatim literals in
the behaviour prompt. Verified the corpus already contains them (chunks
"Thông tin địa chỉ và liên hệ của Dr. Medispa", "Nhắc lịch hẹn và cung cấp địa
chỉ"). So rule 22 is rewritten BEHAVIOURALLY: answer contact questions FROM
<documents>, compose in the model's own words, no fixed verbatim string. The
tenant address/hotline/maps literals leave the prompt entirely (they are
tenant data — also a domain-neutral concern). This eliminates the leak trigger
without weakening the guardrail and matches feedback_sysprompt_verbatim_example.

Idempotent (regex matches the old literal block only; absent → no-op).
Reversible (downgrade restores the original literal block). Rule 7 (alembic).
"""
from __future__ import annotations

import re

from alembic import op
from sqlalchemy import text

revision: str = "0176"
down_revision: str | None = "0175"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOT = "test-spa-id"

# Match rule 22 (FACT_QUOTE_LITERAL) through to just before rule 23.
_OLD_RE = re.compile(
    r"22\. ⭐ FACT_QUOTE_LITERAL.*?(?=23\. ⭐ EXACT_SERVICE_NAME_MATCH)",
    re.DOTALL,
)

# Behavioural replacement — no verbatim literal, contact answered from corpus.
_NEW = (
    "22. ⭐ FACT_QUOTE — Khi user hỏi info cơ bản về spa (hotline / địa chỉ / "
    "giờ mở cửa / bản đồ):\n"
    "   - Trả lời TỪ <documents> (corpus có chunk thông tin liên hệ + địa chỉ "
    "spa). Diễn đạt tự nhiên bằng lời của em, KHÔNG copy nguyên văn một câu mẫu "
    "cố định, KHÔNG đọc lại y hệt một chuỗi dài giống hệt tài liệu.\n"
    "   - KHÔNG refuse câu hỏi info liên hệ cơ bản nếu <documents> có dữ liệu; "
    "nếu thật sự không có, mời khách liên hệ qua kênh chính thức của spa.\n"
    "   - Khi chốt lịch hẹn, nhắc địa chỉ + hotline lấy TỪ <documents> và diễn "
    "đạt lại bằng lời của em.\n\n"
)

# Reverse direction restores the original literal block.
_ORIG = (
    '22. ⭐ FACT_QUOTE_LITERAL — Khi user hỏi info cơ bản về Dr. Medispa:\n'
    '   - Hotline / Số điện thoại / SĐT → trả lời LITERAL: "0926.559.268"\n'
    '   - Địa chỉ / địa điểm / "ở đâu" → trả lời LITERAL: "Số 102 Vũ Trọng Phụng, '
    'Thanh Xuân, Hà Nội (đi thang bộ lên tầng 2)"\n'
    '   - Giờ mở cửa / mở cửa lúc nào → trả lời LITERAL: "9-21h, từ Thứ Hai đến '
    'Chủ Nhật"\n'
    '   - Maps / link bản đồ → "https://maps.app.goo.gl/Vo5sw3iHtZZWbVN9A"\n\n'
    '   QUY TẮC:\n'
    '   - KHÔNG refuse các câu hỏi info cơ bản trên dù <documents> không có chunk '
    'match.\n'
    '   - KHÔNG paraphrase, KHÔNG modify giá trị literal.\n'
    '   - Cite ngắn cuối câu: "(theo thông tin Dr. Medispa)".\n\n'
    '   VD ĐÚNG:\n'
    '   - User: "hotline số nào" → "Hotline Dr. Medispa là 0926.559.268 ạ (theo '
    'thông tin Dr. Medispa)."\n'
    '   - User: "địa chỉ ở đâu" → "Dr. Medispa ở số 102 Vũ Trọng Phụng, Thanh '
    'Xuân, Hà Nội ạ."\n\n'
)


def upgrade() -> None:
    bind = op.get_bind()
    sp = bind.execute(
        text("SELECT system_prompt FROM bots WHERE bot_id = :b"), {"b": _BOT}
    ).scalar()
    if not sp or "FACT_QUOTE_LITERAL" not in sp:
        return  # already de-literalized or bot absent — idempotent no-op
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
    if not sp or "22. ⭐ FACT_QUOTE —" not in sp:
        return
    new_sp = re.sub(
        r"22\. ⭐ FACT_QUOTE —.*?(?=23\. ⭐ EXACT_SERVICE_NAME_MATCH)",
        _ORIG,
        sp,
        count=1,
        flags=re.DOTALL,
    )
    bind.execute(
        text("UPDATE bots SET system_prompt = :sp, updated_at = NOW() WHERE bot_id = :b"),
        {"sp": new_sp, "b": _BOT},
    )
