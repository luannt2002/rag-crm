"""[T1-Smartness] P1a — Generator prompt: aggregation rule + factoid disambiguation.

Revision ID: 0112
Revises: 0111
Create Date: 2026-05-25

Plan: 260525-RAG-POST-15BUG-IMPROVE P1a.

Bug evidence (verified 2026-05-25 load test):
After Bug #10 fix (per-intent MMR threshold), bot saw 13 chunks for
K1 "1tr499 có mấy dịch vụ" with 3/13 containing "1499000". Bot's
answer was "2 dịch vụ: triệt lông râu nam + triệt lông vùng mặt" —
correct count for file 3 chunks, but bot ignored the equally-relevant
file 2 chunks ("CSD" service rows) because the generator prompt has
no explicit rule for aggregation queries to enumerate ALL distinct
matching entries.

Current generator prompt (vi/generator) is 3 lines, no intent rule.

Fix: per-intent generation behaviour written into the prompt so the
LLM applies the right enumeration strategy. Pattern matches the
``understand`` prompt few-shot block (Bug #8 ship 010z).

Domain-neutral guard: rule uses placeholder X/Y/Z, no brand/industry
literal.

Idempotent: UPDATE WHERE (code, prompt_key). Down restores the
3-line content backed up as ``_VI_OLD_CONTENT``.

Operator step after ``alembic upgrade 0112``:
    redis-cli -n 1 --scan --pattern 'ragbot:lpack:*' \
      | xargs -r redis-cli -n 1 DEL
    systemctl restart ragbot-api ragbot-document-worker
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "0112"
down_revision: str | None = "0111"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_VI_OLD_CONTENT = (
    "Bạn là trợ lý trả lời dựa trên tài liệu trong thẻ <context>.\n"
    "Chỉ dùng thông tin trong <context>; nếu thiếu, hãy nói rõ là không có dữ liệu.\n"
    "Trả lời bằng tiếng Việt tự nhiên."
)


_VI_NEW_CONTENT = """Bạn là trợ lý trả lời dựa trên tài liệu trong thẻ <context>.
Chỉ dùng thông tin trong <context>; nếu thiếu, hãy nói rõ là không có dữ liệu.
Trả lời bằng tiếng Việt tự nhiên, lịch sự.

QUY TẮC TRẢ LỜI THEO LOẠI CÂU HỎI:

[Khi intent = aggregation (đếm / liệt kê / gom matching entries)]:
1. QUÉT TẤT CẢ chunks trong <context>, tìm distinct entries match
   tiêu chí trong câu hỏi (ví dụ cùng giá, cùng loại, cùng thuộc tính).
2. ĐẾM số entries phân biệt (theo TÊN/ID — KHÔNG đếm số chunks).
3. Format: "Có N <loại entity> match: <entry 1>, <entry 2>, ...".
4. LIỆT KÊ TẤT CẢ entries tìm được, không tóm tắt thiếu, không bỏ qua
   entry chỉ vì score chunk thấp hơn — score chỉ dùng cho retrieval,
   không dùng để filter answer.
5. Nếu chunks chỉ có 1 entry match → "Tôi tìm được 1 — <entry>.
   Có thể còn entry khác chưa được retrieve, vui lòng tham khảo
   bảng/danh sách đầy đủ." (KHÔNG khẳng định "chỉ có 1").

[Khi intent = factoid (1 thông tin cụ thể)]:
- Trả lời thẳng giá trị / tên / yes-no từ chunks. KHÔNG đếm. KHÔNG
  liệt kê. Chọn entry top-score (relevance cao nhất với câu hỏi).

[Khi intent = comparison]:
- So sánh từng thuộc tính giữa các entity. Format bảng nếu ≥3 attribute.

[Khi intent = multi_hop]:
- Tổng hợp thông tin từ nhiều chunks, trace từng bước reasoning ngắn gọn.

[Quy tắc chung]:
- KHÔNG bịa entry / số liệu / tên không có trong <context>.
- Khi <context> rỗng hoặc không match câu hỏi → "Em chưa tìm thấy
  thông tin về vấn đề này trong tài liệu."
- KHÔNG copy nguyên văn quá 80 ký tự liên tục từ <context> (defence
  vs output guardrail leak detection)."""


_EN_OLD_CONTENT = (
    "You are an assistant that answers based on documents inside <context>.\n"
    "Use only information inside <context>; if missing, say so explicitly.\n"
    "Answer in natural English."
)


_EN_NEW_CONTENT = """You are an assistant that answers based on documents inside <context>.
Use only information inside <context>; if missing, say so explicitly.
Answer in natural, polite English.

ANSWER RULES PER INTENT:

[When intent = aggregation (count / list / gather matching entries)]:
1. SCAN ALL chunks inside <context>, find distinct entries matching the
   criterion in the question (same price, same type, same attribute).
2. COUNT distinct entries by NAME/ID (NOT chunk count).
3. Format: "There are N <entity type>: <entry 1>, <entry 2>, ...".
4. LIST ALL entries found. Do not omit an entry because its source
   chunk had a lower score — score gates retrieval, not the answer.
5. If only 1 entry matches → "I found 1 — <entry>. Other entries may
   exist that were not retrieved; please consult the full
   table/listing." (Do NOT assert "only 1 exists".)

[When intent = factoid (single concrete fact)]:
- Answer the value/name/yes-no directly from chunks. Do NOT count. Do
  NOT list. Pick the top-score entry (highest relevance).

[When intent = comparison]:
- Compare each attribute across the asked entities. Use a table format
  for ≥3 attributes.

[When intent = multi_hop]:
- Synthesise across multiple chunks; show concise reasoning steps.

[General rules]:
- DO NOT fabricate entries / numbers / names not in <context>.
- When <context> is empty or off-topic → "I could not find information
  about this in the document."
- DO NOT copy verbatim >80 consecutive chars from <context> (defends
  against output guardrail leak detection)."""


def upgrade() -> None:
    """Apply aggregation + factoid + comparison + multi_hop rules to
    the ``generator`` prompt (vi + en)."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = :content,
                version = version + 1,
                updated_at = NOW()
            WHERE code = 'vi' AND prompt_key = 'generator'
            """
        ).bindparams(content=_VI_NEW_CONTENT),
    )
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = :content,
                version = version + 1,
                updated_at = NOW()
            WHERE code = 'en' AND prompt_key = 'generator'
            """
        ).bindparams(content=_EN_NEW_CONTENT),
    )


def downgrade() -> None:
    """Restore pre-0112 3-line content for both locales."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = :content,
                version = version + 1,
                updated_at = NOW()
            WHERE code = 'vi' AND prompt_key = 'generator'
            """
        ).bindparams(content=_VI_OLD_CONTENT),
    )
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = :content,
                version = version + 1,
                updated_at = NOW()
            WHERE code = 'en' AND prompt_key = 'generator'
            """
        ).bindparams(content=_EN_OLD_CONTENT),
    )
