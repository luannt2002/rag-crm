"""i18n — LanguagePack dataclass + in-memory fallback for boot-time outages.

Source of truth: ``language_packs`` table (migrations 0055/0056) +
``LanguagePackService`` (cache-first reader). Adding a new language is
a SQL INSERT against ``language_packs`` — never a code change.

This module retains:

- ``LanguagePack`` dataclass — ergonomic accessor for orchestration code
  that reads multiple prompt fields per node.
- ``_VI_PACK`` / ``_EN_PACK`` — verbatim copies of the seed migration
  rows, used as a last-resort fallback when the DB is unreachable at
  boot or before migration 0056 has run. The runtime path always reads
  from the DB-backed service first.
- ``language_pack_from_dict(...)`` — adapter so the DB-driven service
  can hydrate a ``LanguagePack`` from ``{prompt_key → content}`` rows.

Per CLAUDE.md "domain-neutral, application never injects template text":
``greeting_answer`` lives here (and in the DB) as ``""``. Bot owners set
greeting / refusal copy on the bot row.
"""
from __future__ import annotations

from dataclasses import dataclass

from ragbot.shared.constants import DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------
# Multi-HyDE per-intent rewrite prompt templates — kept verbatim equal to the
# rows seeded into the ``language_packs`` DB table so the in-memory fallback
# and DB path never drift (HALLU=0 invariant; the seed migration parity test
# pins this).
# ---------------------------------------------------------------------------
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


@dataclass(frozen=True)
class LanguagePack:
    """Platform-internal prompts for one language."""

    code: str
    prompt_generator: str
    prompt_grader: str
    prompt_understand: str
    prompt_condense: str
    condense_user_role: str
    condense_bot_role: str
    condense_history_label: str
    condense_new_question_label: str
    condense_standalone_label: str
    prompt_rewriter: str
    prompt_reflector: str
    prompt_decompose: str
    greeting_answer: str
    # Multi-HyDE per-intent rewrite prompts (resolved via
    # ``MULTI_QUERY_INTENT_PROMPT_KEYS``). When ``language_pack_service``
    # is wired, the runtime reads these from the ``language_packs`` DB
    # table; the in-memory pack is the fallback path for tests / dev.
    # Naming matches the ``f"prompt_{key}"`` convention applied by
    # callers / migration-seed parity tests.
    prompt_multi_query_factoid_prompt: str = ""
    prompt_multi_query_multi_hop_prompt: str = ""
    prompt_multi_query_comparison_prompt: str = ""
    prompt_multi_query_aggregation_prompt: str = ""
    # OOS / refuse fallback text — tier 6 of OosTemplateResolver chain.
    # Owners override via bots.oos_answer_template (tier 1) or
    # bots.plan_limits (tier 2); platform default lives in
    # system_config.oos_answer_template (tier 5). Empty default means
    # the resolver returns "" — caller emits no text.
    refuse_message: str = ""
    # Platform-default sysprompt rules — appended to bot.system_prompt by
    # SysPromptAssembler service. Per-locale text seeded in DB by alembic
    # 0146. Empty default in this in-memory fallback pack means
    # assembler returns bot.system_prompt unchanged when DB unseeded.
    sysprompt_default_rules: str = ""


# ---------------------------------------------------------------------------
# Vietnamese pack — copied verbatim from orchestration/query_graph.py
# ---------------------------------------------------------------------------
_VI_PACK = LanguagePack(
    code="vi",
    prompt_generator=(
        "Bạn là trợ lý trả lời dựa trên tài liệu trong thẻ <context>.\n"
        "Chỉ dùng thông tin trong <context>; nếu thiếu, hãy nói rõ là không có dữ liệu.\n"
        "Trả lời bằng tiếng Việt tự nhiên.\n"
        # Fix 1.3 RAGAS AnsRel + 1.4 chunk-quote mandate + 1.5 no synthesis
        # — 2026-05-27 plan 260527-ragas-80-percent.
        "QUY TẮC TRẢ LỜI:\n"
        "1. Câu đầu tiên trả lời THẲNG câu hỏi. KHÔNG mở đầu bằng 'Dạ,', "
        "tên Điều/Chương, hoặc tiêu đề tài liệu.\n"
        "2. Câu hỏi đếm (bao nhiêu) → cho số trước, giải thích sau.\n"
        "3. Câu hỏi yes/no → 'Có' hoặc 'Không' trước, lý do sau.\n"
        "4. Câu hỏi liệt kê → tối đa 7 mục quan trọng nhất, mỗi mục 1 dòng "
        "ngắn gọn. KHÔNG lặp lại tiêu đề tài liệu.\n"
        "5. Câu hỏi so sánh → nêu điểm khác biệt CHÍNH trước, chi tiết sau "
        "(dạng bảng hoặc gạch đầu dòng A vs B).\n"
        "6. KHI <context> RỖNG hoặc không chứa thông tin trả lời câu hỏi: "
        "CHỈ output template từ chối, KHÔNG suy đoán, KHÔNG nhắc tên tài liệu "
        "hoặc Điều cụ thể.\n"
        "7. Với câu hỏi liệt kê/đếm, ưu tiên QUOTE nguyên văn text từ "
        "<context> thay vì paraphrase.\n"
        "8. KHÔNG ghép claims từ 2 chunks khác nhau tạo claim mới. "
        "Nếu cần so sánh, present từng chunk riêng + nguồn (Điều X)."
    ),
    prompt_grader=(
        "Đánh giá đoạn nội dung có hỗ trợ trả lời câu hỏi không.\n"
        "Nội dung có thể là văn bản, bảng giá, danh sách, FAQ, hoặc dữ liệu.\n"
        "Trả về một trong 3 mức:\n"
        "- yes: nội dung TRỰC TIẾP trả lời được câu hỏi (toàn bộ hoặc phần lớn)\n"
        "- partial: nội dung liên quan và có thể bổ trợ một phần câu trả lời\n"
        "- no: nội dung HOÀN TOÀN không liên quan đến câu hỏi\n"
        "LƯU Ý: Nếu không chắc chắn giữa partial và no, chọn 'partial'. "
        "Ưu tiên giữ lại nội dung hơn là bỏ sót."
    ),
    prompt_understand=(
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
        "   - out_of_scope: nằm ngoài phạm vi (thời tiết, chuyện cười, off-topic ngoài corpus). LƯU Ý: 'đặt lịch' KHÔNG phải out_of_scope — đặt lịch là phần của factoid/aggregation tùy ngữ cảnh dịch vụ.\n"
        "\n"
        'Trả về JSON:\n'
        '{"query": "câu hỏi đã viết lại (đã chuẩn hóa)", "intent": "factoid"}'
    ),
    prompt_condense=(
        "Nhiệm vụ: viết lại câu hỏi thành câu hỏi ĐỘC LẬP dựa trên lịch sử.\n"
        "Quy tắc:\n"
        "- CHỈ trả về câu hỏi viết lại, KHÔNG trả lời, KHÔNG giải thích\n"
        "- Thay thế đại từ (nó, cái đó, gói đó) bằng tên cụ thể từ lịch sử\n"
        "- Giữ nguyên ý nghĩa gốc, thêm ngữ cảnh từ lịch sử\n"
        "- Nếu câu hỏi đã đủ rõ, trả về nguyên văn"
    ),
    condense_user_role="Khách",
    condense_bot_role="Bot",
    condense_history_label="Lịch sử",
    condense_new_question_label="Câu hỏi mới",
    condense_standalone_label="Câu hỏi độc lập",
    prompt_rewriter=(
        "Bạn là search query optimizer. Nhiệm vụ: biến câu hỏi của user thành cụm từ tìm kiếm ngắn gọn cho retrieval engine.\n"
        "\n"
        "QUY TẮC BẮT BUỘC:\n"
        "1. KHÔNG hỏi lại user. KHÔNG generate câu hỏi clarification.\n"
        "2. Output CHỈ là KEYWORDS hoặc SHORT SEARCH PHRASE — không phải câu hỏi.\n"
        "3. Loại bỏ filler words: 'anh ơi', 'chị ơi', 'ạ', 'nhé', 'vậy', 'nha', 'cho tôi hỏi', 'bao nhiêu vậy'.\n"
        "4. Giữ tên dịch vụ, tên sản phẩm, số lượng, tính từ chỉ định ('rẻ nhất', 'nhanh nhất', 'premium').\n"
        "5. Expand viết tắt nếu có thể suy ra từ ngữ cảnh.\n"
        "6. Nếu câu hỏi NÊU SẴN dữ kiện/bối cảnh rồi mới hỏi (vd 'Biết rằng X..., vậy Y là gì?'), CHỈ trích NHU CẦU THÔNG TIN thực (phần Y cần tìm) + các từ khóa định danh để tra cứu; bỏ phần dữ kiện user đã tự nêu, để retrieval tập trung đúng chỗ.\n"
        "\n"
        "Ví dụ:\n"
        "User: 'Anh ơi cho em hỏi giá gói cơ bản nhé ạ?' → Output: 'giá gói cơ bản'\n"
        "User: 'ko biết mấy giờ mở cửa nhỉ' → Output: 'giờ mở cửa'\n"
        "User: 'loại nào rẻ nhất vậy?' → Output: 'loại rẻ nhất'\n"
        "User: 'có combo gì không em ơi' → Output: 'danh sách combo'\n"
        "\n"
        "KHÔNG output: 'Bạn muốn biết...', 'Bạn có muốn...', bất kỳ câu hỏi nào.\n"
        "Output NGAY cụm từ tìm kiếm, không giải thích."
    ),
    prompt_reflector=(
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
    ),
    prompt_decompose=(
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
    ),
    greeting_answer="",
    prompt_multi_query_factoid_prompt=_VI_MQ_FACTOID,
    prompt_multi_query_multi_hop_prompt=_VI_MQ_MULTI_HOP,
    prompt_multi_query_comparison_prompt=_VI_MQ_COMPARISON,
    prompt_multi_query_aggregation_prompt=_VI_MQ_AGGREGATION,
    refuse_message=(
        "Em chưa có thông tin chính xác về vấn đề này trong tài liệu. "
        "Anh/chị có thể đặt câu hỏi khác hoặc liên hệ trực tiếp để được hỗ trợ cụ thể hơn ạ."
    ),
)

# ---------------------------------------------------------------------------
# English pack
# ---------------------------------------------------------------------------
_EN_PACK = LanguagePack(
    code="en",
    prompt_generator=(
        "You are an assistant that answers based on documents in <context>.\n"
        "Use only information present in <context>; if missing, say so explicitly.\n"
        "Reply in natural English.\n"
        # Fix 1.3 RAGAS AnsRel + 1.4 quote mandate + 1.5 no synthesis.
        "ANSWER RULES:\n"
        "1. First sentence answers the question DIRECTLY. No filler "
        "openings, no section titles, no document names.\n"
        "2. Counting questions → number first, explanation after.\n"
        "3. Yes/no questions → 'Yes' or 'No' first, reason after.\n"
        "4. List questions → max 7 most important items, one per line, "
        "brief. Do NOT restate document titles.\n"
        "5. Comparison questions → state the KEY difference first, details "
        "after (table or A vs B bullets).\n"
        "6. When <context> is EMPTY or does not contain the answer: "
        "output ONLY the refusal template. No speculation, no naming the "
        "document or specific articles.\n"
        "7. For listing/counting, prefer VERBATIM quotes from <context> "
        "over paraphrasing.\n"
        "8. Do NOT synthesize claims by combining facts from 2 different "
        "chunks. If comparison needed, present each chunk separately with "
        "source citation."
    ),
    prompt_grader=(
        "Decide whether each passage supports answering the question.\n"
        "Content may be text, pricing, lists, FAQ, or data.\n"
        "Return one of 3 levels:\n"
        "- yes: passage DIRECTLY answers the question (fully or substantially)\n"
        "- partial: passage is related and partially supports an answer\n"
        "- no: passage is COMPLETELY unrelated to the question\n"
        "NOTE: If uncertain between partial and no, choose 'partial'. "
        "Prefer keeping content over missing it."
    ),
    prompt_understand=(
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
        "   - out_of_scope: outside coverage (weather, jokes, off-topic outside corpus). NOTE: 'booking/appointment' is NOT out_of_scope — booking is part of factoid/aggregation depending on service context.\n"
        "\n"
        'Return JSON:\n'
        '{"query": "rewritten question", "intent": "factoid"}'
    ),
    prompt_condense=(
        "Task: rewrite the question as a STANDALONE question based on history.\n"
        "Rules:\n"
        "- ONLY return the rewritten question, do NOT answer, do NOT explain\n"
        "- Replace pronouns (it, that, that package) with specific names from history\n"
        "- Keep original meaning, add context from history\n"
        "- If the question is already clear, return it verbatim"
    ),
    condense_user_role="User",
    condense_bot_role="Bot",
    condense_history_label="History",
    condense_new_question_label="New question",
    condense_standalone_label="Standalone question",
    prompt_rewriter=(
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
        "User: 'Could you tell me what the price of the basic plan is?' → Output: 'basic plan price'\n"
        "User: 'what time do you open?' → Output: 'opening hours'\n"
        "User: 'which one is cheapest?' → Output: 'cheapest option'\n"
        "\n"
        "Do NOT output: 'What do you want to know...', 'Would you like...', any question.\n"
        "Output the search phrase directly, no explanation."
    ),
    prompt_reflector=(
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
    ),
    prompt_decompose=(
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
    ),
    greeting_answer="",
    prompt_multi_query_factoid_prompt=_EN_MQ_FACTOID,
    prompt_multi_query_multi_hop_prompt=_EN_MQ_MULTI_HOP,
    prompt_multi_query_comparison_prompt=_EN_MQ_COMPARISON,
    prompt_multi_query_aggregation_prompt=_EN_MQ_AGGREGATION,
    refuse_message=(
        "I don't have accurate information on this in the available documents. "
        "Please rephrase your question or contact us directly for more specific assistance."
    ),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
PACKS: dict[str, LanguagePack] = {"vi": _VI_PACK, "en": _EN_PACK}


def get_pack(language: str = DEFAULT_LANGUAGE) -> LanguagePack:
    """Get the in-memory language pack for ``language``.

    For an UNSEEDED locale (any code not in ``PACKS`` and not the requested
    default) the last-resort fallback is the ENGLISH pack, not Vietnamese:
    English is the neutral lingua franca for the internal LLM prompts
    (grader / condense / understand / rewriter) on a multi-tenant platform,
    so a Khmer / French / Chinese bot without seeded ``language_packs`` DB
    rows is not driven by Vietnamese prompts. A locale CAN still be fully
    supported by seeding its DB rows (the DB path wins upstream in
    ``_lang``). ``DEFAULT_LANGUAGE`` remains the new-bot default elsewhere.
    """
    pack = PACKS.get(language)
    if pack is not None:
        return pack
    return PACKS.get("en", PACKS[DEFAULT_LANGUAGE])


def language_pack_from_dict(
    code: str,
    rows: dict[str, str],
    *,
    fallback: LanguagePack | None = None,
) -> LanguagePack:
    """Hydrate a ``LanguagePack`` from ``{prompt_key: content}`` DB rows.

    Used by ``LanguagePackService``-backed code paths so orchestration
    keeps the dataclass ergonomics (``pack.prompt_grader`` etc.) without
    knowing about the DB layout. Missing keys fall back to ``fallback``
    (default = the in-memory pack for ``code``), so partially translated
    languages still produce a complete dataclass.
    """
    base = fallback if fallback is not None else PACKS.get(code, PACKS[DEFAULT_LANGUAGE])
    return LanguagePack(
        code=code,
        prompt_generator=rows.get("generator", base.prompt_generator),
        prompt_grader=rows.get("grader", base.prompt_grader),
        prompt_understand=rows.get("understand", base.prompt_understand),
        prompt_condense=rows.get("condense", base.prompt_condense),
        condense_user_role=rows.get("condense_user_role", base.condense_user_role),
        condense_bot_role=rows.get("condense_bot_role", base.condense_bot_role),
        condense_history_label=rows.get("condense_history_label", base.condense_history_label),
        condense_new_question_label=rows.get("condense_new_question_label", base.condense_new_question_label),
        condense_standalone_label=rows.get("condense_standalone_label", base.condense_standalone_label),
        prompt_rewriter=rows.get("rewriter", base.prompt_rewriter),
        prompt_reflector=rows.get("reflector", base.prompt_reflector),
        prompt_decompose=rows.get("decompose", base.prompt_decompose),
        greeting_answer=rows.get("greeting_answer", base.greeting_answer),
        prompt_multi_query_factoid_prompt=rows.get(
            "multi_query_factoid_prompt",
            base.prompt_multi_query_factoid_prompt,
        ),
        prompt_multi_query_multi_hop_prompt=rows.get(
            "multi_query_multi_hop_prompt",
            base.prompt_multi_query_multi_hop_prompt,
        ),
        prompt_multi_query_comparison_prompt=rows.get(
            "multi_query_comparison_prompt",
            base.prompt_multi_query_comparison_prompt,
        ),
        prompt_multi_query_aggregation_prompt=rows.get(
            "multi_query_aggregation_prompt",
            base.prompt_multi_query_aggregation_prompt,
        ),
        refuse_message=rows.get("refuse_message", base.refuse_message),
        sysprompt_default_rules=rows.get(
            "sysprompt_default_rules", base.sysprompt_default_rules,
        ),
    )


__all__ = [
    "LanguagePack",
    "get_pack",
    "language_pack_from_dict",
    "PACKS",
    "DEFAULT_LANGUAGE",
]
