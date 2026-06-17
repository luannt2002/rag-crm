"""Owner sysprompt: extraction + numeric-copy discipline (drop-fact + override fix).

Revision: 0173
Prev:     0172

RAGAS forensic (45 câu) showed the dominant multi-step failure is GENERATION,
not retrieval: 13 required facts were dropped by the model despite being in the
retrieved chunk (drop-fact), and several numeric values were overridden from
parametric memory (luat: 4.000.000 → 3.000.000; vat-ly: re-derived ignoring the
internal resistance r). Both are generation-discipline problems.

Fix at the CORRECT layer (CLAUDE.md sacred-rule 10): the discipline rule lives
in the BOT OWNER's system_prompt (single source of truth), NOT injected by the
platform SysPromptAssembler (that would be app-inject + a tenant leak, the same
class as the rule-18 spa-vocab removed in 0172). The rule is domain-neutral
(no service/brand/industry literal), behavioural (no verbatim example sentence —
avoids the output system_leak shingle trap), and applies to every demo bot.

Two clauses:
  1. Extraction discipline — multi-part / list / compare / synthesise questions
     must include EVERY relevant number, date, proper noun, document number from
     the documents; do not summarise away required facts; answer every part.
  2. Numeric copy — numbers / prices / dates / document numbers must be copied
     verbatim from the documents; if the documents already contain a worked
     result, use it (do not re-derive); never substitute a value from memory.

Idempotent (append-if-absent via marker). Reversible. Rule 7 (alembic, not psql).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0173"
down_revision: str | None = "0172"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_MARKER = "KỶ LUẬT TRÍCH XUẤT"

_BLOCK = """

═══════════════════════════════════════════════════════════
KỶ LUẬT TRÍCH XUẤT & SỐ LIỆU (bắt buộc):
═══════════════════════════════════════════════════════════
- Với câu hỏi nhiều phần, hoặc yêu cầu liệt kê / so sánh / tổng hợp: PHẢI đưa vào câu trả lời ĐẦY ĐỦ mọi con số, ngày tháng, tên riêng, số hiệu văn bản trong tài liệu có liên quan đến câu hỏi. KHÔNG tóm tắt làm rụng dữ kiện bắt buộc. Trả lời ĐỦ từng phần của câu hỏi.
- Mọi con số, giá, ngày, số hiệu văn bản PHẢI sao chép CHÍNH XÁC từ tài liệu. Nếu tài liệu đã có sẵn lời giải / kết quả tính, DÙNG kết quả đó, KHÔNG tự tính lại. TUYỆT ĐỐI KHÔNG thay bằng số từ trí nhớ; nếu tài liệu không có số đó, nói rõ tài liệu không nêu."""

_BOTS = (
    "test-spa-id", "thong-tu-09-2020-tt-nhnn", "luat-giao-thong", "y-te-co-ban",
    "vat-ly-11", "hoa-hoc-10", "toan-hoc-12", "kinh-te-vi-mo", "tin-hoc-co-ban",
    "sinh-hoc-12", "dia-ly-vn", "lich-su-vn",
)


def upgrade() -> None:
    op.execute(
        text("""
            UPDATE bots SET system_prompt = system_prompt || :block, updated_at = NOW()
            WHERE bot_id = ANY(:bots) AND system_prompt NOT LIKE :marker
        """).bindparams(block=_BLOCK, bots=list(_BOTS), marker=f"%{_MARKER}%")
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE bots SET system_prompt = REPLACE(system_prompt, :block, ''), updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(block=_BLOCK, bots=list(_BOTS))
    )
