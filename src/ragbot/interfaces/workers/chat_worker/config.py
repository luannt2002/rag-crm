"""Chat-worker config helpers + the batched ``system_config`` key tuple.

Relocated verbatim from the former single-file ``chat_worker.py`` during the
god-file package split. No logic change — pure relocation.
"""

from __future__ import annotations

import json  # noqa: F401 — retained for ``json.JSONDecodeError`` type reference below
from typing import Any

import structlog

from ragbot.shared.json_io import loads as json_loads

logger = structlog.get_logger(__name__)

__all__ = [
    "logger",
    "_parse_intent_list",
    "_cfg_int",
    "_cfg_float",
    "_cfg_bool",
    "_cfg_get",
    "_CHAT_CONFIG_KEYS",
]


def _parse_intent_list(value: Any) -> list[str]:
    """Admin may store intent lists as JSON ('["factoid"]') or already-decoded
    lists. Router downstream does `intent in X` — substring-testing a JSON
    string works by coincidence today but breaks as admin adds values.

    On malformed JSON that LOOKS like a list (starts with `[`), log a warning
    so silent config drift becomes observable. The value is still best-effort
    parsed as CSV so the app keeps running.
    """
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json_loads(stripped)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except json.JSONDecodeError:
                logger.warning("intent_list_malformed_json", raw=stripped[:80])
        return [v.strip() for v in stripped.split(",") if v.strip()]
    return []


def _cfg_int(cfg: dict[str, Any], key: str, default: int | None) -> int | None:
    """Lift an int from a pre-fetched config bundle with the same coercion
    semantics as ``SystemConfigService.get_int`` — string→int, None→default.

    Used by the chat worker's batched config load so the 65 sequential
    DB hits collapse into one ``get_many`` round-trip while preserving
    the exact per-key type the downstream pipeline_config consumers
    expect.
    """
    val = cfg.get(key, default)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _cfg_float(cfg: dict[str, Any], key: str, default: float) -> float:
    """Float counterpart of ``_cfg_int`` — mirrors ``get_float`` coercion."""
    val = cfg.get(key, default)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _cfg_bool(cfg: dict[str, Any], key: str, default: bool) -> bool:
    """Bool counterpart of ``_cfg_int`` — mirrors ``get_bool`` coercion."""
    val = cfg.get(key, default)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def _cfg_get(cfg: dict[str, Any], key: str, default: Any) -> Any:
    """Raw lookup with default — counterpart of ``SystemConfigService.get``."""
    if key not in cfg:
        return default
    val = cfg[key]
    return default if val is None else val


# Keys the chat-worker pulls from ``system_config`` on every chat request.
# Batched in one ``get_many`` round-trip — see Finding #2 perf fix.
_CHAT_CONFIG_KEYS: tuple[str, ...] = (
    "rag_rerank_top_n",
    "grounding_check_enabled",
    "graph_rag_default_mode",
    "rag_top_k",
    "embedding_dimension",
    "pipeline_condense_history_limit",
    # DEAD 2026-07-08 (no consumer reads "grade_chunk_preview"; reports/CONFIG_FLOW_DEEPDIVE_20260708.md):
    # "pipeline_grade_chunk_preview",
    "pipeline_reflect_answer_preview",
    "pipeline_crag_fallback_count",
    "pipeline_max_grade_retries",
    "pipeline_max_reflect_retries",
    "pipeline_cache_similarity_threshold",
    "pipeline_graph_recursion_limit",
    "reranker_model",
    "permission_filtering_enabled",
    "permission_default_public",
    "late_chunking_enabled",
    "prompt_compression_enabled",
    "prompt_compression_max_chars_per_chunk",
    "whole_doc_enabled",
    "whole_doc_threshold_chars",
    "parent_child_enabled",
    "graph_rag_max_hops",
    "vietnamese_preprocessing_enabled",
    "vietnamese_abbreviations",
    "bm25_use_cover_density",
    "bm25_normalization_flags",
    "grounding_check_threshold",
    # Wave M3.6-L4 — per-intent threshold override (JSONB dict).
    # WHY: multi_entity / comparison need lower threshold (0.4) so the
    # grounding judge does not over-reject partial-match answers, while
    # factoid / hallu_trap intents keep the strict 0.5 (HALLU sacred).
    "grounding_check_threshold_by_intent",
    "chat_max_history",
    "rag_max_documents",
    "prompt_max_tokens",
    "diacritic_restoration_enabled",
    "diacritic_restoration_use_model",
    "autocut_enabled",
    "autocut_min_gap_ratio",
    "reranker_min_score",
    "reranker_min_score_active",
    "reranker_min_score_bypass",
    "rerank_filter_strategy",
    "rerank_cliff_gap_ratio",
    "rerank_cliff_absolute_floor",
    "rerank_cliff_min_keep",
    "rerank_cliff_skip_intents",
    "rerank_retrieval_safety_n",
    "adaptive_context_enabled",
    "metadata_extraction_model",
    "metadata_layer3_llm_enabled",
    "semantic_cache_skip_numeric",
    "semantic_cache_skip_multi_turn",
    "structural_ref_fallback_pattern",
    "embedding_query_prefix",
    # DEAD 2026-07-08 (no consumer reads it; reports/CONFIG_FLOW_DEEPDIVE_20260708.md):
    # "short_query_word_threshold",
    "semantic_cache_ttl_s",
    "crag_min_fallback_score",
    "crag_skip_retry_above_score",
    "multi_query_enabled",
    "multi_query_complexity_min",
    "multi_query_n_variants",
    "multi_query_max_variants",
    "multi_query_timeout_s",
    "multi_query_model",
    "generation_temperature",
    # DEAD 2026-07-08 (no consumer reads it; reports/CONFIG_FLOW_DEEPDIVE_20260708.md):
    # "default_answer_autonomy_percent",
    "skip_rewrite_intents",
    "skip_reflect_intents",
    "mmr_similarity_threshold",
    "mmr_lambda",
    "pipeline_merge_condense_router",
    "refuse_short_circuit_enabled",
    "rerank_skip_intents",
    "pipeline_timeout_s",
    "callback_max_retries",
    "callback_timeout_s",
    "callback_verify_ssl",
    "callback_hmac_secret",
    "batch_step_logging_enabled",
    # 260525 Phase B0 — per-intent retrieval caps for the production
    # chat path. Must mirror _PIPELINE_CFG_KEYS in test_chat.py per the
    # parity contract enforced by scripts/audit_pipeline_cfg_parity.py.
    "rerank_top_n_by_intent",
    "generate_context_chars_cap_by_intent",
    "crag_min_fallback_score_by_intent",
    "mmr_similarity_threshold_by_intent",
    # 260526 — per-intent retrieve top_k cap. Mirrors test_chat.py parity.
    "retrieve_top_k_by_intent",
    # 260526 T2-CostPerf — per-intent skip flags for rewrite + multi_query.
    # Mirrors _PIPELINE_CFG_KEYS in test_chat.py for tuple parity.
    "rewrite_enabled_by_intent",
    "multi_query_enabled_by_intent",
    # 260525 Bug #7c — bulk close 78 keys that ``query_graph._pcfg``
    # reads but the chat worker tuple never batched. Mirrors the
    # _PIPELINE_CFG_KEYS additions in test_chat.py to maintain tuple
    # parity (enforced by tests/unit/test_pipeline_cfg_keys_parity.py).
    "adaptive_router_l1_enabled",
    "bm25_substring_fallback_enabled",
    "crag_grade_concurrency",
    "crag_lenient_grade_for_compound_intents_enabled",
    "crag_min_relevant_count",
    "crag_min_relevant_fraction",
    "decompose_confidence_gate",
    "decompose_enabled",
    "structured_subanswer_enabled",
    "decompose_min_tokens",
    "decompose_top_k_per_subquery",
    "decompose_use_structured_output",
    "draft_model",
    "entity_grounding_enabled",
    "entity_grounding_max_entities",
    "generate_context_chars_cap",
    "generate_context_trust_hint_enabled",
    "generate_p95_sla_ms",
    "generate_use_structured_output",
    "generic_vocab_enabled",
    "generic_vocab_max_expansions",
    "generic_vocab_max_matches",
    "grade_timeout_s",
    "grade_use_batch",
    "grade_use_structured_output",
    "grounding_check_async_enabled",
    "grounding_check_async_intents",
    "grounding_check_async_top_score_threshold",
    "grounding_intents",
    "guardrail_oos_similarity_threshold",
    "intent_extractor_model",
    "intent_extractor_system_prompt",
    "lexical_rrf_k",
    "lexical_top_k",
    "lost_in_middle_reorder_enabled",
    "max_total_graph_iterations",
    "metadata_extraction_vocabulary",
    "multi_query_dedup_threshold",
    "multi_query_entity_gate_enabled",
    "multi_query_min_tokens",
    "multi_query_skip_chitchat_intent",
    "neighbor_expand_enabled",
    "neighbor_max_concurrency",
    "neighbor_token_budget",
    "neighbor_window_size",
    "output_tokens_per_response_default",
    "pipeline_multi_query_embed_batch_enabled",
    "pipeline_multi_query_speculative_enabled",
    "pipeline_multi_query_speculative_timeout_s",
    "pipeline_parallel_cache_understand_enabled",
    "pipeline_parallel_output_guards_enabled",
    "pipeline_parallel_rewrite_mq_enabled",
    "prompt_token_opt_dedupe_jaccard_threshold",
    "prompt_token_opt_enabled",
    "prompt_token_opt_factoid_skip_history",
    "prompt_token_opt_min_chunk_score",
    "rag_rrf_k",
    "reflect_skip_if_grounded",
    "reflect_skip_top_score_floor",
    "reflect_use_structured_output",
    "reflection_enabled",
    "rerank_threshold_gate_after_cliff_enabled",
    "retrieval_early_exit_threshold",
    "retrieval_multistage_enabled",
    "retrieve_fallback_enabled",
    "retrieve_fallback_top_k",
    "rrf_k",
    "self_rag_critique_enabled",
    "self_rag_critique_threshold",
    "skip_understand_for_greeting",
    "speculative_hallu_verify_enabled",
    "speculative_retrieve_enabled",
    "speculative_retrieve_timeout_s",
    "speculative_similarity_threshold",
    "speculative_streaming_enabled",
    "structured_output_enabled",
    "understand_greeting_patterns",
    "understand_skip_below_tokens",
    "understand_use_structured_output",
    # 260525 Phase B0 Bug #7b — drift closure with _PIPELINE_CFG_KEYS.
    # Each of these is read by query_graph._pcfg but had been missing
    # from the production worker tuple, so an operator UPDATE on the
    # matching system_config row reached the demo endpoint but never
    # production. Verified via tests/unit/test_pipeline_cfg_keys_parity.py.
    "citation_marker_required",
    "embedding_model",
    "guardrail_leak_shingle_size",
    "metadata_aware_retrieval_enabled",
    "metadata_extraction_enabled",
    "metadata_fallback_relax_enabled",
    "reranker_enabled",
    # LEGAL-RETRIEVAL-FIX 2026-05-21 — system-level default for the
    # WA-3/CT-3 contextual-retrieval BM25 hybrid path. Per-bot override
    # still wins via plan_limits.cr_enhanced_enabled; this row lets the
    # platform default flip ON without touching every bot. Mirrors the
    # ingest-side 3-tier resolve introduced in document_service.py.
    "cr_enhanced_enabled",
    # B3 Self-Query Retrieval — stats-index routing thresholds.
    "range_query_min_confidence",
    "stats_index_limit",
    # Race mode: concurrent stats + vector retrieve (opt-in per-bot).
    "stats_index_race_enabled",
    "stats_race_timeout_s",
)
