"""Owner sysprompt: anti-over-refuse + fact-extract discipline (RC#4).

Revision: 0178
Prev:     0177

Hard-test forensic + chunk-level trace (isolated/sequential, ruling out the
parallel-load artifact) showed two GENERATION failures that 0173 did NOT cover:
  (a) OVER-REFUSE — the model answers "Tài liệu không đề cập" even when a
      retrieved chunk holds the relevant fact (lich-su: 3 questions refused
      while the fact was in corpus + retrievable; sequential re-run still
      refused, so it is generation-framing, not load).
  (b) DROP proper nouns — multi-part answers drop low-salience named entities
      that ARE in the chunk (lich-su Yên Bái dropped "Nguyễn Thái Học",
      "De Castries").

Fix at the CORRECT layer (CLAUDE.md sacred-rule 10): the discipline lives in the
BOT OWNER's system_prompt (single source of truth), paper-backed by FaithfulRAG
(ACL'25 — fact-extract pre-step forces every fact to be registered before
summary compression can elide it) and the small-LM-utilization finding (the
"no-answer" refuse is a prompt-framing default, fixable by an explicit
re-check-before-refuse instruction).

CRITICAL role-guard: the block is BEHAVIOURAL ONLY — no verbatim example
sentence, no brand/number literal. A few-shot verbatim example (as some sources
suggest) would re-introduce exactly the system_prompt_leak shingle landmine that
0176/0177 just removed. Marker-gated append (idempotent), reversible. Rule 7.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0178"
down_revision: str | None = "0177"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_MARKER = "KỶ LUẬT TRẢ LỜI & CHỐNG TỪ CHỐI OAN"

_BLOCK = """

═══════════════════════════════════════════════════════════
KỶ LUẬT TRẢ LỜI & CHỐNG TỪ CHỐI OAN (bắt buộc):
═══════════════════════════════════════════════════════════
- TRƯỚC KHI TRẢ LỜI: rà soát toàn bộ tài liệu trong <documents>, ghi nhận (trong đầu, không in ra) mọi dữ kiện liên quan đến TỪNG PHẦN của câu hỏi — tên riêng, số liệu, ngày tháng, mốc sự kiện, điều kiện áp dụng. Sau đó trả lời từng phần dựa trên dữ kiện đã rà; KHÔNG rút gọn hay lược bỏ tên riêng, KHÔNG tóm tắt làm rụng dữ kiện bắt buộc.
- CHỐNG TỪ CHỐI OAN: trước khi nói "tài liệu không đề cập" (hoặc tương đương), BẮT BUỘC kiểm tra lại MỘT lần — trong <documents> có dữ kiện liên quan dù chỉ một phần hoặc gián tiếp không? Nếu CÓ → trả lời phần có dữ kiện và nói rõ phần nào tài liệu chưa nêu; CHỈ từ chối khi <documents> HOÀN TOÀN không có dữ kiện liên quan. (Lưu ý: việc kiểm tra này KHÔNG cho phép bịa — chỉ dùng dữ kiện thật trong tài liệu.)
- Câu hỏi nhiều phần: trả lời ĐỦ mọi phần; nếu một phần thiếu dữ kiện, nêu rõ phần đó còn thiếu, KHÔNG im lặng bỏ qua khiến rụng các phần khác."""

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
            UPDATE bots SET system_prompt = replace(system_prompt, :block, ''), updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(block=_BLOCK, bots=list(_BOTS))
    )
