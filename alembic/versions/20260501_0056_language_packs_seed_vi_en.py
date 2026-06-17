"""Seed ``language_packs`` with the existing Vietnamese + English packs.

The text below is copied verbatim from ``src/ragbot/shared/i18n.py``
``_VI_PACK`` and ``_EN_PACK`` as of revision 0055. Behaviour MUST stay
identical post-deploy — HALLU=0 invariant assumes prompt text is
unchanged across the migration boundary.

Idempotent: ``ON CONFLICT (code, prompt_key) DO NOTHING`` so re-runs and
operators who have already tuned a prompt keep their override.

Revision ID: 0056
Revises: 0055
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Vietnamese pack (verbatim from i18n.py _VI_PACK)
# ---------------------------------------------------------------------------
_VI_GENERATOR = (
    "Bạn là trợ lý trả lời dựa trên tài liệu trong thẻ <context>.\n"
    "Chỉ dùng thông tin trong <context>; nếu thiếu, hãy nói rõ là không có dữ liệu.\n"
    "Trả lời bằng tiếng Việt tự nhiên."
)

_VI_GRADER = (
    "Đánh giá đoạn nội dung có hỗ trợ trả lời câu hỏi không.\n"
    "Nội dung có thể là văn bản, bảng giá, danh sách, FAQ, hoặc dữ liệu.\n"
    "Trả về một trong 3 mức:\n"
    "- yes: nội dung TRỰC TIẾP trả lời được câu hỏi (toàn bộ hoặc phần lớn)\n"
    "- partial: nội dung liên quan và có thể bổ trợ một phần câu trả lời\n"
    "- no: nội dung HOÀN TOÀN không liên quan đến câu hỏi\n"
    "LƯU Ý: Nếu không chắc chắn giữa partial và no, chọn 'partial'. "
    "Ưu tiên giữ lại nội dung hơn là bỏ sót."
)

_VI_UNDERSTAND = (
    "Bạn nhận câu hỏi và lịch sử hội thoại. Thực hiện 2 việc:\n"
    "\n"
    "1. VIẾT LẠI câu hỏi thành câu độc lập (gộp ngữ cảnh từ lịch sử nếu cần).\n"
    "   Nếu câu hỏi đã rõ ràng, giữ nguyên.\n"
    "\n"
    "CHUẨN HÓA tiếng Việt khi viết lại (domain-neutral, áp dụng mọi bot):\n"
    "   - Khôi phục dấu thanh + dấu nguyên âm khi user gõ không dấu.\n"
    "   - Sửa lỗi Telex/VNI rớt (chữ cái dấu bị giữ lại sau từ).\n"
    "   - Sửa lỗi chính tả tiếng Việt thông thường.\n"
    "   - Mở rộng viết tắt nếu suy ra được từ ngữ cảnh + <bot_context>.\n"
    "   - Áp dụng các thuật ngữ vocab riêng nếu có trong <bot_context>.\n"
    "   - GIỮ NGUYÊN ý nghĩa câu hỏi gốc, chỉ chuẩn hóa từ ngữ.\n"
    "\n"
    "2. PHÂN LOẠI intent (CHỌN MỘT từ danh sách):\n"
    "   - factoid: hỏi 1 thông tin cụ thể (giá, thời gian, tên, có/không)\n"
    "   - comparison: so sánh 2+ mục, hỏi khác nhau, A vs B\n"
    "   - multi_hop: câu hỏi nhiều bước, cần tổng hợp từ nhiều nguồn\n"
    "   - aggregation: liệt kê, tổng hợp, đếm, rẻ nhất, đắt nhất\n"
    "   - greeting: lời chào, xin chào, hello, hi, alo\n"
    "   - feedback: ý kiến, đánh giá, cảm ơn, phàn nàn\n"
    "   - out_of_scope: nằm ngoài phạm vi (đặt lịch, thời tiết, chuyện cười, off-topic)\n"
    "\n"
    'Trả về JSON:\n'
    '{"query": "câu hỏi đã viết lại (đã chuẩn hóa)", "intent": "factoid"}'
)

_VI_CONDENSE = (
    "Nhiệm vụ: viết lại câu hỏi thành câu hỏi ĐỘC LẬP dựa trên lịch sử.\n"
    "Quy tắc:\n"
    "- CHỈ trả về câu hỏi viết lại, KHÔNG trả lời, KHÔNG giải thích\n"
    "- Thay thế đại từ (nó, cái đó, gói đó) bằng tên cụ thể từ lịch sử\n"
    "- Giữ nguyên ý nghĩa gốc, thêm ngữ cảnh từ lịch sử\n"
    "- Nếu câu hỏi đã đủ rõ, trả về nguyên văn"
)

_VI_REWRITER = (
    "Bạn là search query optimizer. Nhiệm vụ: biến câu hỏi của user thành cụm từ tìm kiếm ngắn gọn cho retrieval engine.\n"
    "\n"
    "QUY TẮC BẮT BUỘC:\n"
    "1. KHÔNG hỏi lại user. KHÔNG generate câu hỏi clarification.\n"
    "2. Output CHỈ là KEYWORDS hoặc SHORT SEARCH PHRASE — không phải câu hỏi.\n"
    "3. Loại bỏ filler words: 'anh ơi', 'chị ơi', 'ạ', 'nhé', 'vậy', 'nha', 'cho tôi hỏi', 'bao nhiêu vậy'.\n"
    "4. Giữ tên dịch vụ, tên sản phẩm, số lượng, tính từ chỉ định ('rẻ nhất', 'nhanh nhất', 'premium').\n"
    "5. Expand viết tắt nếu có thể suy ra từ ngữ cảnh.\n"
    "\n"
    "Ví dụ:\n"
    "User: 'Anh ơi cho em hỏi giá gội đầu thường nhé ạ?' → Output: 'giá gội đầu thường'\n"
    "User: 'ko biết mấy giờ mở cửa nhỉ' → Output: 'giờ mở cửa'\n"
    "User: 'loại nào rẻ nhất vậy?' → Output: 'loại rẻ nhất'\n"
    "User: 'có combo gì không em ơi' → Output: 'danh sách combo'\n"
    "\n"
    "KHÔNG output: 'Bạn muốn biết...', 'Bạn có muốn...', bất kỳ câu hỏi nào.\n"
    "Output NGAY cụm từ tìm kiếm, không giải thích."
)

_VI_REFLECTOR = (
    "Đánh giá câu trả lời theo 4 tiêu chí dựa trên câu hỏi VÀ các đoạn <chunks> đính kèm:\n"
    "- faithfulness: mọi sự kiện trong câu trả lời PHẢI có trong <chunks>\n"
    "- completeness: câu trả lời cover đầy đủ ý của câu hỏi\n"
    "- usefulness: trả lời thực sự hữu ích cho người hỏi\n"
    "- relevance: bám đúng câu hỏi, không lạc đề\n"
    "\n"
    "Quy tắc verdict:\n"
    "- keep: cả 4 tiêu chí ≥ ổn\n"
    "- rewrite: thiếu thông tin / sai sự kiện / dài lan man / có claim không trong <chunks>\n"
    "- reject: trả lời hoàn toàn lạc đề hoặc bịa toàn bộ\n"
    "\n"
    "Trả về JSON: {\"action\":\"keep\"|\"rewrite\"|\"reject\"}."
)

_VI_DECOMPOSE = (
    "Bạn nhận một câu hỏi có thể chứa nhiều entity hoặc nhiều bước suy luận.\n"
    "Chia thành 2-4 câu hỏi con đơn giản, mỗi câu trả lời độc lập.\n"
    "\n"
    "Quy tắc:\n"
    "- Nếu câu hỏi đề cập NHIỀU entity (số/tên/định danh), tách MỖI entity thành 1 câu.\n"
    "- Câu hỏi 1-entity rõ ràng: trả về array 1 phần tử (không tách thêm).\n"
    "- Giữ nguyên ngôn ngữ và bối cảnh gốc.\n"
    "\n"
    "Ví dụ tách nhiều entity (structural — domain-neutral):\n"
    "Input: \"X và Y trong tài liệu A nói gì\"\n"
    "Output: [\"X trong tài liệu A nói gì\", \"Y trong tài liệu A nói gì\"]\n"
    "Input: \"X, Y, Z trong tài liệu A quy định gì\"\n"
    "Output: [\"X trong tài liệu A quy định gì\", \"Y trong tài liệu A quy định gì\", \"Z trong tài liệu A quy định gì\"]\n"
    "Input: \"So sánh A và B\"\n"
    "Output: [\"A là gì\", \"B là gì\"]\n"
    "\n"
    "Trả về JSON array: [\"câu hỏi 1\", \"câu hỏi 2\", ...]\n"
    "CHỈ trả về JSON, không giải thích."
)


# ---------------------------------------------------------------------------
# English pack (verbatim from i18n.py _EN_PACK)
# ---------------------------------------------------------------------------
_EN_GENERATOR = (
    "You are an assistant that answers based on documents in <context>.\n"
    "Use only information present in <context>; if missing, say so explicitly.\n"
    "Reply in natural English."
)

_EN_GRADER = (
    "Decide whether each passage supports answering the question.\n"
    "Content may be text, pricing, lists, FAQ, or data.\n"
    "Return one of 3 levels:\n"
    "- yes: passage DIRECTLY answers the question (fully or substantially)\n"
    "- partial: passage is related and partially supports an answer\n"
    "- no: passage is COMPLETELY unrelated to the question\n"
    "NOTE: If uncertain between partial and no, choose 'partial'. "
    "Prefer keeping content over missing it."
)

_EN_UNDERSTAND = (
    "You receive a question and conversation history. Do 2 things:\n"
    "\n"
    "1. REWRITE the question as standalone (merge context from history if needed).\n"
    "   If already clear, keep verbatim.\n"
    "\n"
    "2. CLASSIFY intent (PICK ONE from list):\n"
    "   - factoid: a single concrete fact (price, time, name, yes/no)\n"
    "   - comparison: compare 2+ items, A vs B, differences\n"
    "   - multi_hop: multi-step reasoning, synthesis across sources\n"
    "   - aggregation: list / total / count / cheapest / most expensive\n"
    "   - greeting: hello, hi, hey, greetings\n"
    "   - feedback: opinion, review, thanks, complaint\n"
    "   - out_of_scope: outside coverage (booking time, weather, jokes, off-topic)\n"
    "\n"
    'Return JSON:\n'
    '{"query": "rewritten question", "intent": "factoid"}'
)

_EN_CONDENSE = (
    "Task: rewrite the question as a STANDALONE question based on history.\n"
    "Rules:\n"
    "- ONLY return the rewritten question, do NOT answer, do NOT explain\n"
    "- Replace pronouns (it, that, that package) with specific names from history\n"
    "- Keep original meaning, add context from history\n"
    "- If the question is already clear, return it verbatim"
)

_EN_REWRITER = (
    "You are a search query optimizer. Your task: convert the user's question into a concise search phrase for a retrieval engine.\n"
    "\n"
    "MANDATORY RULES:\n"
    "1. Do NOT ask the user back. Do NOT generate clarification questions.\n"
    "2. Output ONLY keywords or a SHORT SEARCH PHRASE — not a question.\n"
    "3. Remove filler words: 'please', 'could you', 'I was wondering', 'can you tell me'.\n"
    "4. Keep service names, product names, quantities, and qualifiers ('cheapest', 'fastest', 'premium').\n"
    "5. Expand abbreviations if inferable from context.\n"
    "\n"
    "Examples:\n"
    "User: 'Could you tell me what the price of a regular shampoo is?' → Output: 'regular shampoo price'\n"
    "User: 'what time do you open?' → Output: 'opening hours'\n"
    "User: 'which one is cheapest?' → Output: 'cheapest option'\n"
    "\n"
    "Do NOT output: 'What do you want to know...', 'Would you like...', any question.\n"
    "Output the search phrase directly, no explanation."
)

_EN_REFLECTOR = (
    "Score the answer on 4 criteria using the question AND attached <chunks>:\n"
    "- faithfulness: every fact in the answer MUST appear in <chunks>\n"
    "- completeness: answer covers every part of the question\n"
    "- usefulness: answer is genuinely helpful\n"
    "- relevance: answer stays on the question, no drift\n"
    "\n"
    "Verdict rule:\n"
    "- keep: all 4 criteria are at least acceptable\n"
    "- rewrite: missing info / incorrect fact / rambling / claim absent from <chunks>\n"
    "- reject: completely off-topic or fabricated\n"
    "\n"
    "Return JSON: {\"action\":\"keep\"|\"rewrite\"|\"reject\"}."
)

_EN_DECOMPOSE = (
    "You receive a question that may contain multiple entities or require multi-step reasoning.\n"
    "Break it into 2-4 simple sub-questions, each answerable independently.\n"
    "\n"
    "Rules:\n"
    "- If the question references MULTIPLE entities (numbers/names/identifiers), split EACH entity into a separate sub-question.\n"
    "- For a clear single-entity question, return a 1-element array (do not over-split).\n"
    "- Preserve the original language and context.\n"
    "\n"
    "Examples (structural — domain-neutral):\n"
    "Input: \"What do X and Y say in document A\"\n"
    "Output: [\"What does X say in document A\", \"What does Y say in document A\"]\n"
    "Input: \"What do X, Y, Z specify in document A\"\n"
    "Output: [\"What does X specify in document A\", \"What does Y specify in document A\", \"What does Z specify in document A\"]\n"
    "Input: \"Compare A and B\"\n"
    "Output: [\"What is A\", \"What is B\"]\n"
    "\n"
    "Return JSON array: [\"question 1\", \"question 2\", ...]\n"
    "Return ONLY JSON, no explanation."
)

# Multi-HyDE per-intent rewrite templates (verbatim from i18n.py).
_VI_MQ_FACTOID = (
    "Bạn là search optimizer. Sinh 2-3 cách diễn đạt thay thế cho câu "
    "hỏi factoid (1 thông tin cụ thể) để tăng recall trên BM25 + vector. "
    "Giữ ý nghĩa, đổi từ vựng/cấu trúc.\n"
    "Trả JSON array: [\"biến thể 1\", \"biến thể 2\", ...]. CHỈ JSON, không giải thích."
)
_VI_MQ_MULTI_HOP = (
    "Bạn nhận câu hỏi yêu cầu suy luận nhiều bước. Sinh 2-3 truy vấn "
    "phụ trợ để cover toàn bộ chuỗi suy luận. Mỗi truy vấn độc lập, "
    "trả lời được trên 1 đoạn ngữ cảnh.\n"
    "Trả JSON array: [\"truy vấn 1\", ...]. CHỈ JSON."
)
_VI_MQ_COMPARISON = (
    "Bạn nhận câu hỏi so sánh nhiều entity (A vs B, A khác B thế nào). "
    "Sinh 2-3 truy vấn riêng cho từng entity để retrieval không lẫn "
    "ngữ cảnh.\n"
    "Trả JSON array: [\"truy vấn A\", \"truy vấn B\", ...]. CHỈ JSON."
)
_VI_MQ_AGGREGATION = (
    "Bạn nhận câu hỏi tổng hợp (liệt kê / đếm / so sánh giá / cao nhất). "
    "Sinh 2-3 truy vấn để cover các phần tử cần tổng hợp + tiêu chí so sánh.\n"
    "Trả JSON array. CHỈ JSON."
)
_EN_MQ_FACTOID = (
    "You are a search optimizer. Produce 2-3 alternative phrasings for a "
    "factoid question (one concrete fact) to lift BM25 + vector recall. "
    "Keep meaning, vary vocabulary / structure.\n"
    "Return JSON array: [\"variant 1\", \"variant 2\", ...]. JSON ONLY."
)
_EN_MQ_MULTI_HOP = (
    "You receive a multi-step reasoning question. Produce 2-3 auxiliary "
    "queries covering the reasoning chain. Each query must be independent "
    "and answerable from a single context window.\n"
    "Return JSON array: [\"query 1\", ...]. JSON ONLY."
)
_EN_MQ_COMPARISON = (
    "You receive a comparison question (A vs B). Produce 2-3 separate "
    "queries per entity so retrieval does not blend contexts.\n"
    "Return JSON array: [\"query A\", \"query B\", ...]. JSON ONLY."
)
_EN_MQ_AGGREGATION = (
    "You receive an aggregation question (list / count / cheapest / highest). "
    "Produce 2-3 queries covering the items to aggregate plus the ranking "
    "criterion.\n"
    "Return JSON array. JSON ONLY."
)


# ``greeting_answer`` is intentionally seeded as ``""`` per CLAUDE.md
# "application never injects hardcoded greeting/refusal text" — bot
# owners override via the ``bots.oos_answer_template`` column / per-bot
# greeting field, never via the language pack.
_SEED_ROWS: tuple[tuple[str, str, str], ...] = (
    ("vi", "generator", _VI_GENERATOR),
    ("vi", "grader", _VI_GRADER),
    ("vi", "understand", _VI_UNDERSTAND),
    ("vi", "condense", _VI_CONDENSE),
    ("vi", "rewriter", _VI_REWRITER),
    ("vi", "reflector", _VI_REFLECTOR),
    ("vi", "decompose", _VI_DECOMPOSE),
    ("vi", "greeting_answer", ""),
    ("vi", "multi_query_factoid_prompt", _VI_MQ_FACTOID),
    ("vi", "multi_query_multi_hop_prompt", _VI_MQ_MULTI_HOP),
    ("vi", "multi_query_comparison_prompt", _VI_MQ_COMPARISON),
    ("vi", "multi_query_aggregation_prompt", _VI_MQ_AGGREGATION),
    ("en", "generator", _EN_GENERATOR),
    ("en", "grader", _EN_GRADER),
    ("en", "understand", _EN_UNDERSTAND),
    ("en", "condense", _EN_CONDENSE),
    ("en", "rewriter", _EN_REWRITER),
    ("en", "reflector", _EN_REFLECTOR),
    ("en", "decompose", _EN_DECOMPOSE),
    ("en", "greeting_answer", ""),
    ("en", "multi_query_factoid_prompt", _EN_MQ_FACTOID),
    ("en", "multi_query_multi_hop_prompt", _EN_MQ_MULTI_HOP),
    ("en", "multi_query_comparison_prompt", _EN_MQ_COMPARISON),
    ("en", "multi_query_aggregation_prompt", _EN_MQ_AGGREGATION),
)


def upgrade() -> None:
    conn = op.get_bind()
    for code, prompt_key, content in _SEED_ROWS:
        conn.execute(
            text(
                """
                INSERT INTO language_packs (code, prompt_key, content)
                VALUES (:c, :k, :v)
                ON CONFLICT (code, prompt_key) DO NOTHING
                """
            ),
            {"c": code, "k": prompt_key, "v": content},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            "DELETE FROM language_packs WHERE code IN ('vi', 'en')"
        )
    )
