"""Seed system_config with runtime-tunable keys. Idempotent (ON CONFLICT DO NOTHING).

Usage:
    python scripts/init_system_config.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ragbot.shared.constants import DEFAULT_TOP_K

load_dotenv(Path(_PROJECT_ROOT) / ".env")

# Mirrors migration 0020 SEED_CONFIGS (migration is bootstrap-of-record). Shared
# keys MUST match 0020 byte-for-byte so a script-seeded DB == an alembic-migrated
# DB; tests/unit/test_seed_paths_agree.py pins this invariant. Keys below absent
# from 0020 are script-only extras and are not subject to that check.
SEED_CONFIGS = [
    # --- LLM ---
    ("llm_default_model", '"gpt-4.1-mini"', "string", "Model mặc định cho generation"),
    ("llm_default_temperature", "0.3", "float", "Temperature mặc định"),
    ("llm_default_max_tokens", "450", "int", "Max tokens mặc định"),
    ("llm_default_top_p", "0.4", "float", "Top-p mặc định"),
    ("llm_timeout_s", "30", "int", "Timeout LLM call (giây)"),
    ("llm_cost_per_prompt_token", "0.0000004", "float", "Chi phí per prompt token ($)"),
    ("llm_cost_per_completion_token", "0.0000016", "float", "Chi phí per completion token ($)"),

    # --- RAG Retrieval ---
    ("rag_top_k", str(DEFAULT_TOP_K), "int", "Số chunks retrieve tối đa (trước rerank)"),
    ("rag_rerank_top_n", "5", "int", "Số chunks giữ lại sau cross-encoder rerank"),
    ("bm25_normalization_flags", "5", "int", "PostgreSQL ts_rank normalization bitmask (1=log-length + 4=harmonic distance)"),
    ("bm25_use_cover_density", "true", "bool", "Use ts_rank_cd (cover density) instead of ts_rank for better BM25 approximation"),
    ("rag_chunk_size", "1024", "int", "Kích thước chunk mặc định (chars)"),
    ("rag_chunk_overlap", "128", "int", "Overlap giữa chunks (chars)"),
    ("mmr_similarity_threshold", "0.88", "float", "Jaccard trigram threshold for MMR dedup (0-1, higher = more dedup)"),
    ("mmr_lambda", "0.7", "float", "MMR lambda: 1.0 = pure relevance, 0.0 = max diversity"),
    ("rag_rrf_k", "60", "int", "RRF penalty constant (Cormack default)"),
    ("rag_rrf_missing_rank_penalty", "1000", "int", "RRF penalty cho chunk không match"),

    # --- Reranker (Strategy registry) ---
    ("reranker_provider", '"null"', "string", "Reranker strategy: null|litellm|viranker_local. Default null = no-op."),
    ("reranker_model", '"cohere/rerank-v3.5"', "string", "Cross-encoder reranker model (litellm format) — only consumed when reranker_provider=litellm"),
    ("reranker_enabled", "true", "bool", "Bật/tắt cross-encoder reranker"),
    ("reranker_model_alternatives", '["cohere/rerank-v3.5", "BAAI/bge-reranker-v2-m3", "viranker"]', "string", "Supported reranker models (JSON list for documentation/UI)"),
    ("reranker_min_score", "0.01", "float", "DEPRECATED — use reranker_min_score_{active,bypass}. Kept for backward-compat."),
    ("reranker_min_score_active", "0.15", "float", "Min score floor when reranker is ACTIVE (cross-encoder 0..1). Matches PLAN_LIMIT_SCHEMA default; bots can raise via threshold_overrides for stricter cuts."),
    ("reranker_min_score_bypass", "0.0", "float", "Min score floor when reranker is BYPASSED (RRF score range too small to threshold)."),

    # --- Embedding ---
    ("embedding_model", '"text-embedding-3-small"', "string", "Embedding model"),
    ("embedding_dimension", "1536", "int", "Embedding dimension"),
    ("embedding_model_alternatives", '["text-embedding-3-small", "BAAI/bge-m3", "intfloat/multilingual-e5-large-instruct"]', "string", "Supported embedding models (JSON list for documentation/UI)"),
    ("embedding_query_prefix", '""', "string", "Prefix prepended to queries before embedding (e.g. 'query: ' for E5 models)"),
    ("embedding_passage_prefix", '""', "string", "Prefix prepended to passages during ingest embedding (e.g. 'passage: ' for E5 models)"),

    # --- Chunking Strategy ---
    ("chunking_heading_threshold", "5", "int", "≥ N headings → HDT strategy"),
    ("chunking_avg_len_short", "30", "int", "Avg text < N → recursive strategy"),
    ("chunking_table_threshold", "2", "int", "> N tables + short text → recursive"),
    ("chunking_avg_len_long", "200", "int", "Avg text > N + few headings → semantic"),
    ("chunking_heading_max_for_semantic", "3", "int", "< N headings → eligible for semantic"),
    ("chunking_mixed_content_threshold", "0.3", "float", "> threshold → hybrid strategy"),

    # --- Late Chunking ---
    ("late_chunking_enabled", "true", "bool", "Bật/tắt late chunking (context-aware embedding) khi ingest"),
    ("late_chunking_context_chars", "200", "int", "Số chars document prefix prepend vào chunk trước embedding"),

    # --- Whole-Document Context ---
    ("whole_doc_threshold_chars", "4000", "int", "Documents shorter than this (chars) are stored as single chunk"),
    ("whole_doc_enabled", "true", "bool", "Bật/tắt whole-document single-chunk cho tài liệu nhỏ"),

    # --- Parent-Child Chunking ---
    ("parent_child_enabled", "true", "bool", "Bật/tắt parent-child chunking (small-to-big retrieval)"),
    ("parent_chunk_size", "1024", "int", "Kích thước parent chunk (chars)"),
    ("child_chunk_size", "256", "int", "Kích thước child chunk (chars)"),
    ("child_chunk_overlap", "50", "int", "Overlap giữa child chunks (chars)"),

    # --- Contextual Enrichment ---
    ("enrichment_enabled", "true", "bool", "Bật/tắt contextual enrichment khi ingest"),
    ("enrichment_model", '"gpt-4.1-mini"', "string", "LLM model cho enrichment"),
    ("enrichment_temperature", "0.0", "float", "Temperature cho enrichment LLM"),
    ("enrichment_max_tokens", "100", "int", "Max tokens cho enrichment prefix"),
    ("enrichment_timeout_s", "10", "int", "Timeout enrichment LLM call (giây)"),
    ("enrichment_doc_preview_chars", "2000", "int", "Chars gửi LLM từ full doc"),
    ("enrichment_chunk_preview_chars", "500", "int", "Chars gửi LLM từ chunk"),
    ("enrichment_max_prefix_chars", "500", "int", "Max chars prefix output"),
    ("enrichment_use_cache_pattern", "true", "bool", "Use cache-friendly message structure (document in system, chunk in user)"),
    ("enrichment_max_concurrency", "5", "int", "Max concurrent LLM enrichment calls (semaphore limit)"),

    # ── Contextual Retrieval ──
    ("contextual_retrieval_enabled", "true", "bool", "Per-chunk CR rewrite at ingest (Anthropic 2024-09)"),
    ("contextual_retrieval_model", '"gpt-4.1-mini"', "string", "LiteLLM model id for CR rewrite (cheap; cfg-overridable)"),
    ("contextual_retrieval_context_max_tokens", "100", "int", "Hard cap on CR context-prefix length (tokens)"),
    ("contextual_retrieval_prompt_cache_enabled", "true", "bool", "Wrap full_doc system block in Anthropic cache_control: ephemeral"),
    ("contextual_retrieval_max_doc_chars", "50000", "int", "Cost guard: skip CR for docs longer than this many chars"),

    # ── Pipeline Control ──
    ("pipeline_merge_condense_router", "true", "bool", "Merge condense+router thành 1 LLM call (tiết kiệm ~1.5s)"),
    ("pipeline_condense_history_limit", "6", "int", "Số messages cho condense (6 = 3 turns)"),
    ("pipeline_grade_chunk_preview", "500", "int", "Chars gửi cho grade LLM mỗi chunk"),
    ("pipeline_reflect_answer_preview", "500", "int", "Chars gửi cho reflect LLM"),
    ("pipeline_crag_fallback_count", "2", "int", "Top N chunks giữ khi tất cả irrelevant"),
    ("pipeline_max_grade_retries", "1", "int", "CRAG: max rewrite+retrieve retry"),
    # ── Structured Output ──
    ("structured_output_enabled", "true", "bool",
     "Master flag for provider-side JSON schema enforcement (OpenAI / Anthropic)."),
    ("grade_use_structured_output", "true", "bool",
     "Per-node toggle: CRAG grade node uses structured output when master flag is ON."),
    ("reflect_use_structured_output", "true", "bool",
     "Per-node toggle: Self-RAG reflect node uses structured output when master flag is ON."),
    ("decompose_use_structured_output", "true", "bool",
     "Per-node toggle: multi-hop decompose node uses structured output when master flag is ON."),
    ("generation_temperature", "0.0", "float", "Deterministic generation: 0.0 = same prompt + same chunks → same answer."),
    ("default_answer_autonomy_percent", "0", "int", "Platform floor for answer autonomy 0-100; effective = MAX(bot_col, this)."),
    ("docs_only_strict_enabled", "true", "bool",
     "Prepend strict docs-only rule into system prompt at generate."),
    ("pipeline_max_reflect_retries", "1", "int", "Self-RAG: max reflect retry"),
    ("pipeline_graph_recursion_limit", "50", "int", "LangGraph max node visits"),
    ("pipeline_cache_similarity_threshold", "0.97", "float", "Semantic cache cosine threshold (strict; matches SEMANTIC_CACHE_THRESHOLD)."),

    # ── Prompt Compression ──
    ("prompt_compression_enabled", "true", "bool", "Bật/tắt prompt compression trước generate"),
    ("prompt_compression_max_chars_per_chunk", "500", "int", "Max chars mỗi chunk sau compression"),
    ("lost_in_middle_reorder_enabled", "true", "bool",
     "Reorder graded chunks so top-ranked items sit at start AND end of LLM context."),

    # ── Chat ──
    ("chat_max_history", "10", "int", "Số tin nhắn lịch sử tối đa mỗi room"),
    ("chat_history_ttl_hours", "24", "int", "Chỉ load history trong N giờ gần nhất"),
    ("question_max_length", "4000", "int", "Độ dài tối đa câu hỏi"),

    # ── Circuit Breaker ──
    ("circuit_breaker_fail_max", "5", "int", "Số lỗi liên tiếp trước khi mở circuit"),
    ("circuit_breaker_reset_timeout", "30", "int", "Giây chờ trước khi thử lại"),
    ("circuit_breaker_enabled", "true", "bool",
     "Bật/tắt circuit breaker per-provider trong DynamicLiteLLMRouter"),
    ("chat_worker_concurrency", "4", "int",
     "Số pipeline run đồng thời tối đa per chat_worker process"),
    ("cache_stampede_singleflight_enabled", "true", "bool",
     "Bật/tắt single-flight lock cho semantic_cache miss"),

    # ── Rate Limit Defaults ──
    ("rate_limit_default_value", "120", "int", "Rate limit mặc định (requests)"),
    ("rate_limit_default_window", "60", "int", "Rate limit window mặc định (giây)"),

    # ── Per-tenant rate limit ──
    ("tenant_rate_limit_enabled", "true", "bool", "Enable per-tenant rate limit"),
    ("tenant_rate_limit_per_min", "600", "int", "Default per-tenant requests/minute (10/s)"),
    # ── Per-tenant monthly token cap ──
    ("tenant_token_cap_enabled", "false", "bool", "Enable monthly token cap enforcement (only when tenants.monthly_token_cap is set)."),

    # ── Audit ──
    ("audit_max_temp_tables", "2", "int", "Số temp tables audit tối đa"),
    ("audit_page_size", "50", "int", "Số records per page audit"),

    # ── CI Quality Gate ──
    ("ci_gate_min_correct_pct", "70", "float", "CI gate: minimum % correct answers"),
    ("ci_gate_min_overlap", "0.35", "float", "CI gate: minimum avg keyword overlap"),
    ("ci_gate_min_source_hit", "0.60", "float", "CI gate: minimum source hit rate"),
    ("ci_gate_max_error_pct", "10", "float", "CI gate: maximum % errors allowed"),

    # ── Permission Filtering ──
    ("permission_filtering_enabled", "false", "bool", "Bật/tắt permission filtering khi retrieve"),
    ("permission_default_public", "true", "bool", "Docs không có access_groups được coi là public"),

    # ── GraphRAG ──
    ("graph_rag_default_mode", '"disabled"', "string", "GraphRAG default mode (disabled/enabled/adaptive)"),
    ("graph_rag_max_hops", "2", "int", "Max graph traversal depth"),
    ("graph_rag_max_triples_per_chunk", "10", "int", "Max triples extracted per chunk"),
    ("graph_rag_entity_extraction_model", '""', "string", "Model for entity extraction (fallback to llm_default_model)"),
    ("graph_rag_lazy_mode", "false", "bool", "Skip upfront entity extraction; do lightweight graph lookup at query time only (LazyGraphRAG)"),

    # ── Golden Dataset ──
    ("golden_dataset_max_content_chars", "50000", "int", "Max chars of document content for LLM context"),
    ("golden_dataset_num_questions", "54", "int", "Default number of questions to generate"),
    ("golden_dataset_model", '""', "string", "Model for golden set generation (fallback: llm_default_model)"),
    ("golden_dataset_max_tokens", "16000", "int", "Max tokens for golden set LLM response"),

    # ── Quality Dashboard ──
    ("quality_dashboard_trend_limit", "10", "int", "Number of past evaluations in dashboard trend"),
    ("quality_dashboard_weak_threshold", "60", "int", "Correct % threshold to flag as weak point"),

    # ── Grounding Check ──
    ("grounding_check_enabled", "true", "bool", "LLM-based grounding check cho output guardrail."),
    ("grounding_check_threshold", "0.5", "float", "Tỷ lệ unsupported sentences để trigger warning (0.0-1.0)."),
    ("citation_marker_required", "false", "bool", "Bot phải emit [chunk_id] markers trong answer. Off by default."),

    # ── RAG Pipeline (additional) ──
    ("short_query_word_threshold", "5", "int", "Word count threshold for short query HyDE expansion"),
    ("semantic_cache_ttl_s", "3600", "int", "Semantic cache entry TTL in seconds"),
    ("crag_min_fallback_score", "0.3", "float", "Minimum chunk score for CRAG fallback (below = OOS answer)"),

    # ── Multi-Query Expansion ──
    ("multi_query_enabled", "true", "bool", "Generate N paraphrases of query → parallel retrieve → RRF merge"),
    ("multi_query_model", '"gpt-4.1-mini"', "string", "Cheap LLM for paraphrase generation (cfg-driven)"),
    ("multi_query_n_variants", "3", "int", "Total queries (incl. original). Range 1..multi_query_max_variants"),
    ("multi_query_max_variants", "5", "int", "Hard ceiling on paraphrase count (safety cap)"),
    ("multi_query_timeout_s", "5", "int", "Timeout (giây) cho mỗi paraphrase LLM call"),

    # ── Pipeline Parallel Execution ──
    ("pipeline_parallel_rewrite_mq_enabled", "false", "bool", "Run rewrite + multi_query LLM calls concurrently (default OFF; flip ON per-bot after canary)"),
    ("pipeline_parallel_cache_understand_enabled", "false", "bool", "Run cache_check + understand_query LLM calls concurrently with cancel-on-cache-hit (default OFF)"),
    ("pipeline_multi_query_embed_batch_enabled", "true", "bool", "Pre-batch embed multi-query variants in one HTTP round-trip (default ON; cheap optimisation)"),

    # ── Vietnamese NLP ──
    ("vietnamese_preprocessing_enabled", "true", "bool", "Bật/tắt Vietnamese preprocessing (abbreviation expansion + diacritic normalization) — per-bot override via plan_limits"),
    ("vietnamese_abbreviations", '"{}"', "string", "JSON dict abbreviation overrides (empty = use built-in defaults)"),
    ("vi_compound_segmentation_ingest_enabled", "true", "bool", "Pre-segment VN compounds (underthesea) at ingest for BM25 boundary symmetry."),
    ("vi_compound_segmentation_timeout_s", "5", "int", "Length-based budget for VN segmentation per chunk."),

    # ── Ingestion Validation ──
    ("ingestion_validation_enabled", "true", "bool", "Bật/tắt ingestion quality validation (advisory, không block)"),
    ("ingestion_min_chunk_chars", "20", "int", "Minimum chars per chunk — below triggers validation warning"),

    # ── Document Parser ──
    ("parser_heading_detection", "true", "bool", "Bật/tắt heading detection trong document parser"),
    ("parser_table_detection", "true", "bool", "Bật/tắt table detection trong document parser"),

    # ── Ingestion Cleaning ──
    ("ingestion_cleaning_enabled", "true", "bool", "Document text cleaning khi ingest (NFKC, strip repeated headers/footers)"),

    # ── Metadata Extraction ──
    ("metadata_extraction_enabled", "false", "bool", "LLM-based metadata extraction khi ingest (feature-flagged)"),
    ("metadata_extraction_model", '"gpt-4.1-mini"', "string", "LLM model cho metadata extraction"),
    ("metadata_aware_retrieval_enabled", "false", "bool", "Read-side metadata filter (requires metadata_extraction_enabled + re-ingested corpus)."),
    ("metadata_fallback_relax_enabled", "true", "bool", "Retry without metadata filter when narrowed to 0 (graceful degradation)."),

    # ── Autocut ──
    ("autocut_enabled", "false", "bool", "Dynamic result cutoff sau RRF fusion (score cliff detection)"),
    ("autocut_min_gap_ratio", "0.3", "float", "Minimum score gap ratio để cắt (0.0–1.0)"),

    # ── Memory/Size Limits ──
    ("max_document_size_bytes", "10000000", "int", "Max bytes khi download Google Docs/Sheets (10MB default)"),
    ("max_ingest_content_chars", "500000", "int", "Max chars cho document content khi ingest (~500KB text)"),
    ("conversation_max_messages_load", "20", "int", "Max messages load từ DB per conversation (SQL LIMIT)"),

    # ── Misc ──
    ("default_bot_id", '"1774946011723"', "string", "Bot mặc định (không cho xóa)"),

    # ── Streaming ──
    ("streaming_enabled", "true", "bool", "Bật/tắt SSE streaming response cho chat"),
    ("streaming_word_delay_ms", "30", "int", "Delay giữa các word khi simulated streaming (ms)"),
    ("chat_stream_timeout_s", "60", "int", "Hard timeout (seconds) cho POST /chat/stream pipeline"),
    ("chat_stream_sink_maxsize", "64", "int", "Backpressure size cho SSE sink — producer block khi đầy"),
    ("chat_stream_heartbeat_ms", "15000", "int", "SSE heartbeat interval (ms; 0=disable)"),

    # ── Webhook Callback ──
    ("callback_max_retries", "3", "int", "Max retries khi POST callback_url thất bại"),
    ("callback_timeout_s", "10", "int", "Timeout (giây) cho mỗi callback HTTP POST"),
    ("callback_verify_ssl", "true", "bool", "Verify SSL certificate khi POST callback (disable for dev only)"),
    ("callback_hmac_secret", '""', "string", "Global HMAC secret for signing webhook payloads (empty = no signing)"),
    ("pipeline_timeout_s", "60", "int", "Timeout (giây) cho toàn bộ RAG pipeline execution"),

    # ── DeepEval RAGAS Runner ──
    ("deepeval_enabled", "false", "bool", "DeepEval RAGAS runner gate (opt-in CI/manual). OFF default."),
    ("deepeval_judge_model", '"gpt-4.1-mini"', "string", "Judge model for DeepEval metrics (FaithfulnessMetric et al.)."),
    ("deepeval_faithfulness_threshold", "0.85", "float", "Pass threshold for FaithfulnessMetric (RAGAS faithfulness)."),
    ("deepeval_relevancy_threshold", "0.80", "float", "Pass threshold for AnswerRelevancyMetric."),
    ("deepeval_precision_threshold", "0.75", "float", "Pass threshold for ContextualPrecisionMetric."),
    ("deepeval_recall_threshold", "0.75", "float", "Pass threshold for ContextualRecallMetric."),
]


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)

    inserted = 0
    existed = 0

    async with engine.begin() as conn:
        for key, value, value_type, description in SEED_CONFIGS:
            result = await conn.execute(text("""
                INSERT INTO system_config (key, value, value_type, description, updated_at)
                VALUES (:key, CAST(:val AS jsonb), :vtype, :desc, now())
                ON CONFLICT (key) DO NOTHING
            """), {"key": key, "val": value, "vtype": value_type, "desc": description})

            if result.rowcount:
                inserted += 1
                print(f"  [+] {key} = {value}")
            else:
                existed += 1
                print(f"  [=] {key} (already exists)")

    await engine.dispose()

    print(f"\nDone: {inserted} inserted, {existed} already existed, {len(SEED_CONFIGS)} total keys.")


if __name__ == "__main__":
    asyncio.run(main())
