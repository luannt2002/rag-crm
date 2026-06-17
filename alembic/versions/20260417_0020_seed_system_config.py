"""0020 — Seed ~50 system_config keys cho mọi runtime-tunable config.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-17
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (key, value_jsonb, value_type, description)
SEED_CONFIGS = [
    # ── LLM ──
    ("llm_default_model", '"gpt-4.1-mini"', "string", "Model mặc định cho generation"),
    ("llm_default_temperature", "0.3", "float", "Temperature mặc định"),
    ("llm_default_max_tokens", "450", "int", "Max tokens mặc định"),
    ("llm_default_top_p", "0.4", "float", "Top-p mặc định"),
    ("llm_timeout_s", "30", "int", "Timeout LLM call (giây)"),
    ("llm_cost_per_prompt_token", "0.0000004", "float", "Chi phí per prompt token ($)"),
    ("llm_cost_per_completion_token", "0.0000016", "float", "Chi phí per completion token ($)"),

    # ── RAG Retrieval ──
    ("rag_top_k", "20", "int", "Số chunks retrieve tối đa (trước rerank)"),
    ("rag_rerank_top_n", "5", "int", "Số chunks giữ lại sau cross-encoder rerank"),
    ("rag_chunk_size", "1024", "int", "Kích thước chunk mặc định (chars)"),
    ("rag_chunk_overlap", "128", "int", "Overlap giữa chunks (chars)"),
    ("rag_rrf_k", "60", "int", "RRF penalty constant (Cormack default)"),
    ("rag_rrf_missing_rank_penalty", "1000", "int", "RRF penalty cho chunk không match"),

    # ── Reranker ──
    ("reranker_model", '"cohere/rerank-v3.5"', "string", "Cross-encoder reranker model (litellm format)"),
    ("reranker_enabled", "true", "bool", "Bật/tắt cross-encoder reranker"),

    # ── Embedding ──
    ("embedding_model", '"text-embedding-3-small"', "string", "Embedding model"),
    ("embedding_dimension", "1536", "int", "Embedding dimension"),

    # ── Chunking Strategy ──
    ("chunking_heading_threshold", "5", "int", "≥ N headings → HDT strategy"),
    ("chunking_avg_len_short", "30", "int", "Avg text < N → recursive strategy"),
    ("chunking_table_threshold", "2", "int", "> N tables + short text → recursive"),
    ("chunking_avg_len_long", "200", "int", "Avg text > N + few headings → semantic"),
    ("chunking_heading_max_for_semantic", "3", "int", "< N headings → eligible for semantic"),
    ("chunking_mixed_content_threshold", "0.3", "float", "> threshold → hybrid strategy"),

    # ── Contextual Enrichment ──
    ("enrichment_enabled", "true", "bool", "Bật/tắt contextual enrichment khi ingest"),
    ("enrichment_model", '"gpt-4.1-mini"', "string", "LLM model cho enrichment"),
    ("enrichment_temperature", "0.0", "float", "Temperature cho enrichment LLM"),
    ("enrichment_max_tokens", "100", "int", "Max tokens cho enrichment prefix"),
    ("enrichment_timeout_s", "10", "int", "Timeout enrichment LLM call (giây)"),
    ("enrichment_doc_preview_chars", "2000", "int", "Chars gửi LLM từ full doc"),
    ("enrichment_chunk_preview_chars", "500", "int", "Chars gửi LLM từ chunk"),
    ("enrichment_max_prefix_chars", "500", "int", "Max chars prefix output"),

    # ── Pipeline Control ──
    ("pipeline_merge_condense_router", "true", "bool", "Merge condense+router thành 1 LLM call (tiết kiệm ~1.5s)"),
    ("pipeline_condense_history_limit", "6", "int", "Số messages cho condense (6 = 3 turns)"),
    ("pipeline_grade_chunk_preview", "500", "int", "Chars gửi cho grade LLM mỗi chunk"),
    ("pipeline_reflect_answer_preview", "500", "int", "Chars gửi cho reflect LLM"),
    ("pipeline_crag_fallback_count", "2", "int", "Top N chunks giữ khi tất cả irrelevant"),
    ("pipeline_max_grade_retries", "1", "int", "CRAG: max rewrite+retrieve retry"),
    ("pipeline_max_reflect_retries", "1", "int", "Self-RAG: max reflect retry"),
    ("pipeline_graph_recursion_limit", "50", "int", "LangGraph max node visits"),
    ("pipeline_cache_similarity_threshold", "0.97", "float", "Semantic cache cosine threshold"),

    # ── Chat ──
    ("chat_max_history", "10", "int", "Số tin nhắn lịch sử tối đa mỗi room"),
    ("question_max_length", "4000", "int", "Độ dài tối đa câu hỏi"),

    # ── Circuit Breaker ──
    ("circuit_breaker_fail_max", "5", "int", "Số lỗi liên tiếp trước khi mở circuit"),
    ("circuit_breaker_reset_timeout", "30", "int", "Giây chờ trước khi thử lại"),

    # ── Rate Limit Defaults ──
    ("rate_limit_default_value", "120", "int", "Rate limit mặc định (requests)"),
    ("rate_limit_default_window", "60", "int", "Rate limit window mặc định (giây)"),

    # ── Audit ──
    ("audit_max_temp_tables", "2", "int", "Số temp tables audit tối đa"),
    ("audit_page_size", "50", "int", "Số records per page audit"),

    # ── Misc ──
    ("default_bot_id", '"1774946011723"', "string", "Bot mặc định (không cho xóa)"),
]

# Keys added in this migration (for downgrade cleanup)
_NEW_KEYS = [
    k for k, *_ in SEED_CONFIGS
    if k not in {
        # Keys already seeded in 0016
        "chat_max_history", "default_bot_id", "audit_page_size",
        "audit_max_temp_tables", "rag_top_k", "rag_chunk_size",
        "rag_chunk_overlap", "llm_default_temperature", "llm_default_max_tokens",
        "llm_default_top_p", "llm_default_model", "llm_timeout_s",
        "question_max_length", "embedding_model", "embedding_dimension",
    }
]


def upgrade() -> None:
    # 1. Insert all missing keys (ON CONFLICT DO NOTHING)
    for key, value, value_type, description in SEED_CONFIGS:
        op.execute(text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:key, CAST(:val AS jsonb), :vtype, :desc, now())
            ON CONFLICT (key) DO NOTHING
        """).bindparams(key=key, val=value, vtype=value_type, desc=description))

    # 2. Update existing rag_top_k from 5 → 20 (broad retrieve for reranking)
    op.execute(text("""
        UPDATE system_config
        SET value = '20',
            description = 'Số chunks retrieve tối đa (trước rerank)',
            updated_at = now()
        WHERE key = 'rag_top_k' AND value::int = 5
    """))


def downgrade() -> None:
    # Revert rag_top_k back to 5
    op.execute(text("""
        UPDATE system_config
        SET value = '5',
            description = 'Số chunks lấy khi vector search',
            updated_at = now()
        WHERE key = 'rag_top_k' AND value::int = 20
    """))

    # Remove only the NEW keys added by this migration
    if _NEW_KEYS:
        op.execute(text(
            "DELETE FROM system_config WHERE key = ANY(:keys)"
        ).bindparams(keys=_NEW_KEYS))
