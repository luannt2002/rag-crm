"""Chat-worker ``pipeline_config`` builder.

Relocated verbatim from ``_handle_chat_received_body`` during the god-file
package split — pure relocation, no logic change. The dict literal + its
three ``_sys_*`` helper locals are wrapped in a single builder so the
pipeline module stays under the line cap.
"""

from __future__ import annotations

import json
from typing import Any

from ragbot.shared.bot_limits import resolve_bot_limit, resolve_semantic_cache_threshold
from ragbot.shared.constants import (
    DEFAULT_ANSWER_AUTONOMY_PERCENT,
    DEFAULT_BATCH_STEP_LOGGING_ENABLED,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
    DEFAULT_CR_ENHANCED_ENABLED,
    DEFAULT_GENERATE_SURFACE_VERBATIM_ENABLED,
    DEFAULT_GROUNDING_CONFIRMED_ACTION,
    DEFAULT_STATS_SERVE_REQUIRE_VALUE,
    STATS_NULL_PRICE_MARKER,
    DEFAULT_STATS_NAME_BY_SHAPE,
    DEFAULT_STATS_BRAND_AWARE,
    DEFAULT_NUMERIC_FIDELITY_ACTION,
    DEFAULT_BRAND_SCOPE_GATE_ACTION,
    DEFAULT_BRAND_SCOPE_NEGATION_PHRASES,
    DEFAULT_EMPTY_ANSWER_GUARD_ENABLED,
    DEFAULT_CLAIM_FIDELITY_SCOPE_PHRASES,
    DEFAULT_CLAIM_FIDELITY_ACTION,
    DEFAULT_GROUNDING_FAILURE_MODE,
    DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
    DEFAULT_STATS_CODE_LOOKUP_ENABLED,
    DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED,
    DEFAULT_CROSS_DOC_RECONCILE_ENABLED,
    DEFAULT_STATS_ROUTE_SKIP_GROUNDING,
    DEFAULT_STATS_SUPERLATIVE_ENABLED,
    DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS,
    DEFAULT_SYSPROMPT_LEAK_SKIP_STATS_ROUTE,
    DEFAULT_XML_WRAP_ENABLED,
    DEFAULT_GENERATION_TEMPERATURE,
    DEFAULT_GROUNDING_CHECK_ENABLED,
    DEFAULT_MULTI_QUERY_ENABLED,
    DEFAULT_MULTI_QUERY_COMPLEXITY_MIN,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_MODEL,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_MULTI_QUERY_TIMEOUT_S,
    DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
    DEFAULT_RERANK_CLIFF_GAP_RATIO,
    DEFAULT_RERANK_CLIFF_MIN_KEEP,
    DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
    DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
    DEFAULT_METADATA_EXTRACTION_MODEL,
    DEFAULT_METADATA_LAYER3_LLM_ENABLED,
    DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
    DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
    DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_TOP_K,
    DEFAULT_RERANKER_MIN_SCORE,
    DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
    DEFAULT_RERANKER_MIN_SCORE_BYPASS,
    DEFAULT_ZEROENTROPY_EMBEDDING_DIM,
    DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
    DEFAULT_RERANK_SKIP_INTENTS,
    DEFAULT_SKIP_REFLECT_INTENTS,
    DEFAULT_SKIP_REWRITE_INTENTS,
    SEMANTIC_CACHE_THRESHOLD,
)

from .config import _cfg_bool, _cfg_float, _cfg_get, _cfg_int, _parse_intent_list

__all__ = ["_build_pipeline_config"]


def _build_pipeline_config(_cfg: dict[str, Any], bot_cfg: Any) -> dict[str, Any]:

    _sys_rerank_top_n = _cfg_int(_cfg, "rag_rerank_top_n", DEFAULT_RERANK_TOP_N)
    _sys_grounding = _cfg_get(
        _cfg, "grounding_check_enabled", DEFAULT_GROUNDING_CHECK_ENABLED,
    )
    _sys_graph_rag = _cfg_get(_cfg, "graph_rag_default_mode", "disabled")

    pipeline_config = {
        # Wave M3.3-A — fallback aligned to canonical DEFAULT_TOP_K.
        "top_k": resolve_bot_limit(bot_cfg, "retrieval_top_k",
                                   system_default=_cfg_int(_cfg, "rag_top_k", DEFAULT_TOP_K)),
        "rerank_top_n": resolve_bot_limit(bot_cfg, "rerank_top_n",
                                          system_default=_sys_rerank_top_n),
        "embedding_dimension": _cfg_int(_cfg, "embedding_dimension", DEFAULT_ZEROENTROPY_EMBEDDING_DIM),
        "condense_history_limit": _cfg_int(_cfg, "pipeline_condense_history_limit", 6),
        # DEAD 2026-07-08 — no consumer reads "grade_chunk_preview" (reports/CONFIG_FLOW_DEEPDIVE_20260708.md):
        # "grade_chunk_preview": _cfg_int(_cfg, "pipeline_grade_chunk_preview", 500),
        "reflect_answer_preview": _cfg_int(_cfg, "pipeline_reflect_answer_preview", 500),
        "crag_fallback_count": _cfg_int(_cfg, "pipeline_crag_fallback_count", 2),
        "max_grade_retries": _cfg_int(_cfg, "pipeline_max_grade_retries", 1),
        "max_reflect_retries": _cfg_int(_cfg, "pipeline_max_reflect_retries", 1),
        # Per-bot override (plan_limits.semantic_cache_threshold or
        # threshold_overrides.semantic_cache_threshold) takes priority
        # over the system_config fallback, which itself falls back to
        # the canonical ``SEMANTIC_CACHE_THRESHOLD`` constant.
        # NOTE: ``resolve_bot_limit`` applies ``max(bot, system)`` for
        # numeric keys (defence vs accidental low-input) — we use
        # ``resolve_semantic_cache_threshold`` here because A/B-testing
        # a LOWER threshold (0.90, 0.85) is the whole purpose of this
        # knob, so per-bot wins outright. Default 0.97 KEPT strict; lower
        # only for explicit A/B opt-in because the semantic cache key
        # does NOT include intent classification (HALLU sacred risk).
        "cache_similarity_threshold": resolve_semantic_cache_threshold(
            bot_cfg,
            system_default=_cfg_float(
                _cfg, "pipeline_cache_similarity_threshold",
                SEMANTIC_CACHE_THRESHOLD,
            ),
        ),
        "graph_recursion_limit": _cfg_int(_cfg, "pipeline_graph_recursion_limit", 50),
        "reranker_model": _cfg_get(_cfg, "reranker_model", None),
        # Cascade Routing per-bot opt-in (Wave A WA-2 + CT-2 + Wave D
        # observability fix). When True, the orchestrator wire reads
        # state["complexity_score"] and asks ModelResolverService.
        # resolve_cascade_runtime for a tier-matched answer model.
        # Default OFF preserves the bot's existing single-model behaviour.
        "cascade_routing_enabled": resolve_bot_limit(
            bot_cfg, "cascade_routing_enabled",
            system_default=False,
        ),
        # HyDE (Hypothetical Document Embeddings) per-bot opt-in. When
        # True, the retrieve embed step asks the cheap LLM tier to
        # draft a short hypothetical answer and embeds THAT in place
        # of the raw query (Gao et al. 2022). Default OFF preserves
        # the legacy raw-query embed path. Mirrors the cascade_routing
        # wire pattern so the test/chat endpoint and worker share an
        # identical pipeline_config shape.
        "hyde_enabled": resolve_bot_limit(
            bot_cfg, "hyde_enabled",
            system_default=False,
        ),
        "permission_filtering_enabled": _cfg_get(_cfg, "permission_filtering_enabled", False),
        "permission_default_public": _cfg_get(_cfg, "permission_default_public", True),
        "late_chunking_enabled": _cfg_get(_cfg, "late_chunking_enabled", True),
        "prompt_compression_enabled": _cfg_get(_cfg, "prompt_compression_enabled", True),
        "prompt_compression_max_chars_per_chunk": _cfg_int(_cfg, "prompt_compression_max_chars_per_chunk", 500),
        "whole_doc_enabled": _cfg_get(_cfg, "whole_doc_enabled", True),
        "whole_doc_threshold_chars": _cfg_int(_cfg, "whole_doc_threshold_chars", 8000),
        "parent_child_enabled": _cfg_get(_cfg, "parent_child_enabled", False),
        "graph_rag_mode": resolve_bot_limit(bot_cfg, "graph_rag_mode",
                                            system_default=_sys_graph_rag),
        "graph_rag_max_hops": _cfg_int(_cfg, "graph_rag_max_hops", 2),
        "vietnamese_preprocessing_enabled": resolve_bot_limit(
            bot_cfg, "vietnamese_preprocessing_enabled",
            system_default=_cfg_get(_cfg, "vietnamese_preprocessing_enabled", True)),
        "vietnamese_abbreviations": _cfg_get(_cfg, "vietnamese_abbreviations", "{}"),
        "bm25_use_cover_density": _cfg_get(_cfg, "bm25_use_cover_density", True),
        "bm25_normalization_flags": _cfg_int(_cfg, "bm25_normalization_flags", 5),
        "grounding_check_enabled": resolve_bot_limit(bot_cfg, "grounding_check_enabled",
                                                     system_default=_sys_grounding),
        "grounding_check_threshold": resolve_bot_limit(
            bot_cfg, "grounding_check_threshold",
            system_default=_cfg_float(_cfg, "grounding_check_threshold", 0.3),
        ),
        # Wave M3.6-L4 — per-intent threshold dict (JSONB).
        # WHY: multi_entity / comparison answers often partial-cover the
        # asked entities. Fixed 0.5 threshold blocked Q14/Q16/Q17
        # (top_score 0.4-0.8) in load tests. Nới sang 0.4 cho 2 intent
        # cụ thể; default 0.5 cho factoid / chitchat / hallu_trap to
        # preserve HALLU sacred (trap intents NOT in this dict → fall
        # back to base threshold).
        "grounding_check_threshold_by_intent": _cfg_get(
            _cfg, "grounding_check_threshold_by_intent", None,
        ),
        # 260525 Phase B0 — per-intent retrieval caps in production
        # chat path. Pre-fix the seeded alembic 010x rows
        # ``rerank_top_n_by_intent`` and
        # ``generate_context_chars_cap_by_intent`` were silently
        # dropped because they were missing from _CHAT_CONFIG_KEYS.
        # See reports/DEBUG_BUG7_PIPELINE_CFG_KEYS_20260525.md.
        "rerank_top_n_by_intent": _cfg_get(
            _cfg, "rerank_top_n_by_intent", None,
        ),
        "generate_context_chars_cap_by_intent": _cfg_get(
            _cfg, "generate_context_chars_cap_by_intent", None,
        ),
        "crag_min_fallback_score_by_intent": _cfg_get(
            _cfg, "crag_min_fallback_score_by_intent", None,
        ),
        "mmr_similarity_threshold_by_intent": _cfg_get(
            _cfg, "mmr_similarity_threshold_by_intent", None,
        ),
        # 260526 — per-intent retrieve top_k cap (JSONB dict). None
        # falls back to global DEFAULT_TOP_K at the call site.
        "retrieve_top_k_by_intent": _cfg_get(
            _cfg, "retrieve_top_k_by_intent", None,
        ),
        # 260526 T2-CostPerf — per-intent skip flags. None falls back to
        # DEFAULT_REWRITE_ENABLED_BY_INTENT / DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT
        # constants at query_graph.py call sites. Mirrors test_chat.py for parity.
        "rewrite_enabled_by_intent": _cfg_get(
            _cfg, "rewrite_enabled_by_intent", None,
        ),
        "multi_query_enabled_by_intent": _cfg_get(
            _cfg, "multi_query_enabled_by_intent", None,
        ),
        # 260525 Bug #7c — bulk-populate 78 keys mirror test_chat.py.
        "adaptive_router_l1_enabled": _cfg_get(_cfg, "adaptive_router_l1_enabled", None),
        "bm25_substring_fallback_enabled": _cfg_get(_cfg, "bm25_substring_fallback_enabled", None),
        "crag_grade_concurrency": _cfg_get(_cfg, "crag_grade_concurrency", None),
        "crag_lenient_grade_for_compound_intents_enabled": _cfg_get(_cfg, "crag_lenient_grade_for_compound_intents_enabled", None),
        "crag_min_relevant_count": _cfg_get(_cfg, "crag_min_relevant_count", None),
        "crag_min_relevant_fraction": _cfg_get(_cfg, "crag_min_relevant_fraction", None),
        "decompose_confidence_gate": _cfg_get(_cfg, "decompose_confidence_gate", None),
        "decompose_enabled": _cfg_get(_cfg, "decompose_enabled", None),
        "structured_subanswer_enabled": _cfg_get(_cfg, "structured_subanswer_enabled", None),
        "decompose_min_tokens": _cfg_get(_cfg, "decompose_min_tokens", None),
        "decompose_top_k_per_subquery": _cfg_get(_cfg, "decompose_top_k_per_subquery", None),
        "decompose_use_structured_output": _cfg_get(_cfg, "decompose_use_structured_output", None),
        "draft_model": _cfg_get(_cfg, "draft_model", None),
        "entity_grounding_enabled": _cfg_get(_cfg, "entity_grounding_enabled", None),
        "entity_grounding_max_entities": _cfg_get(_cfg, "entity_grounding_max_entities", None),
        "generate_context_chars_cap": _cfg_get(_cfg, "generate_context_chars_cap", None),
        "generate_context_trust_hint_enabled": _cfg_get(_cfg, "generate_context_trust_hint_enabled", None),
        "generate_p95_sla_ms": _cfg_get(_cfg, "generate_p95_sla_ms", None),
        "generate_use_structured_output": _cfg_get(_cfg, "generate_use_structured_output", None),
        "generic_vocab_enabled": _cfg_get(_cfg, "generic_vocab_enabled", None),
        "generic_vocab_max_expansions": _cfg_get(_cfg, "generic_vocab_max_expansions", None),
        "generic_vocab_max_matches": _cfg_get(_cfg, "generic_vocab_max_matches", None),
        "grade_timeout_s": _cfg_get(_cfg, "grade_timeout_s", None),
        "grade_use_batch": _cfg_get(_cfg, "grade_use_batch", None),
        "grade_use_structured_output": _cfg_get(_cfg, "grade_use_structured_output", None),
        "grounding_check_async_enabled": _cfg_get(_cfg, "grounding_check_async_enabled", None),
        "grounding_check_async_intents": _cfg_get(_cfg, "grounding_check_async_intents", None),
        "grounding_check_async_top_score_threshold": _cfg_get(_cfg, "grounding_check_async_top_score_threshold", None),
        "grounding_intents": _cfg_get(_cfg, "grounding_intents", None),
        "guardrail_oos_similarity_threshold": _cfg_get(_cfg, "guardrail_oos_similarity_threshold", None),
        "intent_extractor_model": _cfg_get(_cfg, "intent_extractor_model", None),
        "intent_extractor_system_prompt": _cfg_get(_cfg, "intent_extractor_system_prompt", None),
        "lexical_rrf_k": _cfg_get(_cfg, "lexical_rrf_k", None),
        "lexical_top_k": _cfg_get(_cfg, "lexical_top_k", None),
        "lost_in_middle_reorder_enabled": _cfg_get(_cfg, "lost_in_middle_reorder_enabled", None),
        "max_total_graph_iterations": _cfg_get(_cfg, "max_total_graph_iterations", None),
        "metadata_extraction_vocabulary": _cfg_get(_cfg, "metadata_extraction_vocabulary", None),
        "multi_query_dedup_threshold": _cfg_get(_cfg, "multi_query_dedup_threshold", None),
        "multi_query_entity_gate_enabled": _cfg_get(_cfg, "multi_query_entity_gate_enabled", None),
        "multi_query_min_tokens": _cfg_get(_cfg, "multi_query_min_tokens", None),
        "multi_query_skip_chitchat_intent": _cfg_get(_cfg, "multi_query_skip_chitchat_intent", None),
        "neighbor_expand_enabled": _cfg_get(_cfg, "neighbor_expand_enabled", None),
        "neighbor_max_concurrency": _cfg_get(_cfg, "neighbor_max_concurrency", None),
        "neighbor_token_budget": _cfg_get(_cfg, "neighbor_token_budget", None),
        "neighbor_window_size": _cfg_get(_cfg, "neighbor_window_size", None),
        "output_tokens_per_response_default": _cfg_get(_cfg, "output_tokens_per_response_default", None),
        "pipeline_multi_query_embed_batch_enabled": _cfg_get(_cfg, "pipeline_multi_query_embed_batch_enabled", None),
        "pipeline_multi_query_speculative_enabled": _cfg_get(_cfg, "pipeline_multi_query_speculative_enabled", None),
        "pipeline_multi_query_speculative_timeout_s": _cfg_get(_cfg, "pipeline_multi_query_speculative_timeout_s", None),
        "pipeline_parallel_cache_understand_enabled": _cfg_get(_cfg, "pipeline_parallel_cache_understand_enabled", None),
        "pipeline_parallel_output_guards_enabled": _cfg_get(_cfg, "pipeline_parallel_output_guards_enabled", None),
        "pipeline_parallel_rewrite_mq_enabled": _cfg_get(_cfg, "pipeline_parallel_rewrite_mq_enabled", None),
        "prompt_token_opt_dedupe_jaccard_threshold": _cfg_get(_cfg, "prompt_token_opt_dedupe_jaccard_threshold", None),
        "prompt_token_opt_enabled": _cfg_get(_cfg, "prompt_token_opt_enabled", None),
        "prompt_token_opt_factoid_skip_history": _cfg_get(_cfg, "prompt_token_opt_factoid_skip_history", None),
        "prompt_token_opt_min_chunk_score": _cfg_get(_cfg, "prompt_token_opt_min_chunk_score", None),
        "rag_rrf_k": _cfg_get(_cfg, "rag_rrf_k", None),
        "reflect_skip_if_grounded": _cfg_get(_cfg, "reflect_skip_if_grounded", None),
        "reflect_skip_top_score_floor": _cfg_get(_cfg, "reflect_skip_top_score_floor", None),
        "reflect_use_structured_output": _cfg_get(_cfg, "reflect_use_structured_output", None),
        "reflection_enabled": _cfg_get(_cfg, "reflection_enabled", None),
        "rerank_threshold_gate_after_cliff_enabled": _cfg_get(_cfg, "rerank_threshold_gate_after_cliff_enabled", None),
        "retrieval_early_exit_threshold": _cfg_get(_cfg, "retrieval_early_exit_threshold", None),
        "retrieval_multistage_enabled": _cfg_get(_cfg, "retrieval_multistage_enabled", None),
        "retrieve_fallback_enabled": _cfg_get(_cfg, "retrieve_fallback_enabled", None),
        "retrieve_fallback_top_k": _cfg_get(_cfg, "retrieve_fallback_top_k", None),
        "rrf_k": _cfg_get(_cfg, "rrf_k", None),
        "self_rag_critique_enabled": _cfg_get(_cfg, "self_rag_critique_enabled", None),
        "self_rag_critique_threshold": _cfg_get(_cfg, "self_rag_critique_threshold", None),
        "skip_understand_for_greeting": _cfg_get(_cfg, "skip_understand_for_greeting", None),
        "speculative_hallu_verify_enabled": _cfg_get(_cfg, "speculative_hallu_verify_enabled", None),
        "speculative_retrieve_enabled": _cfg_get(_cfg, "speculative_retrieve_enabled", None),
        "speculative_retrieve_timeout_s": _cfg_get(_cfg, "speculative_retrieve_timeout_s", None),
        "speculative_similarity_threshold": _cfg_get(_cfg, "speculative_similarity_threshold", None),
        "speculative_streaming_enabled": _cfg_get(_cfg, "speculative_streaming_enabled", None),
        "structured_output_enabled": _cfg_get(_cfg, "structured_output_enabled", None),
        "understand_greeting_patterns": _cfg_get(_cfg, "understand_greeting_patterns", None),
        "understand_skip_below_tokens": _cfg_get(_cfg, "understand_skip_below_tokens", None),
        "understand_use_structured_output": _cfg_get(_cfg, "understand_use_structured_output", None),
        # 260525 Phase B0 Bug #7b — drift closure. Each entry is
        # consumed by query_graph._pcfg but had not been populated
        # for the production worker pipeline (only for the
        # /test/chat endpoint), so operator UPDATE silently no-op'd
        # in production. Defaults mirror the test_chat builder.
        "citation_marker_required": bool(
            _cfg_get(_cfg, "citation_marker_required", False),
        ),
        "embedding_model": _cfg_get(_cfg, "embedding_model", None) or "unknown",
        # Mirrors test_chat: feeds the query-embedding cache key alongside
        # embedding_model/dimension. Missing here would namespace production
        # cache keys under the DEFAULT_EMBEDDING_PROVIDER fallback instead of
        # the bot's actual provider (test_chat vs worker cache-key divergence).
        "embedding_provider": _cfg_get(_cfg, "embedding_provider", None) or "unknown",
        "guardrail_leak_shingle_size": _cfg_int(
            _cfg, "guardrail_leak_shingle_size", 12,
        ),
        "metadata_aware_retrieval_enabled": _cfg_get(
            _cfg, "metadata_aware_retrieval_enabled", False,
        ),
        "metadata_extraction_enabled": _cfg_get(
            _cfg, "metadata_extraction_enabled", False,
        ),
        "metadata_fallback_relax_enabled": _cfg_get(
            _cfg, "metadata_fallback_relax_enabled", False,
        ),
        "reranker_enabled": bool(_cfg_get(_cfg, "reranker_enabled", True)),
        "bot_custom_vocabulary": bot_cfg.custom_vocabulary or {},
        "max_history": resolve_bot_limit(bot_cfg, "max_history",
                                         system_default=_cfg_int(_cfg, "chat_max_history", None)),
        "max_documents": resolve_bot_limit(bot_cfg, "max_documents",
                                           system_default=_cfg_int(_cfg, "rag_max_documents", None)),
        "prompt_max_tokens": resolve_bot_limit(bot_cfg, "prompt_max_tokens",
                                               system_default=_cfg_int(_cfg, "prompt_max_tokens", None)),
        # ── 13 previously missing keys (were using _pcfg defaults, now wired) ──
        "diacritic_restoration_enabled": _cfg_get(_cfg, "diacritic_restoration_enabled", False),
        "diacritic_restoration_use_model": _cfg_get(_cfg, "diacritic_restoration_use_model", False),
        "autocut_enabled": _cfg_get(_cfg, "autocut_enabled", False),
        "autocut_min_gap_ratio": _cfg_float(_cfg, "autocut_min_gap_ratio", 0.3),
        # Reranker min-score is mode-aware; both keys are forwarded so the
        # rerank node picks active vs bypass at runtime. ``reranker_min_score``
        # kept for back-compat with callers reading the single legacy key.
        "reranker_min_score": _cfg_float(
            _cfg, "reranker_min_score", DEFAULT_RERANKER_MIN_SCORE,
        ),
        "reranker_min_score_active": resolve_bot_limit(
            bot_cfg, "reranker_min_score_active",
            system_default=_cfg_float(
                _cfg, "reranker_min_score_active", DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
            ),
        ),
        "reranker_min_score_bypass": _cfg_float(
            _cfg, "reranker_min_score_bypass", DEFAULT_RERANKER_MIN_SCORE_BYPASS,
        ),
        # Cliff-detect adaptive filter — default "threshold" preserves
        # static-cut behaviour; per-bot opt-in via
        # threshold_overrides.rerank_filter_strategy = "cliff".
        "rerank_filter_strategy": resolve_bot_limit(
            bot_cfg, "rerank_filter_strategy",
            system_default=_cfg_get(
                _cfg, "rerank_filter_strategy", DEFAULT_RERANK_FILTER_STRATEGY,
            ),
        ),
        "rerank_cliff_gap_ratio": resolve_bot_limit(
            bot_cfg, "rerank_cliff_gap_ratio",
            system_default=_cfg_float(
                _cfg, "rerank_cliff_gap_ratio", DEFAULT_RERANK_CLIFF_GAP_RATIO,
            ),
        ),
        "rerank_cliff_absolute_floor": resolve_bot_limit(
            bot_cfg, "rerank_cliff_absolute_floor",
            system_default=_cfg_float(
                _cfg, "rerank_cliff_absolute_floor", DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
            ),
        ),
        "rerank_cliff_min_keep": resolve_bot_limit(
            bot_cfg, "rerank_cliff_min_keep",
            system_default=_cfg_int(
                _cfg, "rerank_cliff_min_keep", DEFAULT_RERANK_CLIFF_MIN_KEEP,
            ),
        ),
        "rerank_cliff_skip_intents": _cfg_get(_cfg, "rerank_cliff_skip_intents", None),
        "rerank_retrieval_safety_n": resolve_bot_limit(
            bot_cfg, "rerank_retrieval_safety_n",
            system_default=_cfg_int(
                _cfg, "rerank_retrieval_safety_n", DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
            ),
        ),
        "adaptive_context_enabled": resolve_bot_limit(
            bot_cfg, "adaptive_context_enabled",
            system_default=_cfg_bool(
                _cfg, "adaptive_context_enabled", DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
            ),
        ),
        # Metadata-aware retrieval (Layer 3) — read by query_graph but
        # previously unwired on this prod path (silent fallback).
        "metadata_extraction_model": resolve_bot_limit(
            bot_cfg, "metadata_extraction_model",
            system_default=_cfg_get(
                _cfg, "metadata_extraction_model", DEFAULT_METADATA_EXTRACTION_MODEL,
            ),
        ),
        "metadata_layer3_llm_enabled": resolve_bot_limit(
            bot_cfg, "metadata_layer3_llm_enabled",
            system_default=_cfg_bool(
                _cfg, "metadata_layer3_llm_enabled", DEFAULT_METADATA_LAYER3_LLM_ENABLED,
            ),
        ),
        "semantic_cache_skip_numeric": resolve_bot_limit(
            bot_cfg, "semantic_cache_skip_numeric",
            system_default=_cfg_bool(
                _cfg, "semantic_cache_skip_numeric", DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
            ),
        ),
        "semantic_cache_skip_multi_turn": resolve_bot_limit(
            bot_cfg, "semantic_cache_skip_multi_turn",
            system_default=_cfg_bool(
                _cfg, "semantic_cache_skip_multi_turn", DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
            ),
        ),
        "structural_ref_fallback_pattern": resolve_bot_limit(
            bot_cfg, "structural_ref_fallback_pattern",
            system_default=_cfg_get(
                _cfg, "structural_ref_fallback_pattern", DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
            ),
        ),
        "embedding_query_prefix": _cfg_get(_cfg, "embedding_query_prefix", ""),
        # DEAD 2026-07-08 — no consumer reads it (reports/CONFIG_FLOW_DEEPDIVE_20260708.md):
        # "short_query_word_threshold": _cfg_int(_cfg, "short_query_word_threshold", 5),
        "semantic_cache_ttl_s": _cfg_int(_cfg, "semantic_cache_ttl_s", 3600),
        "crag_min_fallback_score": _cfg_float(
            _cfg, "crag_min_fallback_score", DEFAULT_CRAG_MIN_FALLBACK_SCORE,
        ),
        # S1 Pipeline-Opt — smart-skip CRAG grade+retry when pass-1 top
        # score clears this floor. Resolves: bot threshold_overrides >
        # plan_limits > system_config > constant default (0.7). Set
        # > 1.0 to disable. HALLU sacred preserved by grounding_check.
        "crag_skip_retry_above_score": resolve_bot_limit(
            bot_cfg, "crag_skip_retry_above_score",
            system_default=_cfg_float(
                _cfg, "crag_skip_retry_above_score",
                DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
            ),
        ),
        # Multi-query is opt-in: all 5 keys explicitly forwarded so query_graph
        # cannot fall back to a silent default-True path.
        "multi_query_enabled": _cfg_bool(
            _cfg, "multi_query_enabled", DEFAULT_MULTI_QUERY_ENABLED,
        ),
        # Adaptive-RAG auto-mode floor (0.0 = inert); per-bot/system tunable.
        "multi_query_complexity_min": _cfg_float(
            _cfg, "multi_query_complexity_min", DEFAULT_MULTI_QUERY_COMPLEXITY_MIN,
        ),
        "multi_query_n_variants": _cfg_int(
            _cfg, "multi_query_n_variants", DEFAULT_MULTI_QUERY_N_VARIANTS,
        ),
        "multi_query_max_variants": _cfg_int(
            _cfg, "multi_query_max_variants", DEFAULT_MULTI_QUERY_MAX_VARIANTS,
        ),
        "multi_query_timeout_s": _cfg_int(
            _cfg, "multi_query_timeout_s", DEFAULT_MULTI_QUERY_TIMEOUT_S,
        ),
        "multi_query_model": _cfg_get(
            _cfg, "multi_query_model", DEFAULT_MULTI_QUERY_MODEL,
        ),
        "generation_temperature": _cfg_float(_cfg, "generation_temperature", DEFAULT_GENERATION_TEMPERATURE),
        # DEAD 2026-07-08 — no consumer reads it (reports/CONFIG_FLOW_DEEPDIVE_20260708.md):
        # "default_answer_autonomy_percent": _cfg_int(_cfg, "default_answer_autonomy_percent", DEFAULT_ANSWER_AUTONOMY_PERCENT),
        "skip_rewrite_intents": _parse_intent_list(
            _cfg_get(
                _cfg, "skip_rewrite_intents", json.dumps(list(DEFAULT_SKIP_REWRITE_INTENTS))
            )
        ),
        "skip_reflect_intents": _parse_intent_list(
            _cfg_get(
                _cfg, "skip_reflect_intents", json.dumps(list(DEFAULT_SKIP_REFLECT_INTENTS))
            )
        ),
        "mmr_similarity_threshold": _cfg_float(_cfg, "mmr_similarity_threshold", 0.88),
        "mmr_lambda": _cfg_float(_cfg, "mmr_lambda", 0.7),
        "merge_condense_router": _cfg_get(_cfg, "pipeline_merge_condense_router", True),
        # Refuse short-circuit: when enabled, generate skips the LLM call and
        # returns ``oos_answer_template`` directly on empty graded_chunks.
        # Wired explicitly so the behaviour is visible at the worker layer.
        "refuse_short_circuit_enabled": _cfg_bool(
            _cfg, "refuse_short_circuit_enabled", DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
        ),
        "oos_answer_template": getattr(bot_cfg, "oos_answer_template", None) or "",
        "greeting_response": getattr(bot_cfg, "greeting_response", None) or "",
        "bot_name": getattr(bot_cfg, "bot_name", None) or "",
        # X2 BUNDLED Tier 2 (alembic 0150)
        "action_config": getattr(bot_cfg, "action_config", {}) or {},
        # Phase 14 — per-bot rerank intent whitelist. Forward the parsed
        # ``RerankIntentWhitelist`` (or None) so the rerank node can gate
        # without re-querying DB. None = legacy always-rerank.
        "rerank_intent_whitelist": getattr(
            bot_cfg, "rerank_intent_whitelist", None,
        ),
        # T2.S7 — per-intent rerank skip gate. Lightweight intents bypass
        # the rerank API when the candidate pool already fits inside
        # rerank_top_n. Owner override flows through ``resolve_bot_limit``
        # (threshold_overrides → plan_limits → system_config → constant).
        "rerank_skip_intents": resolve_bot_limit(
            bot_cfg, "rerank_skip_intents",
            system_default=_parse_intent_list(
                _cfg_get(
                    _cfg, "rerank_skip_intents",
                    json.dumps(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
                ),
            ),
        ),
        # Phase-B B4 — defer request_steps writes to a single batched
        # INSERT at end of turn. Default OFF — same per-step behaviour
        # until ops flips system_config.batch_step_logging_enabled.
        "batch_step_logging_enabled": _cfg_bool(
            _cfg, "batch_step_logging_enabled",
            DEFAULT_BATCH_STEP_LOGGING_ENABLED,
        ),
        # CT-3 — Anthropic Contextual Retrieval BM25 hybrid path. When
        # True the lexical adapter widens its tsvector surface to
        # ``content + chunk_context`` so the per-chunk situated context
        # written by the CR enricher (alembic 010l) is rank-visible.
        # LEGAL-RETRIEVAL-FIX 2026-05-21 — 3-tier resolve mirrors the
        # ingest-side chain (plan_limits > system_config > constant).
        # alembic 010r seeds the system_config row to ``true`` so
        # legal / regulatory / structured corpora pick up the BM25
        # widening without explicit per-bot opt-in. Cost-sensitive
        # tenants stay opt-out via plan_limits.cr_enhanced_enabled=false.
        # Storage column NULL rows fall back to content-only via coalesce,
        # so the flag is safe to flip on partially-enriched corpora.
        "cr_enhanced_enabled": resolve_bot_limit(
            bot_cfg, "cr_enhanced_enabled",
            system_default=_cfg_get(
                _cfg, "cr_enhanced_enabled", DEFAULT_CR_ENHANCED_ENABLED,
            ),
        ),
        # B3 Self-Query Retrieval — stats-index routing thresholds.
        # None falls through to constants inside query_graph._pcfg.
        "range_query_min_confidence": _cfg_get(_cfg, "range_query_min_confidence", None),
        "stats_index_limit": _cfg_get(_cfg, "stats_index_limit", None),
        # Race mode: concurrent stats + vector retrieve (opt-in per-bot).
        "stats_index_race_enabled": _cfg_get(_cfg, "stats_index_race_enabled", None),
        "stats_race_timeout_s": _cfg_get(_cfg, "stats_race_timeout_s", None),
        # Per-bot knobs READ by _pcfg but formerly populated by NEITHER builder —
        # on the PRODUCTION worker path they were read-only-never-configurable
        # (a bot owner's override was silently ignored). Same default as the
        # constant so behaviour is unchanged; now overridable via plan_limits.
        "cross_doc_reconcile_enabled": resolve_bot_limit(
            bot_cfg, "cross_doc_reconcile_enabled",
            system_default=DEFAULT_CROSS_DOC_RECONCILE_ENABLED,
        ),
        "xml_wrap_enabled": resolve_bot_limit(
            bot_cfg, "xml_wrap_enabled", system_default=DEFAULT_XML_WRAP_ENABLED,
        ),
        "stats_route_skip_grounding": resolve_bot_limit(
            bot_cfg, "stats_route_skip_grounding",
            system_default=DEFAULT_STATS_ROUTE_SKIP_GROUNDING,
        ),
        "stats_code_lookup_enabled": resolve_bot_limit(
            bot_cfg, "stats_code_lookup_enabled",
            system_default=DEFAULT_STATS_CODE_LOOKUP_ENABLED,
        ),
        "stats_price_of_entity_enabled": resolve_bot_limit(
            bot_cfg, "stats_price_of_entity_enabled",
            system_default=DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED,
        ),
        "stats_superlative_enabled": resolve_bot_limit(
            bot_cfg, "stats_superlative_enabled",
            system_default=DEFAULT_STATS_SUPERLATIVE_ENABLED,
        ),
        "generate_surface_verbatim_enabled": resolve_bot_limit(
            bot_cfg, "generate_surface_verbatim_enabled",
            system_default=DEFAULT_GENERATE_SURFACE_VERBATIM_ENABLED,
        ),
        "grounding_failure_mode": resolve_bot_limit(
            bot_cfg, "grounding_failure_mode",
            system_default=DEFAULT_GROUNDING_FAILURE_MODE,
        ),
        "grounding_confirmed_action": resolve_bot_limit(
            bot_cfg, "grounding_confirmed_action",
            system_default=DEFAULT_GROUNDING_CONFIRMED_ACTION,
        ),
        # Truth-audit option (b): serve-side shell filter (per-bot opt-out).
        "stats_serve_require_value": resolve_bot_limit(
            bot_cfg, "stats_serve_require_value",
            system_default=DEFAULT_STATS_SERVE_REQUIRE_VALUE,
        ),
        # 002-F: explicit price-absent marker in the mixed priced/price-less
        # synthetic chunk (per-bot override for a different convention).
        "stats_null_price_marker": resolve_bot_limit(
            bot_cfg, "stats_null_price_marker",
            system_default=STATS_NULL_PRICE_MARKER,
        ),
        "stats_name_by_shape": resolve_bot_limit(
            bot_cfg, "stats_name_by_shape",
            system_default=DEFAULT_STATS_NAME_BY_SHAPE,
        ),
        "stats_brand_aware": resolve_bot_limit(
            bot_cfg, "stats_brand_aware",
            system_default=DEFAULT_STATS_BRAND_AWARE,
        ),
        # 002-I: numeric-fidelity enforcement (observe|block), owner opt-in.
        "numeric_fidelity_action": resolve_bot_limit(
            bot_cfg, "numeric_fidelity_action",
            system_default=DEFAULT_NUMERIC_FIDELITY_ACTION,
        ),
        "brand_scope_gate_action": resolve_bot_limit(
            bot_cfg, "brand_scope_gate_action",
            system_default=DEFAULT_BRAND_SCOPE_GATE_ACTION,
        ),
        "brand_scope_negation_phrases": resolve_bot_limit(
            bot_cfg, "brand_scope_negation_phrases",
            system_default=DEFAULT_BRAND_SCOPE_NEGATION_PHRASES,
        ),
        # P0.1: empty-answer guard (blank generation → owner oos_answer_template).
        "empty_answer_guard_enabled": resolve_bot_limit(
            bot_cfg, "empty_answer_guard_enabled",
            system_default=DEFAULT_EMPTY_ANSWER_GUARD_ENABLED,
        ),
        # Claim-fidelity (non-numeric scope over-extension), observe|block per-bot.
        "claim_fidelity_scope_phrases": resolve_bot_limit(
            bot_cfg, "claim_fidelity_scope_phrases",
            system_default=DEFAULT_CLAIM_FIDELITY_SCOPE_PHRASES,
        ),
        "claim_fidelity_action": resolve_bot_limit(
            bot_cfg, "claim_fidelity_action",
            system_default=DEFAULT_CLAIM_FIDELITY_ACTION,
        ),
        "guardrail_leak_min_match_count": resolve_bot_limit(
            bot_cfg, "guardrail_leak_min_match_count",
            system_default=DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
        ),
        "sysprompt_leak_skip_intents": resolve_bot_limit(
            bot_cfg, "sysprompt_leak_skip_intents",
            system_default=DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS,
        ),
        "sysprompt_leak_skip_stats_route": resolve_bot_limit(
            bot_cfg, "sysprompt_leak_skip_stats_route",
            system_default=DEFAULT_SYSPROMPT_LEAK_SKIP_STATS_ROUTE,
        ),
    }
    return pipeline_config
