"""Pin tests for ``bootstrap_config._ALLOWED_KEYS`` — keys that DI factories
in ``bootstrap.py`` read at container-boot via ``get_boot_config``.

LEGAL-RETRIEVAL-FIX bug #1 (8fae3b4 — 2026-05-12) shipped a new
DI factory key (``lexical_retrieval_provider``) without adding it to the
``_ALLOWED_KEYS`` whitelist. ``get_boot_config`` whitelists keys defensively
(SQL-injection-via-config-key guard); a missing whitelist entry causes a
``bootstrap_config_key_not_allowlisted`` warn-log + silent fallback to the
caller-supplied default. Operator UPDATE of system_config to enable the
feature looks fine in DB but never reaches the factory.

This test makes that failure mode loud — the offending feature ship can
no longer land on main without also flipping the whitelist.
"""

from __future__ import annotations

from ragbot.shared.bootstrap_config import _ALLOWED_KEYS


# Keys consumed by the DI factories in ``bootstrap.py`` (and other
# infrastructure modules that depend on container-boot config). Each
# entry corresponds to a row in ``system_config`` an operator can flip.
# Adding a new factory key here without adding it to the production
# whitelist breaks this test — that is the point.
_FACTORY_KEYS_REQUIRED: frozenset[str] = frozenset(
    {
        # Wave G / V — embedder + reranker registry
        "embedding_provider",
        "reranker_provider",
        "reranker_model",
        # WA-3 / CT-3 — Anthropic contextual retrieval
        "cr_enhanced_enabled",
        "structured_ref_extraction_enabled",
        # Wave J3 — LM Studio swap pilot (CRAG grader)
        "crag_grader_provider",
        # Cluster C2 — article-aware metadata filter
        "metadata_filter_provider",
        "article_ref_patterns",
        # Multi-query entity extractor
        "entity_extractor_provider",
        # Wave S7 — Hybrid BM25 + Vector (this was the bug-#1 origin)
        "lexical_retrieval_provider",
        # Vector store registry (defensive — current default ``"pgvector"``
        # happens to match the seeded value so the silent fallback was a
        # latent bug rather than an active one; still must be allow-listed
        # so an operator-driven swap to e.g. ``"qdrant"`` actually lands).
        "vector_store_provider",
        # 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 1 — mixed-CSV synthetic
        # header/footer chunks. Flag default OFF; flip via system_config.
        "table_csv_emit_header_footer_chunks_enabled",
        # 260525 whitelist-complete — 9 keys that ``get_boot_config()`` was
        # reading at runtime but the whitelist gate silently dropped to
        # caller-side defaults. AdapChunk Layer 5 cross-check (7 keys), the
        # atomic-block protect master switch (1), and the understand_query
        # cache TTL (1).
        "adapchunk_layer5_cross_check_enabled",
        "adapchunk_l5_confidence_threshold",
        "adapchunk_l5_hdt_min_headings",
        "adapchunk_l5_semantic_min_avg_block_len",
        "adapchunk_l5_proposition_max_avg_block_len",
        "adapchunk_l5_proposition_max_headings",
        "adapchunk_l5_mixed_content_warn_threshold",
        "formula_image_atomic_protect_enabled",
        "understand_query.cache_ttl_s",
        # 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 3 — per-intent
        # rerank top_n + context-cap dicts. JSONB value per intent name.
        "rerank_top_n_by_intent",
        "generate_context_chars_cap_by_intent",
        # 260525 Bug #10 — per-intent MMR similarity threshold.
        "mmr_similarity_threshold_by_intent",
        # 260525 Bug #7c — bulk closure 78 _pcfg keys reachable from
        # ``query_graph._pcfg`` that the whitelist previously dropped.
        "adaptive_router_l1_enabled",
        "bm25_substring_fallback_enabled",
        "crag_grade_concurrency",
        "crag_lenient_grade_for_compound_intents_enabled",
        "crag_min_relevant_count",
        "crag_min_relevant_fraction",
        "decompose_confidence_gate",
        "decompose_enabled",
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
    },
)


def test_all_di_factory_keys_are_allowlisted() -> None:
    """Every key a DI factory reads at boot must be on ``_ALLOWED_KEYS``.

    A miss silences the lookup and degrades the factory to its
    code-side default — see file-level docstring for the bug pattern.
    """
    missing = _FACTORY_KEYS_REQUIRED - _ALLOWED_KEYS
    assert not missing, (
        f"DI factory key(s) missing from ``_ALLOWED_KEYS`` whitelist: "
        f"{sorted(missing)!r}. ``get_boot_config`` silently falls back "
        f"to the caller's default for un-allowlisted keys, so operator "
        f"UPDATE on the matching ``system_config`` rows would no-op. "
        f"Add the key(s) to ``bootstrap_config._ALLOWED_KEYS`` and ship."
    )
