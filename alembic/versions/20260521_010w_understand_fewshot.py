"""[T1-Smartness] Add few-shot examples to understand_query intent classifier.

Revision ID: 010w
Revises: 010v
Create Date: 2026-05-21

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 2 of 5.

Bug evidence (verified 2026-05-21 session, bot test-spa-id 11-turn UI test):
The prompt ``language_packs (vi, understand)`` lists 7 intent values with
ONE-line definitions only, no concrete examples. The single example at
the bottom (``{"query": "...", "intent": "factoid"}``) anchors the LLM
output toward ``factoid``. Result: across 11 UI test turns, 0 turns
classified as ``aggregation`` despite 3 turns being canonical aggregation
queries:

  - "1tr499 có mấy dịch vụ?"         → classified factoid (should be aggregation)
  - "1499000 có mấy dịch vụ?"        → classified factoid (should be aggregation)
  - "có bao nhiêu gói dưới 2tr?"     → classified factoid (should be aggregation)

Downstream chain when intent is misclassified:
  * multi_query template = factoid_prompt (paraphrase) instead of
    aggregation_prompt (HyDE answer-template) → query variants don't
    include raw values that match CSV row chunks
  * CRAG fallback threshold 0.25 (factoid) instead of 0.20 (aggregation)
  * No per-intent rerank top_k boost (planned Phase 3) → only top-10
    chunks reach the LLM, missing 3 of 4 ground-truth rows

Fix — append a ``3. VÍ DỤ PHÂN LOẠI`` few-shot block with 3-6 pattern
examples per intent. Examples use placeholder X/Y/Z so the prompt stays
domain-neutral: every tenant and every bot (Vietnamese + English) gets
the same lift.

Idempotent: UPDATE with ``WHERE code=? AND prompt_key='understand'``.
Down restores the exact pre-010w content (backed up in
``_VI_OLD_CONTENT`` / ``_EN_OLD_CONTENT`` constants).

Operator step after ``alembic upgrade 010w``:
    redis-cli --scan --pattern 'langpack:*' | xargs -r redis-cli DEL
    systemctl restart ragbot ragbot-worker
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010w"
down_revision: str | None = "010v"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Backup of pre-010w content (verified 2026-05-21 via psql query).
# Used by downgrade() to restore exact prior state.
_VI_OLD_CONTENT = """Bạn nhận câu hỏi và lịch sử hội thoại. Thực hiện 2 việc:

1. VIẾT LẠI câu hỏi thành câu độc lập (gộp ngữ cảnh từ lịch sử nếu cần).
   Nếu câu hỏi đã rõ ràng, giữ nguyên.

2. PHÂN LOẠI intent (CHỌN MỘT từ danh sách):
   - factoid: hỏi 1 thông tin cụ thể (giá, thời gian, tên, có/không)
   - comparison: so sánh 2+ mục, hỏi khác nhau, A vs B
   - multi_hop: câu hỏi nhiều bước, cần tổng hợp từ nhiều nguồn
   - aggregation: liệt kê, tổng hợp, đếm, rẻ nhất, đắt nhất
   - greeting: lời chào, xin chào, hello, hi, alo
   - feedback: ý kiến, đánh giá, cảm ơn, phàn nàn
   - out_of_scope: nằm ngoài phạm vi (đặt lịch, thời tiết, chuyện cười, off-topic)

Trả về JSON:
{"query": "câu hỏi đã viết lại", "intent": "factoid"}"""


_VI_NEW_CONTENT = """Bạn nhận câu hỏi và lịch sử hội thoại. Thực hiện 2 việc:

1. VIẾT LẠI câu hỏi thành câu độc lập (gộp ngữ cảnh từ lịch sử nếu cần).
   Nếu câu hỏi đã rõ ràng, giữ nguyên.

2. PHÂN LOẠI intent (CHỌN MỘT từ danh sách):
   - factoid: hỏi 1 thông tin cụ thể (giá, thời gian, tên, có/không)
   - comparison: so sánh 2+ mục, hỏi khác nhau, A vs B
   - multi_hop: câu hỏi nhiều bước, cần tổng hợp từ nhiều nguồn
   - aggregation: liệt kê, tổng hợp, đếm, gom matching, rẻ nhất, đắt nhất
   - greeting: lời chào, xin chào, hello, hi, alo
   - feedback: ý kiến, đánh giá, cảm ơn, phàn nàn
   - out_of_scope: nằm ngoài phạm vi (đặt lịch, thời tiết, chuyện cười, off-topic)

3. VÍ DỤ PHÂN LOẠI (pattern — không phải nội dung domain cụ thể):

   FACTOID (1 thông tin cụ thể):
   - "X là gì?" / "X là sản phẩm/dịch vụ nào?"  → factoid
   - "Giá X bao nhiêu?" / "X giá bao nhiêu?"    → factoid
   - "Có X không?" / "X còn không?"             → factoid
   - "X bao gồm gì?" (mô tả 1 entity)           → factoid

   AGGREGATION (đếm / liệt kê / gom matching):
   - "Có mấy X?" / "Có bao nhiêu X?"            → aggregation
   - "Liệt kê tất cả X" / "Tất cả X có gì?"     → aggregation
   - "Y có mấy X?" (đếm X có thuộc tính Y)       → aggregation
   - "Giá Z có những X nào?" (gom matching)      → aggregation
   - "X nào rẻ nhất / đắt nhất / cao nhất?"      → aggregation
   - "X dưới giá N có gì?" (filter + list)       → aggregation

   COMPARISON: "X vs Y khác gì?", "X tốt hơn Y không?"
   MULTI_HOP: "X gồm Y nào và mỗi Y có giá bao nhiêu?"

Trả về JSON ĐÚNG cấu trúc:
{"query": "câu hỏi đã viết lại", "intent": "<một trong các giá trị trên>"}"""


_EN_OLD_CONTENT = """You receive a question and conversation history. Do 2 things:

1. REWRITE the question as standalone (merge context from history if needed).
   If already clear, keep verbatim.

2. CLASSIFY intent (PICK ONE from list):
   - factoid: a single concrete fact (price, time, name, yes/no)
   - comparison: compare 2+ items, A vs B, differences
   - multi_hop: multi-step reasoning, synthesis across sources
   - aggregation: list / total / count / cheapest / most expensive
   - greeting: hello, hi, hey, greetings
   - feedback: opinion, review, thanks, complaint
   - out_of_scope: outside coverage (booking time, weather, jokes, off-topic)

Return JSON:
{"query": "rewritten question", "intent": "factoid"}"""


_EN_NEW_CONTENT = """You receive a question and conversation history. Do 2 things:

1. REWRITE the question as standalone (merge context from history if needed).
   If already clear, keep verbatim.

2. CLASSIFY intent (PICK ONE from list):
   - factoid: a single concrete fact (price, time, name, yes/no)
   - comparison: compare 2+ items, A vs B, differences
   - multi_hop: multi-step reasoning, synthesis across sources
   - aggregation: list / count / total / gather matching / cheapest / most expensive
   - greeting: hello, hi, hey, greetings
   - feedback: opinion, review, thanks, complaint
   - out_of_scope: outside coverage (booking time, weather, jokes, off-topic)

3. EXAMPLES (pattern — NOT specific domain content):

   FACTOID:
   - "What is X?" / "Which item is X?"   → factoid
   - "How much is X?" / "Price of X?"    → factoid
   - "Is there X?" / "Do you have X?"    → factoid
   - "What does X include?" (1 entity)   → factoid

   AGGREGATION:
   - "How many X are there?"                  → aggregation
   - "List all X" / "What X do you have?"     → aggregation
   - "How many X cost Y?" (count matching)    → aggregation
   - "Which X are under price Y?"             → aggregation
   - "Which X is cheapest / most expensive?"  → aggregation

   COMPARISON: "How is X different from Y?"
   MULTI_HOP: "List X and the price of each Y"

Return JSON in this exact shape:
{"query": "rewritten question", "intent": "<one of the values above>"}"""


def upgrade() -> None:
    """Append few-shot example block to understand prompt (vi + en)."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = :content,
                version = version + 1,
                updated_at = NOW()
            WHERE code = 'vi' AND prompt_key = 'understand'
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
            WHERE code = 'en' AND prompt_key = 'understand'
            """
        ).bindparams(content=_EN_NEW_CONTENT),
    )


def downgrade() -> None:
    """Restore pre-010w content for both locales."""
    op.execute(
        text(
            """
            UPDATE language_packs
            SET content = :content,
                version = version + 1,
                updated_at = NOW()
            WHERE code = 'vi' AND prompt_key = 'understand'
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
            WHERE code = 'en' AND prompt_key = 'understand'
            """
        ).bindparams(content=_EN_OLD_CONTENT),
    )
