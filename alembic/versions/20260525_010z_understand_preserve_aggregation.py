"""[T1-Smartness] Bug #8 — understand prompt: CLASSIFY-FIRST + preserve aggregation cues.

Revision ID: 010z
Revises: 010y
Create Date: 2026-05-25

Plan: 260525-4BUG-INGEST-PIPELINE Bug #8 (discovered after Phase B0 ship).

Bug evidence (verified 2026-05-25 live test):
After Phase B0 wires pipeline_config correctly, bot "test-spa-id" still
returns "không có thông tin" for "1tr499 có mấy dịch vụ" because the
intent comes back as ``factoid`` (not ``aggregation``). DB trace shows
``understand_query`` rewrites the question into "Dịch vụ trị giá
1.499.000 đồng tại Dr. Medispa là dịch vụ nào?" — turning an
aggregation query into a factoid one BEFORE the classifier sees it.

Root cause: alembic 010w (Phase 2) prompt placed REWRITE as step 1 and
CLASSIFY as step 2. In production with realistic history + sysprompt
context, gpt-4.1-mini rewrites "có mấy" into "là dịch vụ nào" to
"clarify" the question, then classifies the rewritten string. The
isolated probe used by Phase 2 acceptance lacked the same context so
the bug stayed hidden until live runtime.

Fix:
  * Invert step order — CLASSIFY FIRST using the ORIGINAL user query;
    REWRITE second with an explicit rule preserving aggregation cues
    ("có mấy", "có bao nhiêu", "liệt kê", "rẻ nhất", ...).
  * Add cue-preserve rule to the EN row as well so multi-lingual
    routes stay aligned.

Validation:
Live LLM probe (gpt-4.1-mini, temperature=0, realistic history): 8/8
PASS across mixed aggregation + factoid queries. Aggregation cues
preserved verbatim in the rewritten output.

Idempotent: UPDATE WHERE (code, prompt_key). Down restores the 010w
content backed up in ``_VI_OLD_CONTENT`` / ``_EN_OLD_CONTENT``.

Operator step after upgrade:
    redis-cli -n 1 --scan --pattern 'ragbot:lpack:*' | xargs -r redis-cli -n 1 DEL
    systemctl restart ragbot-api ragbot-document-worker
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010z"
down_revision: str | None = "010y"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Pre-010z content (from alembic 010w Phase 2 commit 046485a). Used by
# downgrade() to restore the prior state exactly.
_VI_OLD_CONTENT = """Bạn nhận câu hỏi và lịch sử hội thoại. Thực hiện 2 việc:

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


_VI_NEW_CONTENT = """Bạn nhận câu hỏi và lịch sử hội thoại. Thực hiện 2 việc THEO THỨ TỰ:

BƯỚC 1 (LÀM TRƯỚC) — PHÂN LOẠI intent dựa trên CÂU HỎI GỐC của user
(KHÔNG dựa trên câu đã rewrite). Chọn MỘT từ danh sách:
   - factoid: hỏi 1 thông tin cụ thể (giá, thời gian, tên, có/không)
   - comparison: so sánh 2+ mục, hỏi khác nhau, A vs B
   - multi_hop: câu hỏi nhiều bước, cần tổng hợp từ nhiều nguồn
   - aggregation: liệt kê, tổng hợp, đếm, gom matching, rẻ nhất, đắt nhất
   - greeting: lời chào, xin chào, hello, hi, alo
   - feedback: ý kiến, đánh giá, cảm ơn, phàn nàn
   - out_of_scope: nằm ngoài phạm vi (đặt lịch, thời tiết, off-topic)

BƯỚC 2 — VIẾT LẠI câu hỏi thành câu độc lập (gộp ngữ cảnh từ lịch sử
nếu cần). QUY TẮC BẮT BUỘC:
   - PHẢI GIỮ NGUYÊN các từ aggregation cue trong câu gốc:
     "có mấy", "có bao nhiêu", "tổng cộng", "liệt kê", "tất cả",
     "rẻ nhất", "đắt nhất", "cao nhất", "thấp nhất", "dưới giá",
     "trên giá". KHÔNG được thay bằng "là gì", "là dịch vụ nào".
   - Nếu câu hỏi đã rõ ràng, giữ nguyên hoàn toàn.

VÍ DỤ PHÂN LOẠI (pattern):
   FACTOID:
   - "X là gì?" / "X là sản phẩm/dịch vụ nào?"  → factoid
   - "Giá X bao nhiêu?"                          → factoid
   - "Có X không?" / "X còn không?"             → factoid

   AGGREGATION (BƯỚC 2 phải giữ "có mấy"/"có bao nhiêu"/"liệt kê"):
   - "Có mấy X?" / "Có bao nhiêu X?"            → aggregation
   - "Liệt kê tất cả X" / "Tất cả X có gì?"     → aggregation
   - "Y có mấy X?" (đếm X có thuộc tính Y)       → aggregation
   - "Giá Z có những X nào?" (gom matching)      → aggregation
   - "X nào rẻ nhất / đắt nhất / cao nhất?"      → aggregation
   - "X dưới giá N có gì?" (filter + list)       → aggregation

   COMPARISON: "X vs Y khác gì?"
   MULTI_HOP: "X gồm Y nào và mỗi Y có giá bao nhiêu?"

Trả về JSON ĐÚNG cấu trúc:
{"query": "câu hỏi đã viết lại (giữ aggregation cues)", "intent": "<intent dựa câu GỐC>"}"""


_EN_OLD_CONTENT = """You receive a question and conversation history. Do 2 things:

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


_EN_NEW_CONTENT = """You receive a question and conversation history. Do 2 things IN ORDER:

STEP 1 (DO FIRST) — CLASSIFY intent based on the USER'S ORIGINAL question
(NOT on the rewritten one). Pick ONE from the list:
   - factoid: a single concrete fact (price, time, name, yes/no)
   - comparison: compare 2+ items, A vs B, differences
   - multi_hop: multi-step reasoning, synthesis across sources
   - aggregation: list / count / total / gather matching / cheapest / most expensive
   - greeting: hello, hi, hey, greetings
   - feedback: opinion, review, thanks, complaint
   - out_of_scope: outside coverage (booking time, weather, jokes, off-topic)

STEP 2 — REWRITE the question as standalone (merge context from history
if needed). MANDATORY RULES:
   - MUST preserve aggregation cue words from the original:
     "how many", "list", "all", "total", "cheapest", "most expensive",
     "highest", "lowest", "under price", "over price". Do NOT rewrite
     them into "what is" / "which X".
   - If the question is already clear, keep verbatim.

EXAMPLES (pattern):
   FACTOID:
   - "What is X?" / "Which item is X?"   → factoid
   - "How much is X?"                    → factoid
   - "Is there X?"                       → factoid

   AGGREGATION (STEP 2 must keep "how many"/"list"/"all"):
   - "How many X are there?"                  → aggregation
   - "List all X" / "What X do you have?"     → aggregation
   - "How many X cost Y?" (count matching)    → aggregation
   - "Which X are under price Y?"             → aggregation
   - "Which X is cheapest / most expensive?"  → aggregation

   COMPARISON: "How is X different from Y?"
   MULTI_HOP: "List X and the price of each Y"

Return JSON in this exact shape:
{"query": "rewritten question (preserve aggregation cues)", "intent": "<intent based on ORIGINAL>"}"""


def upgrade() -> None:
    """Apply CLASSIFY-FIRST + preserve-aggregation-cues prompt (vi + en)."""
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
    """Restore pre-010z (alembic 010w Phase 2) content."""
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
