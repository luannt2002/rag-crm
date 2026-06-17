"""[T1-Smartness] Seed VN backward-compat payload into the 6 domain-neutral slots.

Why
---
Migration 0098 reserved six ``system_config`` rows with empty JSONB so
the runtime resolver could read per-language dicts. This migration lifts
the existing Vietnamese constants out of Python source and into those
rows under the ``"vi"`` key, plus seeds the four per-intent multi-query
rewrite prompts (factoid / multi_hop / comparison / aggregation) into
``language_packs(code='vi', prompt_key=...)``.

Post-migration state — VI bots behave identically to the pre-0098 code
path (legalbot + medispa regression-safe) while ``en`` / ``es`` / other
language tenants can be onboarded by ``UPDATE`` only, no deploy.

Source of truth (mirrored from):
- ``src/ragbot/shared/prompt_compression.py`` (boilerplate regex, 10 patterns)
- ``src/ragbot/shared/vi_tokenizer.py`` (abbreviations seed dict)
- ``src/ragbot/application/services/multi_query_expansion.py`` (4 prompts)

Patterns are serialized as JSON via ``json.dumps`` and passed as bound
parameters — this is the only safe way to embed regex with backslashes
inside ``op.execute(text(...))``. The trailing empty-markdown-header
pattern (``^\\s*#+\\s*$`` with ``re.MULTILINE``) is omitted because the
flag does not round-trip through plain JSON; runtime callers re-apply
``re.MULTILINE`` for that specific pattern if needed.

``upgrade`` UPDATEs the six 0098 rows (replacing ``{}`` with the VI
payload) and INSERTs four ``language_packs`` rows. ``downgrade`` resets
those rows back to ``{}`` and deletes the four prompts, restoring the
0098 post-migration state.

Revision ID: 0099
Revises: 0098
Create Date: 2026-05-14
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────────────────
# VN boilerplate regex — mirrors ``shared/prompt_compression.py`` _BOILERPLATE_PATTERNS.
# Note: trailing ``^\s*#+\s*$`` (re.MULTILINE) intentionally omitted (see docstring).
# ─────────────────────────────────────────────────────────────────────────────
_VI_BOILERPLATE_PATTERNS: list[str] = [
    r"Xem thêm(?:\s+tại)?[:\s].*",
    r"Nguồn\s*:.*",
    r"Click\s+(?:here|vào đây).*",
    r"Đọc thêm\s*:.*",
    r"Tham khảo\s*:.*",
    r"(?:Bài viết|Tin) liên quan\s*:?.*",
    r"Tags?\s*:.*",
    r"Share\s*(?:this|bài|:).*",
    r"Copyright\s*©.*",
    r"All rights reserved.*",
]


# VN abbreviation seed — mirrors ``shared/vi_tokenizer.py`` _VI_ABBREVIATIONS_SEED.
_VI_ABBREVIATIONS: dict[str, str] = {
    "ko": "không", "k": "không", "hk": "không", "hem": "không",
    "dc": "được", "đc": "được",
    "mk": "mình", "mn": "mọi người", "ns": "nói",
    "j": "gì", "gj": "gì", "r": "rồi", "vs": "với",
    "ntn": "như thế nào", "bn": "bao nhiêu", "bnh": "bao nhiêu",
    "tks": "cảm ơn", "ib": "inbox", "rep": "trả lời", "tl": "trả lời",
    "fb": "facebook", "zl": "zalo", "oki": "ok", "okie": "ok",
    "sdt": "số điện thoại", "đt": "điện thoại",
    "nv": "nhân viên", "kh": "khách hàng",
    "sp": "sản phẩm", "dv": "dịch vụ",
    "ck": "chuyển khoản", "tk": "tài khoản",
    "km": "khuyến mãi", "gt": "giới thiệu",
    "lh": "liên hệ", "đk": "đăng ký", "cs": "chính sách",
    "dn": "doanh nghiệp", "cn": "chủ nhật",
    "vd": "ví dụ", "pm": "nhắn tin",
    "cx": "cũng", "cg": "cũng", "ms": "mới",
    "trc": "trước", "nc": "nói chuyện", "bt": "bình thường",
    "nma": "nhưng mà", "thik": "thích", "lm": "làm", "bik": "biết",
    "thui": "thôi", "nha": "nhé",
    "z": "vậy", "v": "vậy",
    "bhxh": "bảo hiểm xã hội", "bhyt": "bảo hiểm y tế",
    "ubnd": "ủy ban nhân dân", "hdld": "hợp đồng lao động",
    "nld": "người lao động", "hđ": "hợp đồng",
}


# VN stopwords — mirrors ``shared/vi_tokenizer.py`` stopword set (negation words
# removed there because they flip query meaning; same exclusion here).
_VI_STOPWORDS: list[str] = (
    "là và của có được cho với trong này đó để từ một các "
    "cũng như đã sẽ khi tại đến hay hoặc nếu thì mà về bị vì "
    "trên dưới ngoài sau trước giữa theo bao nhiêu bao "
    "những rằng lại còn đang ở hơn nhất ra vào nên rất "
    "ai gì nào đây kia ấy thế mỗi vẫn chỉ do bởi"
).split()


# VN section markers — legalese + generic prose headings used by AdapChunk
# structural split. Domain-neutral default; per-bot overrides go in
# ``bots.custom_vocabulary``.
_VI_SECTION_MARKERS: list[str] = [
    "Điều", "Chương", "Mục", "Phần", "Khoản", "Điểm", "Tiết",
    "Chapter", "Section", "Article", "Part",
]


# VN legal reference patterns — extract ``Điều 32``, ``Khoản 1``, ... for
# citation linking. Mirrors what call sites in ``shared/legal_ref.py`` expect.
_VI_LEGAL_REF_PATTERNS: list[str] = [
    r"Điều\s+\d+",
    r"Khoản\s+\d+",
    r"Điểm\s+[a-zđ]\b",
    r"Chương\s+[IVXLCDM\d]+",
    r"Mục\s+\d+",
    r"Article\s+\d+",
    r"Section\s+\d+",
]


# VN knowledge-graph stopwords — function words excluded when building entity
# edges (overlap with retrieval stopwords + a handful of KG-specific noise).
_VI_KG_STOPWORDS: list[str] = _VI_STOPWORDS + [
    "tại", "theo", "gồm", "bao", "thuộc",
]


# Multi-query rewrite prompts — verbatim copy from
# ``application/services/multi_query_expansion.py`` so the per-intent
# behaviour preserved post-migration. ``{n}`` is a runtime format slot
# resolved by the caller; the language_packs row holds the raw template.
_VI_FACTOID_PROMPT = (
    "Bạn là trợ lý viết lại câu hỏi. Cho câu hỏi của người dùng, hãy tạo ra "
    "{n} phiên bản khác nhau diễn đạt cùng ý nghĩa nhưng dùng từ ngữ khác. "
    "Mục tiêu: tăng độ phủ khi tìm kiếm tài liệu. "
    "Trả lời CHÍNH XÁC dạng JSON array của {n} chuỗi, không thêm chú thích.\n"
    'Ví dụ: ["câu hỏi 1", "câu hỏi 2", "câu hỏi 3"]'
)

_VI_MULTI_HOP_PROMPT = (
    "Bạn là trợ lý phân tách câu hỏi đa-bước. Cho câu hỏi người dùng cần kết "
    "hợp nhiều thông tin, hãy phân rã thành {n} câu hỏi con TÁCH BIỆT — mỗi "
    "câu hỏi tập trung vào MỘT khía cạnh hoặc bước phụ thuộc. Tránh diễn đạt "
    "lại cùng ý; ưu tiên các sub-question khác nhau về thực thể, thuộc tính, "
    "hay điều kiện. Trả lời CHÍNH XÁC dạng JSON array của {n} chuỗi, không "
    "thêm chú thích.\n"
    'Ví dụ: ["sub-câu hỏi 1", "sub-câu hỏi 2", "sub-câu hỏi 3"]'
)

_VI_COMPARISON_PROMPT = (
    "Bạn là trợ lý mở rộng câu hỏi so sánh. Cho câu hỏi đối chiếu giữa nhiều "
    "thực thể/lựa chọn, hãy tạo ra {n} biến thể tập trung vào TỪNG cặp thực "
    "thể hoặc TỪNG thuộc tính so sánh. Mỗi biến thể truy hồi tài liệu cho "
    "một entity/attribute riêng để bộ rerank tổng hợp. Trả lời CHÍNH XÁC "
    "dạng JSON array của {n} chuỗi, không thêm chú thích.\n"
    'Ví dụ: ["truy vấn entity-A", "truy vấn entity-B", "truy vấn attribute-X"]'
)

_VI_AGGREGATION_PROMPT = (
    "Bạn là trợ lý mở rộng câu hỏi tổng hợp đa-thuộc tính. Cho câu hỏi cần "
    "gom nhiều thuộc tính/khía cạnh, hãy tạo {n} biến thể — mỗi biến thể "
    "diễn đạt câu hỏi như một giả thuyết câu trả lời (HyDE answer-template) "
    "tập trung vào MỘT thuộc tính cụ thể. Hypothesis ngắn gọn, dùng ngôn "
    "ngữ tài liệu tham chiếu thường dùng. Trả lời CHÍNH XÁC dạng JSON array "
    "của {n} chuỗi, không thêm chú thích.\n"
    'Ví dụ: ["giả thuyết về thuộc tính 1", "giả thuyết về thuộc tính 2"]'
)


# (system_config key, JSON payload) tuples. Payload is the full per-language
# dict — this migration only seeds the ``vi`` slot.
_SYSTEM_CONFIG_PAYLOADS: tuple[tuple[str, dict], ...] = (
    ("boilerplate_removal_patterns_by_language", {"vi": _VI_BOILERPLATE_PATTERNS}),
    ("stopwords_by_language",                    {"vi": _VI_STOPWORDS}),
    ("default_abbreviations_by_language",        {"vi": _VI_ABBREVIATIONS}),
    ("section_markers_by_language",              {"vi": _VI_SECTION_MARKERS}),
    ("legal_ref_patterns_by_language",           {"vi": _VI_LEGAL_REF_PATTERNS}),
    ("knowledge_graph_stopwords_by_language",    {"vi": _VI_KG_STOPWORDS}),
)


# language_packs rows for the 4 multi-query intent prompts.
_LANGUAGE_PACK_ROWS: tuple[tuple[str, str, str], ...] = (
    ("vi", "multi_query_factoid_prompt",     _VI_FACTOID_PROMPT),
    ("vi", "multi_query_multi_hop_prompt",   _VI_MULTI_HOP_PROMPT),
    ("vi", "multi_query_comparison_prompt",  _VI_COMPARISON_PROMPT),
    ("vi", "multi_query_aggregation_prompt", _VI_AGGREGATION_PROMPT),
)


_UPDATE_CONFIG_SQL = text(
    """
    UPDATE system_config
       SET value = (:payload)::jsonb,
           value_type = 'json'
     WHERE key = :key
    """
)


_RESET_CONFIG_SQL = text(
    """
    UPDATE system_config
       SET value = '{}'::jsonb,
           value_type = 'json'
     WHERE key = :key
    """
)


_UPSERT_PACK_SQL = text(
    """
    INSERT INTO language_packs (code, prompt_key, content)
    VALUES (:code, :prompt_key, :content)
    ON CONFLICT (code, prompt_key) DO UPDATE
    SET content = EXCLUDED.content,
        updated_at = now()
    """
)


_DELETE_PACK_SQL = text(
    """
    DELETE FROM language_packs
     WHERE code = :code AND prompt_key = :prompt_key
    """
)


def upgrade() -> None:
    for key, payload in _SYSTEM_CONFIG_PAYLOADS:
        op.execute(
            _UPDATE_CONFIG_SQL.bindparams(
                key=key,
                payload=json.dumps(payload, ensure_ascii=False),
            )
        )

    for code, prompt_key, content in _LANGUAGE_PACK_ROWS:
        op.execute(
            _UPSERT_PACK_SQL.bindparams(
                code=code,
                prompt_key=prompt_key,
                content=content,
            )
        )


def downgrade() -> None:
    for key, _payload in _SYSTEM_CONFIG_PAYLOADS:
        op.execute(_RESET_CONFIG_SQL.bindparams(key=key))

    for code, prompt_key, _content in _LANGUAGE_PACK_ROWS:
        op.execute(_DELETE_PACK_SQL.bindparams(code=code, prompt_key=prompt_key))
