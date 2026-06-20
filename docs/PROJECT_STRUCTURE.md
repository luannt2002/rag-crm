# Cấu trúc thư mục HIỆN TẠI — ragbot (src/ragbot)

> Kiến trúc Hexagonal/DDD (layered). Sinh tự động 2026-06-19.
> Dùng để đối chiếu với cấu trúc AdapChunk của technical support (~/Documents/CĐ1/adapchunk/).

```
ragbot/
├── application/  
│   ├── commands/  
│   │   ├── __init__.py
│   │   ├── chat_commands.py
│   │   └── document_commands.py
│   ├── dto/  
│   │   ├── __init__.py
│   │   ├── ai_specs.py
│   │   ├── block.py
│   │   ├── bot_config.py
│   │   ├── chat_dto.py
│   │   ├── chat_payload.py
│   │   ├── document_dto.py
│   │   ├── llm_schemas.py
│   │   ├── model_runtime.py
│   │   └── notify_channel.py
│   ├── events/  
│   │   ├── __init__.py
│   │   └── chat_completed.py
│   ├── ports/  <- Port interfaces (Hexagonal)
│   │   ├── __init__.py
│   │   ├── ai_config_port.py
│   │   ├── audit_logger_port.py
│   │   ├── bus_port.py
│   │   ├── cache_port.py
│   │   ├── cag_port.py
│   │   ├── chunk_quality_port.py
│   │   ├── circuit_breaker_port.py
│   │   ├── conversation_state_port.py
│   │   ├── convo_summary_port.py
│   │   ├── crag_grader_port.py
│   │   ├── doc_profile_port.py
│   │   ├── document_parser_port.py
│   │   ├── embedder_port.py
│   │   ├── embedding_port.py
│   │   ├── embedding_text_port.py
│   │   ├── entity_extractor_port.py
│   │   ├── guardrail_port.py
│   │   ├── hyde_port.py
│   │   ├── language_pack_port.py
│   │   ├── language_pack_repository_port.py
│   │   ├── lexical_retrieval_port.py
│   │   ├── llm_port.py
│   │   ├── metadata_filter_port.py
│   │   ├── metrics_port.py
│   │   ├── multi_vector_embed_port.py
│   │   ├── narrate_port.py
│   │   ├── notify_channel_port.py
│   │   ├── ocr_port.py
│   │   ├── outbox_port.py
│   │   ├── pii_port.py
│   │   ├── pii_redactor_port.py
│   │   ├── proposition_decomposer_port.py
│   │   ├── proximity_cache_port.py
│   │   ├── query_router_port.py
│   │   ├── rate_limiter_port.py
│   │   ├── repository_ports.py
│   │   ├── reranker_port.py
│   │   ├── reranker_resolver_port.py
│   │   ├── response_mode_port.py
│   │   ├── retrieval_fallback_port.py
│   │   ├── sanitizer_port.py
│   │   ├── secrets_port.py
│   │   ├── self_rag_router_port.py
│   │   ├── sentence_similarity_port.py
│   │   ├── source_validator_port.py
│   │   ├── strategy_ports.py
│   │   ├── tenant_model_tier_port.py
│   │   ├── text_normalizer_port.py
│   │   ├── token_ledger_port.py
│   │   ├── tokenizer_port.py
│   │   ├── tool_client_port.py
│   │   └── vector_store_port.py
│   ├── queries/  
│   │   ├── __init__.py
│   │   └── chat_queries.py
│   ├── services/  <- pipeline (ingest) + narrator + business services
│   │   ├── crag_grader/  
│   │   │   ├── __init__.py
│   │   │   ├── batch_grader.py
│   │   │   ├── null_grader.py
│   │   │   ├── per_chunk_grader.py
│   │   │   └── registry.py
│   │   ├── document_service/  <- pipeline ingest U1-U7
│   │   │   ├── __init__.py
│   │   │   ├── ingest_core.py
│   │   │   ├── ingest_helpers.py
│   │   │   ├── ingest_phases.py
│   │   │   ├── ingest_stages.py
│   │   │   ├── ingest_stages_enrich.py
│   │   │   ├── ingest_stages_final.py
│   │   │   ├── ingest_stages_store.py
│   │   │   └── text_processing.py
│   │   ├── model_resolver/  
│   │   │   ├── __init__.py
│   │   │   └── _helpers.py
│   │   ├── multi_agent_review/  
│   │   │   ├── agents/  
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auditor_agent.py
│   │   │   │   └── specialist_agent.py
│   │   │   ├── __init__.py
│   │   │   ├── agent_port.py
│   │   │   ├── litellm_adapter.py
│   │   │   ├── orchestrator.py
│   │   │   ├── parser.py
│   │   │   ├── prompts.py
│   │   │   └── registry.py
│   │   ├── narrate/  
│   │   │   ├── __init__.py
│   │   │   ├── formula_narrator.py
│   │   │   └── table_narrator.py
│   │   ├── __init__.py
│   │   ├── action_config_validator.py
│   │   ├── adaptive_rerank_weight.py
│   │   ├── ai_config_service.py
│   │   ├── audit_log_hasher.py
│   │   ├── audit_verifier.py
│   │   ├── boilerplate_resolver.py
│   │   ├── bot_lifecycle_service.py
│   │   ├── bot_management_service.py
│   │   ├── bot_registry_service.py
│   │   ├── cag_service.py
│   │   ├── chunk_context_enricher.py
│   │   ├── citation_policy.py
│   │   ├── content_type_router.py
│   │   ├── contextual_chunk_enrichment.py
│   │   ├── corpus_version_service.py
│   │   ├── cost_cap_alerter.py
│   │   ├── crm_analytics_service.py
│   │   ├── error_notify_hook.py
│   │   ├── faq_candidate_service.py
│   │   ├── google_link_service.py
│   │   ├── google_sheets_test_fetcher.py
│   │   ├── guardrail_rule_loader.py
│   │   ├── hallu_verifier.py
│   │   ├── heuristic_intent_classifier.py
│   │   ├── hyde_generator.py
│   │   ├── idempotency.py
│   │   ├── ingest_idempotency_service.py
│   │   ├── ingest_quota_service.py
│   │   ├── jwt_token_service.py
│   │   ├── language_pack_service.py
│   │   ├── multi_query_expansion.py
│   │   ├── narrate_dispatch.py
│   │   ├── narrate_service.py
│   │   ├── notify_channel_resolver.py
│   │   ├── oos_template_resolver.py
│   │   ├── parsed_md_dump.py
│   │   ├── persona_quality_gate.py
│   │   ├── provider_key_resolver.py
│   │   ├── query_intent_extractor.py
│   │   ├── ragas_metric_adapter.py
│   │   ├── reranker_resolver.py
│   │   ├── retry_policy.py
│   │   ├── slot_extractor.py
│   │   ├── step_tracker.py
│   │   ├── structured_output_helper.py
│   │   ├── structured_ref_extractor.py
│   │   ├── superlative_context_enricher.py
│   │   ├── sysprompt_assembler.py
│   │   ├── system_config_service.py
│   │   ├── tenant_analytics_service.py
│   │   ├── tenant_config_cache.py
│   │   ├── tenant_guard.py
│   │   ├── tenant_rate_limiter.py
│   │   ├── tenant_token_meter.py
│   │   ├── token_budget.py
│   │   ├── vocabulary_expander.py
│   │   └── webhook_secret_rotation.py
│   ├── use_cases/  
│   │   ├── __init__.py
│   │   ├── answer_question.py
│   │   ├── delete_document.py
│   │   ├── get_job_status.py
│   │   ├── give_feedback.py
│   │   ├── ingest_document.py
│   │   └── rechunk_document.py
│   └── __init__.py
├── config/  
│   ├── __init__.py
│   ├── logging.py
│   └── settings.py
├── domain/  <- entities/value-objects (DDD)
│   ├── entities/  
│   │   ├── __init__.py
│   │   ├── citation.py
│   │   ├── conversation.py
│   │   ├── document.py
│   │   ├── document_profile.py
│   │   └── message.py
│   ├── events/  
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── bot_events.py
│   │   ├── chat_events.py
│   │   └── document_events.py
│   ├── value_objects/  
│   │   ├── __init__.py
│   │   ├── idempotency_key.py
│   │   ├── structural_path.py
│   │   ├── tenant_scope.py
│   │   └── versioning.py
│   └── __init__.py
├── evaluation/  
│   ├── __init__.py
│   └── ragas_metrics.py
├── infrastructure/  
│   ├── cache/  
│   │   ├── __init__.py
│   │   ├── embed_cache.py
│   │   ├── redis_cache.py
│   │   ├── semantic_cache.py
│   │   └── understand_query_cache.py
│   ├── cag/  
│   │   ├── __init__.py
│   │   ├── anthropic_cag.py
│   │   ├── null_cag.py
│   │   └── registry.py
│   ├── chat_hooks/  
│   │   ├── __init__.py
│   │   ├── quota_threshold_notify_hook.py
│   │   ├── token_usage_db_hook.py
│   │   └── token_usage_redis_hook.py
│   ├── chunk_quality/  
│   │   ├── __init__.py
│   │   ├── heuristic_chunk_quality_scorer.py
│   │   ├── null_chunk_quality_scorer.py
│   │   └── registry.py
│   ├── conversation_state/  
│   │   ├── __init__.py
│   │   ├── jsonb_conversation_state.py
│   │   ├── null_conversation_state.py
│   │   └── registry.py
│   ├── convo_summary/  
│   │   ├── __init__.py
│   │   ├── llm_convo_summary.py
│   │   ├── null_convo_summary.py
│   │   └── registry.py
│   ├── db/  
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── message_feedback_model.py
│   │   ├── models.py
│   │   ├── models_guardrail.py
│   │   ├── models_invocation.py
│   │   ├── models_monitoring.py
│   │   ├── refuse_suggestion_model.py
│   │   ├── session.py
│   │   └── uow.py
│   ├── delivery/  
│   │   ├── __init__.py
│   │   ├── callback_delivery.py
│   │   └── noop_delivery.py
│   ├── doc_profile/  
│   │   ├── __init__.py
│   │   ├── null_doc_profile.py
│   │   ├── registry.py
│   │   └── rule_based_doc_profile.py
│   ├── embedding/  <- embedder (Jina)
│   │   ├── __init__.py
│   │   ├── bkai_vn_embedder.py
│   │   ├── jina_embedder.py
│   │   ├── litellm_embedder.py
│   │   ├── multi_vector_registry.py
│   │   ├── null_embedder.py
│   │   ├── null_multi_vector.py
│   │   ├── openai_embedder.py
│   │   ├── registry.py
│   │   ├── sentence_split_multi_vector.py
│   │   └── zeroentropy_embedder.py
│   ├── embedding_text/  
│   │   ├── __init__.py
│   │   ├── null_embedding_text_strategy.py
│   │   ├── prefix_plus_raw_strategy.py
│   │   ├── raw_only_strategy.py
│   │   └── registry.py
│   ├── entity_extractor/  
│   │   ├── __init__.py
│   │   ├── en_simple_extractor.py
│   │   ├── null_extractor.py
│   │   ├── registry.py
│   │   └── vi_underthesea_extractor.py
│   ├── events/  
│   │   ├── __init__.py
│   │   └── redis_streams_bus.py
│   ├── graph/  
│   │   ├── __init__.py
│   │   ├── graph_retriever.py
│   │   └── knowledge_graph.py
│   ├── guardrails/  
│   │   ├── __init__.py
│   │   ├── _default_patterns.py
│   │   ├── local_guardrail.py
│   │   ├── math_lockdown.py
│   │   ├── null_guardrail.py
│   │   └── registry.py
│   ├── hyde/  
│   │   ├── __init__.py
│   │   ├── llm_hyde.py
│   │   ├── null_hyde.py
│   │   └── registry.py
│   ├── idempotency/  
│   │   └── __init__.py
│   ├── llm/  
│   │   ├── __init__.py
│   │   ├── anthropic_haiku_batch.py
│   │   ├── dynamic_litellm_router.py
│   │   ├── llm_chunk_context_provider.py
│   │   ├── registry.py
│   │   ├── speculative_router.py
│   │   └── tpm_rate_limiter.py
│   ├── metadata_filter/  
│   │   ├── __init__.py
│   │   ├── article_aware_filter.py
│   │   ├── generic_llm_extractor.py
│   │   ├── llm_metadata_cache.py
│   │   ├── null_filter.py
│   │   └── registry.py
│   ├── narrate/  
│   │   ├── __init__.py
│   │   ├── llm_narrate.py
│   │   ├── null_narrate.py
│   │   └── registry.py
│   ├── notify/  
│   │   ├── __init__.py
│   │   ├── webhook_dispatcher.py
│   │   └── webhook_notifier.py
│   ├── observability/  
│   │   ├── __init__.py
│   │   ├── invocation_logger.py
│   │   ├── metrics.py
│   │   ├── null_audit_logger.py
│   │   ├── p99_outlier.py
│   │   ├── pipeline_audit_logger.py
│   │   ├── prometheus_metrics_adapter.py
│   │   ├── sla_metrics.py
│   │   ├── tracing.py
│   │   └── warmup.py
│   ├── ocr/  
│   │   ├── __init__.py
│   │   ├── docling_parser.py
│   │   ├── kreuzberg_parser.py
│   │   ├── ocr_factory.py
│   │   └── simple_text_parser.py
│   ├── parser/  <- ocr_client (parse PDF/Excel/Sheets/MD)
│   │   ├── __init__.py
│   │   ├── docx_parser.py
│   │   ├── excel_openpyxl_parser.py
│   │   ├── google_sheets_parser.py
│   │   ├── markdown_parser.py
│   │   ├── null_parser.py
│   │   ├── pdf_parser.py
│   │   └── registry.py
│   ├── pii/  
│   │   ├── __init__.py
│   │   ├── null_pii_redactor.py
│   │   ├── presidio_pii_redactor.py
│   │   ├── regex_pii_redactor.py
│   │   ├── registry.py
│   │   └── vn_regex_pii_redactor.py
│   ├── proximity_cache/  
│   │   ├── __init__.py
│   │   ├── lsh_proximity_cache.py
│   │   ├── null_proximity_cache.py
│   │   └── registry.py
│   ├── query_router/  
│   │   ├── __init__.py
│   │   ├── llm_query_router.py
│   │   ├── null_query_router.py
│   │   ├── regex_query_router.py
│   │   └── registry.py
│   ├── rate_limiter/  
│   │   ├── __init__.py
│   │   ├── in_memory.py
│   │   ├── registry.py
│   │   └── sliding_window.py
│   ├── repositories/  
│   │   ├── __init__.py
│   │   ├── _base.py
│   │   ├── ai_config_repository.py
│   │   ├── audit_chain_writer.py
│   │   ├── audit_repository.py
│   │   ├── bot_repository.py
│   │   ├── conversation_repository.py
│   │   ├── document_repository.py
│   │   ├── guardrail_repository.py
│   │   ├── job_repository.py
│   │   ├── language_pack_repository.py
│   │   ├── message_feedback_repository.py
│   │   ├── message_repository.py
│   │   ├── outbox_repository.py
│   │   ├── quota_repository.py
│   │   ├── request_log_repository.py
│   │   ├── stats_index_repository.py
│   │   ├── tenant_policy_repository.py
│   │   ├── tenant_repository.py
│   │   ├── token_ledger_analytics_repository.py
│   │   └── workspace_repository.py
│   ├── reranker/  <- reranker (Jina)
│   │   ├── __init__.py
│   │   ├── _modality_boost.py
│   │   ├── jina_reranker.py
│   │   ├── litellm_reranker.py
│   │   ├── null_reranker.py
│   │   ├── registry.py
│   │   ├── viranker_local_reranker.py
│   │   ├── voyage_reranker.py
│   │   └── zeroentropy_reranker.py
│   ├── resilience/  
│   │   ├── __init__.py
│   │   ├── _base.py
│   │   ├── db_circuit_breaker.py
│   │   ├── failover_orchestrator.py
│   │   ├── llm_circuit_breaker.py
│   │   ├── null_circuit_breaker.py
│   │   ├── redis_circuit_breaker.py
│   │   └── registry.py
│   ├── retrieval/  <- retriever (BM25/hybrid helpers)
│   │   ├── __init__.py
│   │   ├── lexical_registry.py
│   │   ├── null_lexical_retrieval.py
│   │   └── pg_bm25_retrieval.py
│   ├── retrieval_fallback/  
│   │   ├── __init__.py
│   │   ├── bm25_only_stage2.py
│   │   ├── hybrid_stage1.py
│   │   ├── keyword_stage3.py
│   │   ├── null_stage.py
│   │   ├── parent_expand_stage4.py
│   │   └── registry.py
│   ├── safety/  
│   │   ├── __init__.py
│   │   ├── domain_allowlist_validator.py
│   │   ├── null_sanitizer.py
│   │   ├── null_source_validator.py
│   │   ├── pii_detector.py
│   │   ├── registry.py
│   │   ├── sanitizer.py
│   │   └── vn_recognizers.py
│   ├── security/  
│   │   ├── __init__.py
│   │   ├── env_secrets.py
│   │   ├── hmac_signer.py
│   │   └── jwt_auth.py
│   ├── self_rag_router/  
│   │   ├── __init__.py
│   │   ├── intent_based_self_rag_router.py
│   │   ├── null_self_rag_router.py
│   │   └── registry.py
│   ├── sentence_similarity/  
│   │   ├── __init__.py
│   │   ├── embedding_sentence_similarity.py
│   │   ├── null_sentence_similarity.py
│   │   └── registry.py
│   ├── tenant_model_tier/  
│   │   ├── __init__.py
│   │   ├── null_tenant_model_tier.py
│   │   ├── registry.py
│   │   └── static_tenant_model_tier.py
│   ├── text_normalizer/  
│   │   ├── __init__.py
│   │   ├── bartpho_accent_normalizer.py
│   │   ├── null_normalizer.py
│   │   └── registry.py
│   ├── token_ledger/  
│   │   ├── __init__.py
│   │   ├── async_db_token_ledger.py
│   │   ├── aux_usage.py
│   │   ├── null_token_ledger.py
│   │   └── registry.py
│   ├── tokenizer/  
│   │   ├── __init__.py
│   │   ├── null_tokenizer.py
│   │   ├── registry.py
│   │   ├── simple_tokenizer.py
│   │   └── vi_tokenizer.py
│   ├── tools/  
│   │   ├── __init__.py
│   │   ├── mcp_tool_client.py
│   │   ├── null_tool_client.py
│   │   └── registry.py
│   ├── vector/  <- vector_store (pgvector)
│   │   ├── __init__.py
│   │   ├── null_vector_store.py
│   │   ├── pgvector_store.py
│   │   └── registry.py
│   └── __init__.py
├── interfaces/  
│   ├── http/  
│   │   ├── middlewares/  
│   │   │   ├── __init__.py
│   │   │   ├── anti_abuse.py
│   │   │   ├── body_size.py
│   │   │   ├── bot_rate_limit.py
│   │   │   ├── cors_per_tenant.py
│   │   │   ├── ip_rate_limit.py
│   │   │   ├── loadtest_bypass.py
│   │   │   ├── logging_mw.py
│   │   │   ├── rate_limit.py
│   │   │   ├── rbac.py
│   │   │   ├── schema_version.py
│   │   │   ├── security_headers.py
│   │   │   ├── source_rate_limit.py
│   │   │   ├── tenant_context.py
│   │   │   └── trace_context.py
│   │   ├── routes/  
│   │   │   ├── test_chat/  
│   │   │   │   ├── __init__.py
│   │   │   │   ├── _pipeline_config.py
│   │   │   │   ├── _shared.py
│   │   │   │   ├── admin_routes.py
│   │   │   │   ├── bot_admin_routes.py
│   │   │   │   ├── bot_insights_routes.py
│   │   │   │   ├── chat_routes.py
│   │   │   │   ├── document_routes.py
│   │   │   │   ├── monitoring_routes.py
│   │   │   │   ├── pages.py
│   │   │   │   ├── schemas.py
│   │   │   │   └── token_routes.py
│   │   │   ├── __init__.py
│   │   │   ├── _action_conversation.py
│   │   │   ├── admin_ai.py
│   │   │   ├── admin_analytics.py
│   │   │   ├── admin_audit.py
│   │   │   ├── admin_bots.py
│   │   │   ├── admin_documents_debug.py
│   │   │   ├── admin_gdpr.py
│   │   │   ├── admin_metrics.py
│   │   │   ├── admin_notify.py
│   │   │   ├── admin_policy.py
│   │   │   ├── admin_rate_limits.py
│   │   │   ├── admin_refuse_suggestions.py
│   │   │   ├── admin_tenant_policy.py
│   │   │   ├── admin_tenants.py
│   │   │   ├── admin_webhooks.py
│   │   │   ├── chat.py
│   │   │   ├── chat_async.py
│   │   │   ├── chat_stream.py
│   │   │   ├── crm.py
│   │   │   ├── documents.py
│   │   │   ├── documents_stream_upload.py
│   │   │   ├── feedback.py
│   │   │   ├── health.py
│   │   │   ├── health_models.py
│   │   │   ├── honeypot.py
│   │   │   ├── jobs.py
│   │   │   └── sync.py
│   │   ├── schemas/  
│   │   │   ├── __init__.py
│   │   │   ├── admin_ai_schemas.py
│   │   │   ├── admin_tenant_policy_schema.py
│   │   │   ├── admin_tenants.py
│   │   │   ├── chat_schema.py
│   │   │   ├── common_schema.py
│   │   │   └── document_schema.py
│   │   ├── __init__.py
│   │   ├── _ingest_quota_guard.py
│   │   ├── _resource_ownership.py
│   │   ├── _sse_helper.py
│   │   ├── app.py
│   │   ├── embedded_workers.py
│   │   ├── errors.py
│   │   └── router.py
│   ├── workers/  
│   │   ├── chat_worker/  
│   │   │   ├── __init__.py
│   │   │   ├── callbacks.py
│   │   │   ├── config.py
│   │   │   ├── payload.py
│   │   │   ├── pipeline.py
│   │   │   └── pipeline_config.py
│   │   ├── __init__.py
│   │   ├── ai_config_listener.py
│   │   ├── document_recovery_worker.py
│   │   ├── document_worker.py
│   │   └── outbox_publisher.py
│   └── __init__.py
├── orchestration/  <- rag_chain (LangGraph query graph) + nodes
│   ├── nodes/  <- retriever / answer_generator / grade / rerank... (mỗi node 1 file)
│   │   ├── __init__.py
│   │   ├── adaptive_decompose.py
│   │   ├── cascade_router_helper.py
│   │   ├── check_cache.py
│   │   ├── condense_question.py
│   │   ├── critique_parser.py
│   │   ├── decompose.py
│   │   ├── generate.py
│   │   ├── grade.py
│   │   ├── graph_retrieve.py
│   │   ├── guard_input.py
│   │   ├── guard_output.py
│   │   ├── mmr_dedup.py
│   │   ├── neighbor_expand.py
│   │   ├── persist.py
│   │   ├── query_complexity.py
│   │   ├── query_complexity_node.py
│   │   ├── query_decomposer.py
│   │   ├── reflect.py
│   │   ├── rerank.py
│   │   ├── retrieve.py
│   │   ├── rewrite.py
│   │   ├── rewrite_retry.py
│   │   ├── router.py
│   │   ├── routing.py
│   │   ├── rrf_round_robin.py
│   │   ├── speculative_retrieve.py
│   │   └── understand.py
│   ├── system_prompts/  
│   │   ├── __init__.py
│   │   └── context_aware_refusal_template.py
│   ├── __init__.py
│   ├── graph_assembly.py
│   ├── query_graph.py
│   ├── query_graph_helpers.py
│   ├── retrieval_filter.py
│   └── state.py
├── shared/  
│   ├── chunking/  <- chunking strategies + analyze (= adapchunk/chunking + feature_extractor/strategy_selector/cross_checker)
│   │   ├── __init__.py
│   │   ├── analyze.py
│   │   ├── blocks.py
│   │   ├── csv_chunker.py
│   │   ├── strategies.py
│   │   └── vn_structural.py
│   ├── constants/  <- config (defaults) [+ system_config DB]
│   │   ├── _00_app_env_taxonomy.py
│   │   ├── _01_http_db_client_construction_.py
│   │   ├── _02_per_intent_rerank_skip_gate_.py
│   │   ├── _03_language_packs_db_driven_pro.py
│   │   ├── _04_jwt_auth.py
│   │   ├── _05_embedding_circuitbreaker.py
│   │   ├── _06_llm_defaults.py
│   │   ├── _07_llm_sampling_defaults.py
│   │   ├── _08_sentry_otel.py
│   │   ├── _09_message_feedback_thumbs_verd.py
│   │   ├── _10_rbac.py
│   │   ├── _11_table_csv_chunking_strategy.py
│   │   ├── _12_multi_stage_retrieval_fallba.py
│   │   ├── _13_adapchunk_layer_1_ocr_parser.py
│   │   ├── _14_anti_abuse_ip_rate_limit_hon.py
│   │   ├── _15_m2_neighbor_window_expansion.py
│   │   ├── _16_prompt_token_squeeze_phase_b.py
│   │   ├── _17_260509_a1_pipeline_audit_6_c.py
│   │   ├── _18_admin_all_tenants_analytics_.py
│   │   ├── _19_sprint3_ekimetrics_selector_.py
│   │   ├── _20_cag_mode_cache_augmented_gen.py
│   │   ├── _21_streaming_upload_wb_2_p1_5.py
│   │   ├── _22_conversation_state_memory.py
│   │   ├── _23_crm_analytics_readlayer_.py
│   │   ├── _24_structural_markers_by_lang.py
│   │   └── __init__.py
│   ├── __init__.py
│   ├── anthropic_cache.py
│   ├── api_key_pool.py
│   ├── auto_merge_retrieval.py
│   ├── autonomy_resolver.py
│   ├── bootstrap_config.py
│   ├── bot_bindings.py
│   ├── bot_limits.py
│   ├── callback_validator.py
│   ├── chunk_identity.py
│   ├── chunk_quality.py
│   ├── chunking_policy.py
│   ├── clock.py
│   ├── complexity_sizing.py
│   ├── context_buffer.py
│   ├── context_utils.py
│   ├── contextual_enrichment.py
│   ├── dedup.py
│   ├── diff_reingest.py
│   ├── document_stats.py
│   ├── embedding_cache.py
│   ├── errors.py
│   ├── hashing.py
│   ├── hmac_signing.py
│   ├── i18n.py
│   ├── ingestion_validator.py
│   ├── intrinsic_metrics.py
│   ├── json_io.py
│   ├── json_parse.py
│   ├── late_chunking.py
│   ├── llm_usage.py
│   ├── markdown_normalizer.py
│   ├── mime_sniff.py
│   ├── mmr.py
│   ├── number_format.py
│   ├── pagination.py
│   ├── perf.py
│   ├── pii_universal.py
│   ├── prompt_compression.py
│   ├── prompt_injection_guard.py
│   ├── prompt_token_opt.py
│   ├── proposition_llm.py
│   ├── query_range_parser.py
│   ├── rate_limit_policy.py
│   ├── rbac.py
│   ├── result.py
│   ├── sentence_similarity.py
│   ├── single_flight.py
│   ├── text_normalization.py
│   ├── text_utils.py
│   ├── token_budget.py
│   ├── types.py
│   ├── vi_tokenizer.py
│   ├── vn_honorific.py
│   └── workspace_id_validator.py
├── __init__.py
├── bootstrap.py
└── main.py
```

**Thống kê:** 79 thư mục, 632 file .py (Hexagonal/DDD — trải nhiều layer, khác AdapChunk flat của support).
