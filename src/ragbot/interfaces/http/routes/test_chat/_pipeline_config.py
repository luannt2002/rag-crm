"""Pipeline-config assembly for the test_chat chat endpoints.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). The
``_build_pipeline_config`` single-source-of-truth + its 200-key tuple live here
to keep ``_shared.py`` focused on request-context helpers. ``_shared`` re-exports
these so external importers (``chat_stream.py``, parity tests) are unchanged.
"""

from __future__ import annotations

from typing import Any

from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.shared.bot_limits import resolve_bot_limit
from ragbot.shared.constants import (
    DEFAULT_ANSWER_AUTONOMY_PERCENT,
    DEFAULT_BATCH_STEP_LOGGING_ENABLED,
    DEFAULT_GUARD_OUTPUT_PARALLEL_ENABLED,
    DEFAULT_HEURISTIC_INTENT_ENABLED,
    HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
    DEFAULT_GENERATION_TEMPERATURE,
    DEFAULT_GROUNDING_CHECK_ENABLED,
    DEFAULT_GROUNDING_CONFIRMED_ACTION,
    DEFAULT_STATS_SERVE_REQUIRE_VALUE,
    STATS_NULL_PRICE_MARKER,
    DEFAULT_GROUNDING_FAILURE_MODE,
    DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
    DEFAULT_GENERATE_SURFACE_VERBATIM_ENABLED,
    DEFAULT_STATS_CODE_LOOKUP_ENABLED,
    DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED,
    DEFAULT_CROSS_DOC_RECONCILE_ENABLED,
    DEFAULT_STATS_ROUTE_SKIP_GROUNDING,
    DEFAULT_STATS_SUPERLATIVE_ENABLED,
    DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS,
    DEFAULT_SYSPROMPT_LEAK_SKIP_STATS_ROUTE,
    DEFAULT_XML_WRAP_ENABLED,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MULTI_QUERY_COMPLEXITY_MIN,
    DEFAULT_MULTI_QUERY_ENABLED,
    DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_MODEL,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_MULTI_QUERY_TIMEOUT_S,
    DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
    DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
    DEFAULT_RERANK_CLIFF_GAP_RATIO,
    DEFAULT_RERANK_CLIFF_MIN_KEEP,
    DEFAULT_RERANK_MAX_CHUNKS_TO_LLM,
    DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
    DEFAULT_ADAPTIVE_CONTEXT_HIGH_SCORE,
    DEFAULT_ADAPTIVE_CONTEXT_MAX_N,
    DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
    DEFAULT_METADATA_EXTRACTION_MODEL,
    DEFAULT_METADATA_LAYER3_LLM_ENABLED,
    DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
    DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
    DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANK_SKIP_INTENTS,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_TOP_K,
    DEFAULT_RERANKER_MIN_SCORE,
    DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
    DEFAULT_RERANKER_MIN_SCORE_BYPASS,
    DEFAULT_SKIP_REFLECT_INTENTS,
    DEFAULT_SKIP_REWRITE_INTENTS,
    DEFAULT_ZEROENTROPY_EMBEDDING_DIM,
    SEMANTIC_CACHE_THRESHOLD,
)


_PIPELINE_CFG_KEYS: tuple[str, ...] = (
    "rag_top_k",
    "rag_rerank_top_n",
    "embedding_dimension",
    "embedding_model",
    "pipeline_condense_history_limit",
    "pipeline_grade_chunk_preview",
    "pipeline_reflect_answer_preview",
    "pipeline_crag_fallback_count",
    "pipeline_max_grade_retries",
    "pipeline_max_reflect_retries",
    "pipeline_cache_similarity_threshold",
    "pipeline_graph_recursion_limit",
    "reranker_model",
    "pipeline_merge_condense_router",
    "guardrail_leak_shingle_size",
    "grounding_check_enabled",
    "grounding_check_threshold",
    # Wave M3.6-L4 — per-intent threshold (JSONB dict); see chat_worker.
    "grounding_check_threshold_by_intent",
    "citation_marker_required",
    "reranker_enabled",
    "reranker_min_score",
    "reranker_min_score_active",
    "reranker_min_score_bypass",
    "rerank_filter_strategy",
    "rerank_cliff_gap_ratio",
    "rerank_cliff_absolute_floor",
    "rerank_cliff_min_keep",
    "rerank_cliff_skip_intents",
    "adaptive_context_enabled",
    "rerank_retrieval_safety_n",
    "metadata_extraction_model",
    "metadata_layer3_llm_enabled",
    "semantic_cache_skip_numeric",
    "semantic_cache_skip_multi_turn",
    "structural_ref_fallback_pattern",
    "crag_min_fallback_score",
    "metadata_aware_retrieval_enabled",
    "metadata_extraction_enabled",
    "metadata_fallback_relax_enabled",
    # Wave M3.1 2026-05-20: 44 keys synced from chat_worker._CHAT_CONFIG_KEYS
    # to close test-vs-prod-parity gap that masked Wave M3 latency lift.
    # Drift between this list and chat_worker._CHAT_CONFIG_KEYS is a bug —
    # an audit script `scripts/audit_pipeline_cfg_parity.py` verifies parity.
    "graph_rag_default_mode",
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
    "chat_max_history",
    "rag_max_documents",
    "prompt_max_tokens",
    "diacritic_restoration_enabled",
    "diacritic_restoration_use_model",
    "autocut_enabled",
    "autocut_min_gap_ratio",
    "embedding_query_prefix",
    "short_query_word_threshold",
    "semantic_cache_ttl_s",
    "crag_skip_retry_above_score",
    "multi_query_enabled",
    "multi_query_complexity_min",
    "multi_query_n_variants",
    "multi_query_max_variants",
    "multi_query_timeout_s",
    "multi_query_model",
    "generation_temperature",
    "default_answer_autonomy_percent",
    "skip_rewrite_intents",
    "skip_reflect_intents",
    "mmr_similarity_threshold",
    "mmr_lambda",
    "refuse_short_circuit_enabled",
    "rerank_skip_intents",
    "pipeline_timeout_s",
    "callback_max_retries",
    "callback_timeout_s",
    "callback_verify_ssl",
    "callback_hmac_secret",
    "batch_step_logging_enabled",
    # 260525 Phase B0 — per-intent retrieval caps (CHUNK-AGGREGATION-
    # UNIVERSAL Phase 3 wire). The query_graph.py rerank node and
    # prompt_build context-cap reader call ``_pcfg(state, "<key>", None)``
    # for both keys; without them in this tuple ``get_many()`` drops them
    # and the per-intent boost is dead code (verified 2026-05-25 live
    # test: rerank_top_n stuck at 10 for intent=aggregation despite
    # alembic 010x seeding the row).
    "rerank_top_n_by_intent",
    "generate_context_chars_cap_by_intent",
    # 260525 Phase B0 — Bug #7a. ``crag_min_fallback_score_by_intent``
    # has a non-None code default (``DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT``)
    # so it never silently zeroed out, but the same drift pattern means
    # operator UPDATE on this row never reaches the call site at
    # query_graph.py:4547 — defeating per-intent tuning via system_config.
    "crag_min_fallback_score_by_intent",
    # 260525 Phase B0 — Bug #7b drift closure. Already in
    # _CHAT_CONFIG_KEYS for the production worker but missing here so
    # the demo endpoint diverged from production behaviour on the WA-3
    # contextual-retrieval hybrid path.
    "cr_enhanced_enabled",
    # 260525 Bug #10 — per-intent MMR similarity threshold. Aggregation
    # queries need looser dedup so row-shape CSV chunks with same
    # column structure but different data values survive MMR.
    "mmr_similarity_threshold_by_intent",
    # 260526 — per-intent retrieve top_k cap. Lightweight intents need 5
    # candidates; aggregation needs 40 to feed the rerank+MMR funnel.
    "retrieve_top_k_by_intent",
    # 260526 T2-CostPerf — per-intent skip flags for rewrite + multi_query.
    # Lightweight intents (greeting/chitchat/factoid/feedback/vu_vo/oos)
    # skip both LLM calls (~3.5s saved/turn). Dict with intent-name keys.
    "rewrite_enabled_by_intent",
    "multi_query_enabled_by_intent",
    # 260525 Bug #7c — bulk close 78 keys that ``query_graph._pcfg``
    # reads but ``_build_pipeline_config`` never populated, so
    # ``get_many()`` skipped them and operator UPDATE on the matching
    # ``system_config`` row silently no-op'd. Every key paired with a
    # caller-side ``_pcfg(state, key, DEFAULT_*)`` fallback so adding
    # them here is purely additive — no existing behaviour changes
    # unless an operator has actively flipped one of these rows in DB.
    # The full list comes from a grep audit of every ``_pcfg(state, "..."``
    # call site in query_graph.py minus the legitimately-elsewhere-populated
    # allowlist in ``test_pipeline_cfg_keys_parity.py::_PCFG_ALLOWLIST``.
    "adaptive_router_l1_enabled",
    "bm25_substring_fallback_enabled",
    "crag_grade_concurrency",
    "crag_lenient_grade_for_compound_intents_enabled",
    "crag_min_relevant_count",
    "crag_min_relevant_fraction",
    "decompose_confidence_gate",
    "decompose_enabled",
    "decompose_min_tokens",
    "structured_subanswer_enabled",
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
    # B3 Self-Query Retrieval — stats-index routing thresholds. Both keys
    # let operators tune the aggregation/comparison route via system_config
    # without redeploy. Default falls through to the constant in query_graph.
    "range_query_min_confidence",
    "stats_index_limit",
    # Race mode: fire stats SQL + vector retrieve concurrently; first non-empty
    # result wins. Opt-in per-bot via pipeline_config (default OFF).
    "stats_index_race_enabled",
    "stats_race_timeout_s",
)


def _coerce_int(value: Any, default: int) -> int:
    """Mirror ``SystemConfigService.get_int`` coercion for batched values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    """Mirror chat_worker._cfg_bool semantics: accept bool/int/str literals."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return default


def _parse_intent_list(value: Any) -> list[str]:
    """Mirror chat_worker._parse_intent_list: JSON-list or CSV→list[str]."""
    import json as _json
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except _json.JSONDecodeError:
                pass
        return [v.strip() for v in stripped.split(",") if v.strip()]
    return []


async def _build_pipeline_config(cfg_svc: SystemConfigService, bot_cfg: Any) -> dict[str, Any]:
    """Single source of truth for pipeline_config keys.

    Previously built inline in two endpoints (chat + stream) — drift was
    inevitable: adding a config key meant updating two dicts and forgetting
    one silently disabled the feature on whichever endpoint was missed.

    Reads ~30 system_config keys via 1 batched ``get_many()`` round-trip
    (CLAUDE.md Async Rule 1 — gather-first / batched I/O).
    """
    raw = await cfg_svc.get_many(list(_PIPELINE_CFG_KEYS))
    return {
        # Wave M3.3-B — fallback aligned to canonical constants
        # (DEFAULT_TOP_K=20, DEFAULT_RERANK_TOP_N=7). Pre-fix the literals
        # ``20`` and ``5`` drifted from Z2 migration 0057 seed (7) so a
        # missing DB row silently regressed quality on first boot.
        "top_k": resolve_bot_limit(
            bot_cfg, "retrieval_top_k",
            system_default=_coerce_int(raw.get("rag_top_k"), DEFAULT_TOP_K),
        ),
        "rerank_top_n": resolve_bot_limit(
            bot_cfg, "rerank_top_n",
            system_default=_coerce_int(raw.get("rag_rerank_top_n"), DEFAULT_RERANK_TOP_N),
        ),
        "embedding_dimension": _coerce_int(
            raw.get("embedding_dimension"), DEFAULT_ZEROENTROPY_EMBEDDING_DIM,
        ),
        "embedding_model": raw.get("embedding_model") or "unknown",
        # Provider for the embedding-cache identity key (F10): provider+model+dim
        # so a provider/dim swap cannot serve stale cross-distribution vectors.
        "embedding_provider": raw.get("embedding_provider") or "unknown",
        "condense_history_limit": _coerce_int(raw.get("pipeline_condense_history_limit"), 6),
        "grade_chunk_preview": _coerce_int(raw.get("pipeline_grade_chunk_preview"), 500),
        "reflect_answer_preview": _coerce_int(raw.get("pipeline_reflect_answer_preview"), 500),
        "crag_fallback_count": _coerce_int(raw.get("pipeline_crag_fallback_count"), 2),
        "max_grade_retries": _coerce_int(raw.get("pipeline_max_grade_retries"), 1),
        "max_reflect_retries": _coerce_int(raw.get("pipeline_max_reflect_retries"), 1),
        "cache_similarity_threshold": _coerce_float(
            raw.get("pipeline_cache_similarity_threshold"), SEMANTIC_CACHE_THRESHOLD,
        ),
        "graph_recursion_limit": _coerce_int(raw.get("pipeline_graph_recursion_limit"), 50),
        "reranker_model": raw.get("reranker_model"),
        "merge_condense_router": raw.get("pipeline_merge_condense_router", True),
        "guardrail_leak_shingle_size": _coerce_int(raw.get("guardrail_leak_shingle_size"), 12),
        "grounding_check_enabled": bool(
            raw.get("grounding_check_enabled", DEFAULT_GROUNDING_CHECK_ENABLED),
        ),
        "grounding_check_threshold": resolve_bot_limit(
            bot_cfg, "grounding_check_threshold",
            system_default=_coerce_float(raw.get("grounding_check_threshold"), 0.3),
        ),
        # Wave M3.6-L4 — per-intent threshold dict (JSONB).
        # WHY: see chat_worker.py for the rationale; this mirror keeps the
        # demo + worker pipelines in lockstep on the same knob shape.
        "grounding_check_threshold_by_intent": raw.get(
            "grounding_check_threshold_by_intent", None,
        ),
        # 260525 Phase B0 — per-intent rerank top_n + context-cap dicts
        # (JSONB). Aggregation queries need wider funnel + more chunks in
        # final context. None falls back to global default at the call
        # site via _pcfg fallback chain. See
        # reports/DEBUG_BUG7_PIPELINE_CFG_KEYS_20260525.md for the
        # discovery + root-cause walk-through.
        "rerank_top_n_by_intent": raw.get("rerank_top_n_by_intent", None),
        "generate_context_chars_cap_by_intent": raw.get(
            "generate_context_chars_cap_by_intent", None,
        ),
        # 260525 Phase B0 — Bug #7a. Operator override per-intent CRAG
        # fallback threshold; ``None`` here lets query_graph.py fall back
        # to ``DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT`` constant.
        "crag_min_fallback_score_by_intent": raw.get(
            "crag_min_fallback_score_by_intent", None,
        ),
        # 260525 Phase B0 — Bug #7b drift closure. Wave A WA-3 / CT-3
        # contextual-retrieval hybrid path; mirrors chat_worker.py so
        # the demo endpoint exercises the same plan_limits/system_config
        # 3-tier resolve.
        "cr_enhanced_enabled": resolve_bot_limit(
            bot_cfg, "cr_enhanced_enabled",
            system_default=bool(raw.get("cr_enhanced_enabled", False)),
        ),
        # 260525 Bug #10 — per-intent MMR similarity threshold dict.
        "mmr_similarity_threshold_by_intent": raw.get(
            "mmr_similarity_threshold_by_intent", None,
        ),
        # 260526 — per-intent retrieve top_k cap (JSONB dict). None
        # falls back to global DEFAULT_TOP_K at the call site.
        "retrieve_top_k_by_intent": raw.get("retrieve_top_k_by_intent", None),
        # 260526 T2-CostPerf — per-intent skip flags. None falls back to
        # DEFAULT_REWRITE_ENABLED_BY_INTENT / DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT
        # constants at the call site in query_graph.py.
        "rewrite_enabled_by_intent": raw.get("rewrite_enabled_by_intent", None),
        "multi_query_enabled_by_intent": raw.get("multi_query_enabled_by_intent", None),
        # 260525 Bug #7c — bulk-populate 78 keys that were missing
        # from this dict so ``_pcfg(state, key, default)`` reads can
        # see the live system_config value when set. None default
        # routes through to the caller-supplied DEFAULT_* fallback at
        # each call site — purely additive change.
        "adaptive_router_l1_enabled": raw.get("adaptive_router_l1_enabled", None),
        "bm25_substring_fallback_enabled": raw.get("bm25_substring_fallback_enabled", None),
        "crag_grade_concurrency": raw.get("crag_grade_concurrency", None),
        "crag_lenient_grade_for_compound_intents_enabled": raw.get("crag_lenient_grade_for_compound_intents_enabled", None),
        "crag_min_relevant_count": raw.get("crag_min_relevant_count", None),
        "crag_min_relevant_fraction": raw.get("crag_min_relevant_fraction", None),
        "decompose_confidence_gate": raw.get("decompose_confidence_gate", None),
        "decompose_enabled": raw.get("decompose_enabled", None),
        "decompose_min_tokens": raw.get("decompose_min_tokens", None),
        "structured_subanswer_enabled": raw.get("structured_subanswer_enabled", None),
        "decompose_top_k_per_subquery": raw.get("decompose_top_k_per_subquery", None),
        "decompose_use_structured_output": raw.get("decompose_use_structured_output", None),
        "draft_model": raw.get("draft_model", None),
        "entity_grounding_enabled": raw.get("entity_grounding_enabled", None),
        "entity_grounding_max_entities": raw.get("entity_grounding_max_entities", None),
        "generate_context_chars_cap": raw.get("generate_context_chars_cap", None),
        "generate_context_trust_hint_enabled": raw.get("generate_context_trust_hint_enabled", None),
        "generate_p95_sla_ms": raw.get("generate_p95_sla_ms", None),
        "generate_use_structured_output": raw.get("generate_use_structured_output", None),
        "generic_vocab_enabled": raw.get("generic_vocab_enabled", None),
        "generic_vocab_max_expansions": raw.get("generic_vocab_max_expansions", None),
        "generic_vocab_max_matches": raw.get("generic_vocab_max_matches", None),
        "grade_timeout_s": raw.get("grade_timeout_s", None),
        "grade_use_batch": raw.get("grade_use_batch", None),
        "grade_use_structured_output": raw.get("grade_use_structured_output", None),
        "grounding_check_async_enabled": raw.get("grounding_check_async_enabled", None),
        "grounding_check_async_intents": raw.get("grounding_check_async_intents", None),
        "grounding_check_async_top_score_threshold": raw.get("grounding_check_async_top_score_threshold", None),
        "grounding_intents": raw.get("grounding_intents", None),
        "guardrail_oos_similarity_threshold": raw.get("guardrail_oos_similarity_threshold", None),
        "intent_extractor_model": raw.get("intent_extractor_model", None),
        "intent_extractor_system_prompt": raw.get("intent_extractor_system_prompt", None),
        "lexical_rrf_k": raw.get("lexical_rrf_k", None),
        "lexical_top_k": raw.get("lexical_top_k", None),
        "lost_in_middle_reorder_enabled": raw.get("lost_in_middle_reorder_enabled", None),
        "max_total_graph_iterations": raw.get("max_total_graph_iterations", None),
        "metadata_extraction_vocabulary": raw.get("metadata_extraction_vocabulary", None),
        "multi_query_dedup_threshold": raw.get("multi_query_dedup_threshold", None),
        "multi_query_entity_gate_enabled": raw.get("multi_query_entity_gate_enabled", None),
        "multi_query_min_tokens": raw.get("multi_query_min_tokens", None),
        "multi_query_skip_chitchat_intent": raw.get("multi_query_skip_chitchat_intent", None),
        "neighbor_expand_enabled": raw.get("neighbor_expand_enabled", None),
        "neighbor_max_concurrency": raw.get("neighbor_max_concurrency", None),
        "neighbor_token_budget": raw.get("neighbor_token_budget", None),
        "neighbor_window_size": raw.get("neighbor_window_size", None),
        "output_tokens_per_response_default": raw.get("output_tokens_per_response_default", None),
        "pipeline_multi_query_embed_batch_enabled": raw.get("pipeline_multi_query_embed_batch_enabled", None),
        "pipeline_multi_query_speculative_enabled": raw.get("pipeline_multi_query_speculative_enabled", None),
        "pipeline_multi_query_speculative_timeout_s": raw.get("pipeline_multi_query_speculative_timeout_s", None),
        "pipeline_parallel_cache_understand_enabled": raw.get("pipeline_parallel_cache_understand_enabled", None),
        "pipeline_parallel_output_guards_enabled": raw.get("pipeline_parallel_output_guards_enabled", None),
        "pipeline_parallel_rewrite_mq_enabled": raw.get("pipeline_parallel_rewrite_mq_enabled", None),
        "prompt_token_opt_dedupe_jaccard_threshold": raw.get("prompt_token_opt_dedupe_jaccard_threshold", None),
        "prompt_token_opt_enabled": raw.get("prompt_token_opt_enabled", None),
        "prompt_token_opt_factoid_skip_history": raw.get("prompt_token_opt_factoid_skip_history", None),
        "prompt_token_opt_min_chunk_score": raw.get("prompt_token_opt_min_chunk_score", None),
        "rag_rrf_k": raw.get("rag_rrf_k", None),
        "reflect_skip_if_grounded": raw.get("reflect_skip_if_grounded", None),
        "reflect_skip_top_score_floor": raw.get("reflect_skip_top_score_floor", None),
        "reflect_use_structured_output": raw.get("reflect_use_structured_output", None),
        "reflection_enabled": raw.get("reflection_enabled", None),
        "rerank_threshold_gate_after_cliff_enabled": raw.get("rerank_threshold_gate_after_cliff_enabled", None),
        "retrieval_early_exit_threshold": raw.get("retrieval_early_exit_threshold", None),
        "retrieval_multistage_enabled": raw.get("retrieval_multistage_enabled", None),
        "retrieve_fallback_enabled": raw.get("retrieve_fallback_enabled", None),
        "retrieve_fallback_top_k": raw.get("retrieve_fallback_top_k", None),
        "rrf_k": raw.get("rrf_k", None),
        "self_rag_critique_enabled": raw.get("self_rag_critique_enabled", None),
        "self_rag_critique_threshold": raw.get("self_rag_critique_threshold", None),
        "skip_understand_for_greeting": raw.get("skip_understand_for_greeting", None),
        "speculative_hallu_verify_enabled": raw.get("speculative_hallu_verify_enabled", None),
        "speculative_retrieve_enabled": raw.get("speculative_retrieve_enabled", None),
        "speculative_retrieve_timeout_s": raw.get("speculative_retrieve_timeout_s", None),
        "speculative_similarity_threshold": raw.get("speculative_similarity_threshold", None),
        "speculative_streaming_enabled": raw.get("speculative_streaming_enabled", None),
        "structured_output_enabled": raw.get("structured_output_enabled", None),
        "understand_greeting_patterns": raw.get("understand_greeting_patterns", None),
        "understand_skip_below_tokens": raw.get("understand_skip_below_tokens", None),
        "understand_use_structured_output": raw.get("understand_use_structured_output", None),
        # Only audit-heavy bots need [chunk_id] citation markers in answers.
        # Off by default — flow/script bots shouldn't emit
        # brackets to end users.
        "citation_marker_required": bool(raw.get("citation_marker_required", False)),
        "reranker_enabled": bool(raw.get("reranker_enabled", True)),
        # T1 audit: reranker min-score is now mode-aware. Both keys
        # are forwarded so query_graph._pcfg picks the right one at runtime.
        "reranker_min_score": _coerce_float(
            raw.get("reranker_min_score"), DEFAULT_RERANKER_MIN_SCORE,
        ),
        "reranker_min_score_active": resolve_bot_limit(
            bot_cfg, "reranker_min_score_active",
            system_default=_coerce_float(
                raw.get("reranker_min_score_active"),
                DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
            ),
        ),
        # Cascade Routing per-bot opt-in (Wave A WA-2 + CT-2). Default
        # OFF preserves single-model behaviour. Mirrors chat_worker.py's
        # production pipeline_config build so the test/chat endpoint
        # exercises the same cascade wire path.
        "cascade_routing_enabled": resolve_bot_limit(
            bot_cfg, "cascade_routing_enabled",
            system_default=False,
        ),
        # HyDE (Hypothetical Document Embeddings) per-bot opt-in. Mirrors
        # the chat_worker.py production pipeline_config build so the
        # test/chat endpoint exercises the same retrieve-embed wire path
        # (cascade-pattern parity, T1.4 Wave F).
        "hyde_enabled": resolve_bot_limit(
            bot_cfg, "hyde_enabled",
            system_default=False,
        ),
        "reranker_min_score_bypass": _coerce_float(
            raw.get("reranker_min_score_bypass"),
            DEFAULT_RERANKER_MIN_SCORE_BYPASS,
        ),
        # Adaptive cliff-detect filter knobs (per-bot opt-in via threshold_overrides).
        "rerank_filter_strategy": resolve_bot_limit(
            bot_cfg, "rerank_filter_strategy",
            system_default=raw.get(
                "rerank_filter_strategy", DEFAULT_RERANK_FILTER_STRATEGY,
            ),
        ),
        "rerank_cliff_gap_ratio": resolve_bot_limit(
            bot_cfg, "rerank_cliff_gap_ratio",
            system_default=_coerce_float(
                raw.get("rerank_cliff_gap_ratio"), DEFAULT_RERANK_CLIFF_GAP_RATIO,
            ),
        ),
        "rerank_cliff_absolute_floor": resolve_bot_limit(
            bot_cfg, "rerank_cliff_absolute_floor",
            system_default=_coerce_float(
                raw.get("rerank_cliff_absolute_floor"),
                DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
            ),
        ),
        "rerank_cliff_min_keep": resolve_bot_limit(
            bot_cfg, "rerank_cliff_min_keep",
            system_default=_coerce_int(
                raw.get("rerank_cliff_min_keep"), DEFAULT_RERANK_CLIFF_MIN_KEEP,
            ),
        ),
        "rerank_cliff_skip_intents": raw.get("rerank_cliff_skip_intents", None),
        "rerank_max_chunks_to_llm": resolve_bot_limit(
            bot_cfg, "rerank_max_chunks_to_llm",
            system_default=_coerce_int(
                raw.get("rerank_max_chunks_to_llm"),
                DEFAULT_RERANK_MAX_CHUNKS_TO_LLM,
            ),
        ),
        "adaptive_context_enabled": resolve_bot_limit(
            bot_cfg, "adaptive_context_enabled",
            system_default=_coerce_bool(
                raw.get("adaptive_context_enabled"), DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
            ),
        ),
        "rerank_retrieval_safety_n": resolve_bot_limit(
            bot_cfg, "rerank_retrieval_safety_n",
            system_default=_coerce_int(
                raw.get("rerank_retrieval_safety_n"), DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
            ),
        ),
        "adaptive_context_high_score": resolve_bot_limit(
            bot_cfg, "adaptive_context_high_score",
            system_default=_coerce_float(
                raw.get("adaptive_context_high_score"), DEFAULT_ADAPTIVE_CONTEXT_HIGH_SCORE,
            ),
        ),
        "adaptive_context_max_n": resolve_bot_limit(
            bot_cfg, "adaptive_context_max_n",
            system_default=_coerce_int(
                raw.get("adaptive_context_max_n"), DEFAULT_ADAPTIVE_CONTEXT_MAX_N,
            ),
        ),
        # Metadata-aware retrieval (Layer 3 LLM extractor) — read by query_graph
        # but previously unwired (silent fallback; Layer-3 feature un-enableable).
        "metadata_extraction_model": resolve_bot_limit(
            bot_cfg, "metadata_extraction_model",
            system_default=raw.get("metadata_extraction_model") or DEFAULT_METADATA_EXTRACTION_MODEL,
        ),
        "metadata_layer3_llm_enabled": resolve_bot_limit(
            bot_cfg, "metadata_layer3_llm_enabled",
            system_default=_coerce_bool(
                raw.get("metadata_layer3_llm_enabled"), DEFAULT_METADATA_LAYER3_LLM_ENABLED,
            ),
        ),
        "semantic_cache_skip_numeric": resolve_bot_limit(
            bot_cfg, "semantic_cache_skip_numeric",
            system_default=_coerce_bool(
                raw.get("semantic_cache_skip_numeric"), DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
            ),
        ),
        "semantic_cache_skip_multi_turn": resolve_bot_limit(
            bot_cfg, "semantic_cache_skip_multi_turn",
            system_default=_coerce_bool(
                raw.get("semantic_cache_skip_multi_turn"), DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
            ),
        ),
        "structural_ref_fallback_pattern": resolve_bot_limit(
            bot_cfg, "structural_ref_fallback_pattern",
            system_default=raw.get("structural_ref_fallback_pattern") or DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
        ),
        "crag_min_fallback_score": _coerce_float(
            raw.get("crag_min_fallback_score"), DEFAULT_CRAG_MIN_FALLBACK_SCORE,
        ),
        # Per-bot override for OOS message (P16 Wave 3 Phase 11)
        "oos_answer_template": getattr(bot_cfg, "oos_answer_template", None),
        "bot_name": getattr(bot_cfg, "bot_name", ""),
        # X2 BUNDLED Tier 2 (alembic 0150) — per-bot action extraction
        # config. Default {} → state tracking OFF, bot behaviour unchanged.
        # When enabled=true, generate node loads + saves state via
        # conversation_state Port (Null/Jsonb registry-driven).
        "action_config": getattr(bot_cfg, "action_config", {}) or {},
        # 2 P0 — propagate the metadata-aware pair into pipeline
        # config so the retrieve node can enforce the read-side ↔ write-side
        # tie. Without these the gate falls back to constants defaults; that
        # is correct (both False) but explicit propagation also lets per-bot
        # plan_limits / system_config overrides flow through.
        "metadata_aware_retrieval_enabled": bool(
            raw.get("metadata_aware_retrieval_enabled", False),
        ),
        "metadata_extraction_enabled": bool(
            raw.get("metadata_extraction_enabled", False),
        ),
        "metadata_fallback_relax_enabled": bool(
            raw.get("metadata_fallback_relax_enabled", True),
        ),
        # Phase 14 — per-bot rerank intent whitelist. Forward the parsed
        # ``RerankIntentWhitelist`` (or None) so the rerank node can gate
        # without re-querying DB. Falls back to None when the column is
        # NULL → legacy always-rerank.
        "rerank_intent_whitelist": getattr(
            bot_cfg, "rerank_intent_whitelist", None,
        ),
        # T2.S7 — per-intent rerank skip gate. Lightweight intents (greeting/
        # chitchat/factoid lookup) bypass rerank when the candidate pool already
        # fits in rerank_top_n. Owner override flows through the standard
        # resolve chain (threshold_overrides → plan_limits → system_config →
        # constant). System default = ``DEFAULT_RERANK_SKIP_INTENTS``.
        "rerank_skip_intents": resolve_bot_limit(
            bot_cfg, "rerank_skip_intents",
            system_default=_parse_intent_list(
                raw.get(
                    "rerank_skip_intents",
                    tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
                ),
            ),
        ),
        # ── Wave M3.1 2026-05-20: 44 keys synced from chat_worker for
        # test-vs-prod-parity. Test endpoint must build identical
        # pipeline_config shape so load tests reflect production behavior.
        # Drift detection: scripts/audit_pipeline_cfg_parity.py runs in CI.
        "graph_rag_mode": resolve_bot_limit(
            bot_cfg, "graph_rag_mode",
            system_default=raw.get("graph_rag_default_mode", "disabled"),
        ),
        "graph_rag_max_hops": _coerce_int(raw.get("graph_rag_max_hops"), 2),
        "permission_filtering_enabled": _coerce_bool(
            raw.get("permission_filtering_enabled"), False,
        ),
        "permission_default_public": _coerce_bool(
            raw.get("permission_default_public"), True,
        ),
        "late_chunking_enabled": _coerce_bool(
            raw.get("late_chunking_enabled"), True,
        ),
        "prompt_compression_enabled": _coerce_bool(
            raw.get("prompt_compression_enabled"), True,
        ),
        "prompt_compression_max_chars_per_chunk": _coerce_int(
            raw.get("prompt_compression_max_chars_per_chunk"), 500,
        ),
        "whole_doc_enabled": _coerce_bool(raw.get("whole_doc_enabled"), True),
        "whole_doc_threshold_chars": _coerce_int(
            raw.get("whole_doc_threshold_chars"), 8000,
        ),
        "parent_child_enabled": _coerce_bool(
            raw.get("parent_child_enabled"), False,
        ),
        "vietnamese_preprocessing_enabled": resolve_bot_limit(
            bot_cfg, "vietnamese_preprocessing_enabled",
            system_default=_coerce_bool(
                raw.get("vietnamese_preprocessing_enabled"), True,
            ),
        ),
        "vietnamese_abbreviations": raw.get("vietnamese_abbreviations") or "{}",
        "bm25_use_cover_density": _coerce_bool(
            raw.get("bm25_use_cover_density"), True,
        ),
        "bm25_normalization_flags": _coerce_int(
            raw.get("bm25_normalization_flags"), 5,
        ),
        "max_history": resolve_bot_limit(
            bot_cfg, "max_history",
            system_default=_coerce_int(raw.get("chat_max_history"), DEFAULT_MAX_HISTORY),
        ),
        "max_documents": resolve_bot_limit(
            bot_cfg, "max_documents",
            system_default=_coerce_int(raw.get("rag_max_documents"), 50),
        ),
        "prompt_max_tokens": resolve_bot_limit(
            bot_cfg, "prompt_max_tokens",
            system_default=_coerce_int(raw.get("prompt_max_tokens"), 8000),
        ),
        "diacritic_restoration_enabled": _coerce_bool(
            raw.get("diacritic_restoration_enabled"), False,
        ),
        "diacritic_restoration_use_model": _coerce_bool(
            raw.get("diacritic_restoration_use_model"), False,
        ),
        "autocut_enabled": _coerce_bool(raw.get("autocut_enabled"), False),
        "autocut_min_gap_ratio": _coerce_float(
            raw.get("autocut_min_gap_ratio"), 0.3,
        ),
        "embedding_query_prefix": raw.get("embedding_query_prefix") or "",
        "short_query_word_threshold": _coerce_int(
            raw.get("short_query_word_threshold"), 5,
        ),
        "semantic_cache_ttl_s": _coerce_int(
            raw.get("semantic_cache_ttl_s"), 3600,
        ),
        "crag_skip_retry_above_score": resolve_bot_limit(
            bot_cfg, "crag_skip_retry_above_score",
            system_default=_coerce_float(
                raw.get("crag_skip_retry_above_score"),
                DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
            ),
        ),
        # Multi-query: 5 keys explicit so query_graph never silently
        # falls back to default-True on test endpoint.
        "multi_query_enabled": _coerce_bool(
            raw.get("multi_query_enabled"), DEFAULT_MULTI_QUERY_ENABLED,
        ),
        # Adaptive-RAG auto-mode floor (0.0 = gate inert). Per-bot/system
        # override flows here so the complexity gate is tunable without code.
        "multi_query_complexity_min": _coerce_float(
            raw.get("multi_query_complexity_min"), DEFAULT_MULTI_QUERY_COMPLEXITY_MIN,
        ),
        "multi_query_n_variants": _coerce_int(
            raw.get("multi_query_n_variants"), DEFAULT_MULTI_QUERY_N_VARIANTS,
        ),
        "multi_query_max_variants": _coerce_int(
            raw.get("multi_query_max_variants"), DEFAULT_MULTI_QUERY_MAX_VARIANTS,
        ),
        "multi_query_timeout_s": _coerce_int(
            raw.get("multi_query_timeout_s"), DEFAULT_MULTI_QUERY_TIMEOUT_S,
        ),
        "multi_query_model": raw.get("multi_query_model") or DEFAULT_MULTI_QUERY_MODEL,
        "generation_temperature": _coerce_float(
            raw.get("generation_temperature"), DEFAULT_GENERATION_TEMPERATURE,
        ),
        "default_answer_autonomy_percent": _coerce_int(
            raw.get("default_answer_autonomy_percent"),
            DEFAULT_ANSWER_AUTONOMY_PERCENT,
        ),
        "skip_rewrite_intents": _parse_intent_list(
            raw.get("skip_rewrite_intents", list(DEFAULT_SKIP_REWRITE_INTENTS)),
        ),
        "skip_reflect_intents": _parse_intent_list(
            raw.get("skip_reflect_intents", list(DEFAULT_SKIP_REFLECT_INTENTS)),
        ),
        "mmr_similarity_threshold": _coerce_float(
            raw.get("mmr_similarity_threshold"), 0.88,
        ),
        "mmr_lambda": _coerce_float(raw.get("mmr_lambda"), 0.7),
        "refuse_short_circuit_enabled": _coerce_bool(
            raw.get("refuse_short_circuit_enabled"),
            DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
        ),
        "pipeline_timeout_s": _coerce_int(raw.get("pipeline_timeout_s"), 30),
        "callback_max_retries": _coerce_int(raw.get("callback_max_retries"), 3),
        "callback_timeout_s": _coerce_int(raw.get("callback_timeout_s"), 10),
        "callback_verify_ssl": _coerce_bool(raw.get("callback_verify_ssl"), True),
        "callback_hmac_secret": raw.get("callback_hmac_secret") or "",
        "batch_step_logging_enabled": _coerce_bool(
            raw.get("batch_step_logging_enabled"),
            DEFAULT_BATCH_STEP_LOGGING_ENABLED,
        ),
        # B3 Self-Query Retrieval — stats-index routing thresholds.
        # None falls through to DEFAULT_RANGE_QUERY_MIN_CONFIDENCE /
        # DEFAULT_STATS_INDEX_LIMIT constants inside query_graph._pcfg.
        "range_query_min_confidence": raw.get("range_query_min_confidence", None),
        "stats_index_limit": raw.get("stats_index_limit", None),
        # Race mode: concurrent stats + vector retrieve (opt-in per-bot).
        "stats_index_race_enabled": raw.get("stats_index_race_enabled", None),
        "stats_race_timeout_s": raw.get("stats_race_timeout_s", None),
        # Layer-1 heuristic intent classify fast-path (T2-CostPerf latency opt).
        # Enabled by default; bot owners can disable per-bot via pipeline_config.
        "heuristic_intent_enabled": _coerce_bool(
            raw.get("heuristic_intent_enabled"), DEFAULT_HEURISTIC_INTENT_ENABLED,
        ),
        # Confidence threshold below which heuristic result falls through to LLM.
        # Default 0.85 from CLAUDE.md hardcoded gate; per-bot override via config.
        "heuristic_intent_confidence_threshold": _coerce_float(
            raw.get("heuristic_intent_confidence_threshold"),
            HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
        ),
        # Per-bot guard_output parallel flag (canonical name). Falls back to
        # pipeline_parallel_output_guards_enabled for backward compat.
        "guard_output_parallel_enabled": _coerce_bool(
            raw.get("guard_output_parallel_enabled"), DEFAULT_GUARD_OUTPUT_PARALLEL_ENABLED,
        ),
        # Pre-retrieval parallel gather (understand + cache-preflight + model-resolve).
        # Default ON — saves ~200ms by overlapping the three independent calls.
        "pipeline_pre_retrieval_parallel_enabled": _coerce_bool(
            raw.get("pipeline_pre_retrieval_parallel_enabled"),
            DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED,
        ),
        # Per-bot knobs formerly READ by _pcfg but populated by NEITHER builder
        # (mirage knobs: read, never configurable). Provided here with the SAME
        # default as the constant so behaviour is unchanged — this only lets a bot
        # owner actually override them via plan_limits (the config-parity guard
        # test_pipeline_cfg_keys_parity pins this).
        "bot_custom_vocabulary": getattr(bot_cfg, "custom_vocabulary", None) or {},
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


__all__ = [
    "_PIPELINE_CFG_KEYS",
    "_coerce_int",
    "_coerce_float",
    "_coerce_bool",
    "_parse_intent_list",
    "_build_pipeline_config",
]
