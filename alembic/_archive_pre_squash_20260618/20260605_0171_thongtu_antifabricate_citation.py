"""thong-tu bot: pointed anti-fabricate rule for document/article citation tokens.

Revision: 0171
Prev:     0170

Multi-step test surfaced a HALLU-fabricate breach (sacred): asked "Thông tư
09/2020 thay thế văn bản nào?", the bot answered "thay thế Thông tư 03/2014/
TT-NHNN" — a circular number that appears in ZERO corpus chunks (verified by
DB scan). The correct value "18/2018/TT-NHNN" IS in the corpus (Điều 56,
chunk 776ad65f).

Evidence (3 runs, bypass_cache, all chunks=1):
  run1 → fabricated "03/2014" (breach)   run2/run3 → refused (correct)
The bare "thay thế" query retrieves a wrong chunk (the word "thay thế" also
appears in disaster-recovery/failover clauses), so the replacement clause is
often not in context. The existing generic rule 1 ("không bịa... không thêm
kiến thức ngoài") did NOT stop gpt-4.1-mini from emitting a citation token
from its parametric prior — the borderline 1/3 fabrication rate shows the
generic rule is necessary but not sufficient for citation/number tokens.

Fix (owner sysprompt config, sacred-rule-10 compliant — behavioural rule, no
verbatim example to avoid system_leak shingle): a POINTED rule that any văn
bản number / Điều / Khoản / ngày MUST be copied from <documents>; if the
retrieved Nguồn does not state it, say so explicitly — never supply a number
from memory. This is the legal-citation analog of the spa bot's successful
anti-invent-name rule. Forces refuse-or-correct instead of fabricate when the
replacement clause is not retrieved.

NOTE: the underlying retrieval-coverage gap (bare "thay thế" query missing the
Điều 56 chunk) is a separate follow-up; this migration closes the SACRED HALLU
breach (fabrication) which must hold regardless of retrieval.

Idempotent (append-if-absent). Reversible. Rule 7 (alembic, not psql).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0171"
down_revision: str | None = "0170"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOT = "thong-tu-09-2020-tt-nhnn"
_MARKER = "ANTI-FABRICATE SỐ HIỆU"

_BLOCK = """

═══════════════════════════════════════════════════════════
QUY TẮC ANTI-FABRICATE SỐ HIỆU (sacred — HALLU=0):
═══════════════════════════════════════════════════════════

13. ⭐ Mọi SỐ HIỆU văn bản (Thông tư, Nghị định, Quyết định, Luật), số Điều / Khoản / Điểm, và ngày tháng PHẢI được sao chép CHÍNH XÁC từ <documents>. TUYỆT ĐỐI KHÔNG dùng số hiệu hoặc ngày từ trí nhớ / kiến thức ngoài tài liệu.
   - Nếu Nguồn được cung cấp KHÔNG nêu số hiệu cụ thể cho phần được hỏi → nói rõ tài liệu không nêu số hiệu đó, KHÔNG được điền một số hiệu tự nhớ.
   - Thà trả lời thiếu (refuse phần đó) còn hơn điền sai một số hiệu văn bản — sai số hiệu trong ngữ cảnh pháp lý là lỗi nghiêm trọng."""


def upgrade() -> None:
    op.execute(
        text("""
            UPDATE bots SET system_prompt = system_prompt || :block, updated_at = NOW()
            WHERE bot_id = :bot AND system_prompt NOT LIKE :marker
        """).bindparams(block=_BLOCK, bot=_BOT, marker=f"%{_MARKER}%")
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE bots SET system_prompt = REPLACE(system_prompt, :block, ''), updated_at = NOW()
            WHERE bot_id = :bot
        """).bindparams(block=_BLOCK, bot=_BOT)
    )
