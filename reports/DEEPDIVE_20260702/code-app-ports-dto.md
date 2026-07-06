# DEEPDIVE — application/ports + application/dto + application non-services files

- **Scope**: every file under `src/ragbot/application/` EXCEPT `services/` — 44 port files, 8 DTO files, `commands/` (3), `queries/` (2), `events/` (1), `use_cases/` (7), package `__init__` files. 82 files, ~6,060 lines, **all read line-by-line**.
- **Method**: full read → wiring trace to `bootstrap.py` + infrastructure registries + orchestration/worker/route call sites (grep evidence per claim).
- **Evidence discipline (rule #0)**: every claim carries `file:line`. Labels: **FACT** = static code evidence verified in this session; **HYPOTHESIS** = inferred failure scenario not yet reproduced at runtime (no runtime repro was executed in this read-only pass).
- Date: 2026-07-02. Branch: `fix-260623-ingest-expert`.

---

## PART 1 — File inventory: what each file does + pipeline connection

### 1.1 `application/ports/` (44 files)

Wiring legend: **LIVE** = implemented + registered + injected/consumed on a real path; **PARTIAL** = implemented but only some paths get it; **DEAD-REG** = port + impls exist but the registry is fully commented out; **ORPHAN** = no implementation and/or no consumer.

| Port file | Contract (one line) | Impl(s) | Registry | Injected / consumed at | Status |
|---|---|---|---|---|---|
| `ai_config_port.py` | Repo over `ai_providers/ai_models/bot_model_bindings/prompt_templates` + audit; row dataclasses `ProviderRow/ModelRow/BindingRow/PromptTemplateRow/AuditEntry` | SqlAlchemy repo | n/a | `bootstrap.py:487` `ai_config_repo` → `model_resolver` (:532), `llm` router (:569) | **LIVE** |
| `audit_logger_port.py` | `log(bot_id, stage, event, data)` pipeline audit line | `PipelineAuditLogger`, `NullAuditLogger` (`infrastructure/observability/null_audit_logger.py:1`) | n/a | `bootstrap.py:525` → `query_graph.py:850` | **LIVE** |
| `bus_port.py` | Redis-Streams event bus (`publish/subscribe/request`) + orjson (de)serialize staticmethods | RedisStreams impl | n/a | `bootstrap.py:246` `bus` | **LIVE** |
| `cache_port.py` | `CachePort` bytes KV + `SemanticCachePort` + 4 pure key builders; `CachedResponse` | `RedisCache`, `PgSemanticCache` | n/a | `bootstrap.py:234-235`; `check_cache.py`, `persist.py` | **LIVE** (but see F5 port-drift) |
| `cag_port.py` | Cache-Augmented Generation gate (`should_engage`/`build_corpus_payload`) | `null_cag.py`, Anthropic strategy | `infrastructure/cag/registry.py` **entirely commented** (:50-86) | none | **DEAD-REG** |
| `chunk_quality_port.py` | Ingest chunk-quality scorer (4 weighted sub-scores) | `HeuristicChunkQualityScorer` (`shared/chunk_quality.py:218`) | `chunk_quality/registry.py` **commented** (:57-99) | consumed via module singleton `_SCORER_SINGLETON` (`shared/chunk_quality.py:322`) from `ingest_stages_enrich.py:579` | **PARTIAL** (live via hardwired singleton, registry dead) |
| `circuit_breaker_port.py` | Per-resource CB contract over `retry_policy.CBState` | Null/Redis/Db/Llm CBs | `resilience/registry.py` (live) | only inside `resilience/failover_orchestrator.py:109`; no consumer outside package found | **ORPHAN-ish** (self-contained subsystem, no external caller found) |
| `conversation_state_port.py` | Multi-turn slot state load/save + drift detect (returns `GuardrailHit`) | Null + Jsonb | `conversation_state/registry.py` (live) | `bootstrap.py:640` (`provider="jsonb"` literal) | **LIVE** |
| `convo_summary_port.py` | Summarise turns → text (`Turn` dataclass) | Null + LLM | `convo_summary/registry.py` **commented** (:47-77) | none (constants `DEFAULT_CONVO_SUMMARY_*` exist: `_00_app_env_taxonomy.py:132-133`) | **DEAD-REG** |
| `crag_grader_port.py` | `grade_batch(query, chunks)->{chunk_id:score}` strategy | per_chunk / batch / null (in `application/services/crag_grader/`) | live | `bootstrap.py:435` `crag_grader_factory` | **LIVE** |
| `doc_profile_port.py` | `analyze(text)->DocumentProfile` (AdapChunk L3) | Null + RuleBased | `doc_profile/registry.py` (live) | `ingest_stages.py:704` (built inline per-call, not via container) | **LIVE** |
| `document_parser_port.py` | `supports(mime,ext)` + `parse(bytes)->[{content,metadata}]` | many parsers | `parser/registry.py` (live, `detect_parser`) | `document_worker.py:208`; `DocumentService._parser_detector` (`document_service/__init__.py:241`) | **LIVE** |
| `embedder_port.py` | Minimal embedder (`embed_query/embed_documents/dimension/model_id`) for failover/similarity | `openai_embedder.py`, `null_embedder.py`, sentence-similarity adapter | n/a | consumed by `sentence_similarity` + multi-vector code | **PARTIAL** (its main consumers are themselves dead — see F4) |
| `embedding_port.py` | Full spec-driven embedding (`embed_batch/embed_one` + `EmbeddingSpec` + tenant) | LiteLLM embedder | `embedding/registry.py:66` `build_embedder` (live) | `bootstrap.py:308` `embedder` → query graph + DocumentService | **LIVE** |
| `embedding_text_port.py` | Build the string fed to embedder (prefix+raw vs raw-only) | Prefix/RawOnly/Null | `embedding_text/registry.py` (live) | `document_service` ingest (`ingest_helpers.py:121` etc.) | **LIVE** |
| `entity_extractor_port.py` | Query-side NER for BM25 variants, `extract(query, language)` | Null, vi_underthesea, en_simple | live | `bootstrap.py:400` | **LIVE** |
| `guardrail_port.py` | `GuardrailHit`/`GuardrailBlocked` + `check_input`/`check_output` (+ legacy moderation) | Local + Null guardrail | `guardrails/registry.py` (live) | `bootstrap.py:359`; `guard_output.py:387,518` | **LIVE** |
| `hyde_port.py` | `generate(query)->hypothetical` | Null + LLM | `hyde/registry.py` **commented** (:46-76) | port unused; concrete `HyDEGenerator` class (`application/services/hyde_generator.py:67`) injected directly at `bootstrap.py:584` | **DEAD-REG** (feature live but Port bypassed) |
| `language_pack_port.py` | `get(language,key)` / `get_pack(language)` | `LanguagePackService` | n/a | `bootstrap.py:609` | **LIVE** |
| `language_pack_repository_port.py` | read-only `language_packs` repo | SqlAlchemy repo | n/a | `bootstrap.py:518` | **LIVE** |
| `lexical_retrieval_port.py` | BM25/sparse `search(query, record_bot_id, top_k, cr_enhanced)` | pg_bm25, ES(?), null | `retrieval/lexical_registry.py` (live) | `bootstrap.py:278` | **LIVE** |
| `llm_port.py` | `LLMMessage/LLMResponse` + `complete/stream/refresh_routing` | `DynamicLiteLLMRouter` | n/a | `bootstrap.py:569` | **LIVE** |
| `metadata_filter_port.py` | `extract(query)->JSONB containment dict` pre-filter | Null + ArticleAware | live | `bootstrap.py:416` | **LIVE** |
| `metrics_port.py` | `observe_step_duration` + `inc_rate_limit_bypass` | `PrometheusMetricsAdapter` | n/a | `bootstrap.py:546` | **LIVE** |
| `multi_vector_embed_port.py` | ColBERT-style per-role vectors | `null_multi_vector.py`, `sentence_split_multi_vector.py` | `embedding/multi_vector_registry.py:64` **commented** | none | **DEAD-REG** |
| `narrate_port.py` | Narrate TABLE/FORMULA/IMAGE before embed | Null + LLM | `narrate/registry.py` (live) | `document_worker.py:561`, `sync.py:483` | **LIVE** |
| `notify_channel_port.py` | `send_quota_exhausted(...)` webhook | WebhookNotifier + Null | n/a | `bootstrap.py:743` + hook at :770 | **LIVE** |
| `ocr_port.py` | `parse(bytes)->ParsedDocument(blocks)` | Docling, SimpleText | `ocr/ocr_factory.py:47` | `bootstrap.py:322`; `document_worker.py:494` | **LIVE** |
| `outbox_port.py` | outbox repo + publisher (exactly-once session helpers) | SqlAlchemy repo + publisher | n/a | `bootstrap.py:484` | **LIVE** |
| `pii_port.py` (async, language-aware) | `redact(text, language)->spans by type` | only legacy `regex_pii_redactor.py:11` (itself NOT in the live registry) | n/a | **no consumer**; still exported from `ports/__init__.py:23` | **ORPHAN** |
| `pii_redactor_port.py` (sync strategy) | `redact(text)->(masked, entities)` | Null / VnRegex / Presidio-STUB | `pii/registry.py` (live) | `bootstrap.py:447`; chat `payload.py:65`, ingest helpers | **LIVE** (but provider frozen to `"null"` — see F3) |
| `proposition_decomposer_port.py` | Dense-X decompose text→propositions | Null + LLM (in ingest chunking) | (consumed inside `_chunk_proposition` path) | flag-gated ingest path | **LIVE-flagged** |
| `proximity_cache_port.py` | LSH semantic short-circuit `lookup/store` | Null + LSH | `proximity_cache/registry.py` **commented** (:50-85) | none | **DEAD-REG** |
| `query_router_port.py` | pre-retrieve coarse intent (`QueryIntent` Literal mirror of `QUERY_INTENT_TYPES`) | Null + strategies | `query_router/registry.py` **commented** (:51-81) | none | **DEAD-REG** |
| `rate_limiter_port.py` | sliding-window `check(key, limit, window)` → `RateLimiterDecision` | redis_sliding + in_memory | `rate_limiter/registry.py` (live) | `bootstrap.py:562` (provider literal `"redis_sliding"`) | **LIVE** |
| `repository_ports.py` | UoW + Conversation/Document/Bot/Job/Quota repos | SqlAlchemy repos | n/a | `bootstrap.py:462-475` | **LIVE** |
| `reranker_port.py` | `rerank(query, chunks, top_n, model)` | Null/Jina/LiteLLM/ViRanker/ZeroEntropy | `reranker/registry.py` (live) | `bootstrap.py:380` + resolver :709 | **LIVE** |
| `reranker_resolver_port.py` | per-bot reranker resolution (binding→cache→build) | `RerankerResolver` | n/a | `bootstrap.py:709` | **LIVE** |
| `response_mode_port.py` | `deliver(result)` + `mode_name` | `CallbackDelivery`, `NoopDelivery` (`infrastructure/delivery/`) | n/a | `chat_worker/callbacks.py:171,290` (duck-typed; Port itself imported nowhere) | **LIVE-de-facto** (Port file itself has 0 importers) |
| `retrieval_fallback_port.py` | multi-stage retrieval chain | stages + null | `retrieval_fallback/registry.py` (live) | `orchestration/nodes/retrieve.py:1537,1562` | **LIVE-flagged** |
| `sanitizer_port.py` | CleanBase Tier-0 `sanitize(text)->(text, SanitizeReport)` | tier0 + null | `safety/registry.py:88` `build_sanitizer` (LIVE code) | **ZERO callers of `build_sanitizer`; DocumentService has no `sanitizer` ctor param** | **BUILT-NOT-WIRED** (F2) |
| `secrets_port.py` | `resolve(ref, encrypted)` / `encrypt` | `EnvSecretsAdapter` | n/a | `bootstrap.py:330` | **LIVE** |
| `self_rag_router_port.py` | `should_skip_retrieve(intent, query)` | Null + adaptive | `self_rag_router/registry.py` **commented** (:53-82) | none | **DEAD-REG** |
| `sentence_similarity_port.py` | adjacent-sentence similarity for semantic chunking | lexical (live default in `shared/sentence_similarity.py:96`), embedding adapter | `sentence_similarity/registry.py` **commented** (:51-88) | lexical only, hardwired in `shared/chunking/strategies.py` | **PARTIAL** (embedding-cosine variant unreachable) |
| `source_validator_port.py` | per-bot `allowed_source_domains` allow-list | Null + DomainAllowlist | `safety/registry.py:57` (live) | ONLY `document_worker.py:605`; NOT passed in `sync.py:519` / `test_chat/_shared.py:326` | **PARTIAL** (F9) |
| `strategy_ports.py` | 5 ports: ModelSelection / Prompt / ChunkingResolver / RerankerStrategy / EmbeddingStrategy + `ChunkingDecision` | only `ChunkingStrategyResolverPort` has impls (`chunking_strategy/llm_resolver.py:39`, `rule_resolver.py`) | `chunking_strategy/registry.py` live but `build_chunking_resolver` has **0 callers** | none of the 5 injected anywhere | **ORPHAN ×4 + BUILT-NOT-WIRED ×1** (F6) |
| `system_config_reader_port.py` | narrow `get(key, default)` | `SystemConfigService` (structural) | n/a | `bootstrap.py:323` | **LIVE** |
| `tenant_model_tier_port.py` | tenant→allowed cost tiers | Null impl | `tenant_model_tier/registry.py` **commented** (:54-83) | none | **DEAD-REG** |
| `text_normalizer_port.py` | VN accent restore / NFC strategy | Null (+BARTpho planned) | `text_normalizer/registry.py` **commented** (:53-84) | none | **DEAD-REG** |
| `tokenizer_port.py` | per-language `tokenize/count_tokens` | `vi_tokenizer.py` (underthesea), null | `tokenizer/registry.py` **commented** (:61-106) | none — `warmup.py:240` `hasattr(container,"tokenizer")` is **always False** → silent skip | **DEAD-REG** (F4 — multilingual impact) |
| `token_ledger_port.py` | `TokenLedgerEntry` snapshot + fire-and-forget `emit` | ledger impls | `token_ledger/` (live) | `bootstrap.py:302` → llm/embedder | **LIVE** |
| `tool_client_port.py` | MCP/tool-use `list_tools/call` | Null | `tools/registry.py` **commented** (:47-78) | none | **DEAD-REG** |
| `vector_store_port.py` | `hybrid_search/upsert_chunks/delete_by_*` + `HybridQuery/VectorCandidate` | PgVectorStore + Null | `vector/registry.py:37-41` (live) | `bootstrap.py:261` | **LIVE** (Null-fallback hazard, F17) |

**Head-count (FACT)**: of 44 port files, **12 have a fully commented-out registry** (cag, chunk_quality, convo_summary, hyde, proximity_cache, query_router, self_rag_router, sentence_similarity, tenant_model_tier, text_normalizer, tokenizer, tools — each `registry.py` body is 100% `#`-prefixed), **1 is built-not-wired at the service ctor** (sanitizer), **1 is a source-validator wired on only 1 of 3 ingest paths**, **4 strategy ports have zero implementations**, **2 ports are orphan contracts** (`pii_port.py` async, `response_mode_port.py` never imported though duck-typed impls run).

### 1.2 `application/dto/` (8 files)

| File | Purpose | Pipeline connection | Status |
|---|---|---|---|
| `ai_specs.py` | `LLMSpec/RerankerSpec/EmbeddingSpec/PromptTemplate` + `BindingPurpose` enum (`llm_primary/llm_intent/llm_rewrite/embedding/rerank`) | produced by `model_resolver/_helpers.py:222` etc., consumed by router/embedder/reranker | LIVE |
| `block.py` | RAG-Anything-style `Block` dataclass w/ dict-compat `__getitem__/get/__contains__`, `from_chunk_dict` lift | **zero consumers in `src/`** (only `tests/unit/test_dto_block.py`); gate key `blocks_api_enabled` defined at `shared/bot_limits.py:289` but read by nothing | **ORPHAN** (F8) |
| `bot_config.py` | `BotConfig` runtime DTO for `bots` row cache + `BotSettingOptions` + `RerankIntentWhitelist` | `BotRegistryService` cache → everywhere (4-key resolve) | LIVE |
| `chat_dto.py` | `AnswerDTO/CitationDTO/ChatAcceptedDTO/MessageDTO/ConversationHistoryDTO/JobStatusDTO` | Chat routes + jobs route | LIVE except `ConversationHistoryDTO` (0 consumers) |
| `chat_payload.py` | `ChatReceivedPayload` — wire schema of `chat.received` event; legacy `tenant_id` INT + `tenant_uuid` aliases | validated at `chat_worker/pipeline.py:145` | LIVE |
| `document_dto.py` | `DocumentDTO/IngestAcceptedDTO/IngestResultDTO/DeleteResultDTO` | documents routes + use cases | LIVE (`DeleteResultDTO.corpus_version` always 0 — F14; `DocumentDTO` main consumer is routes) |
| `llm_schemas.py` | structured-output schemas: `UnderstandOutput` (9-intent Literal), `GradeOutput/GradeBatchOutput`, `GenerateOutput/GenerateFlatOutput` (+`SubAnswerItem`), `GroundingVerdicts`, `DecomposeOutput`, `ReflectOutput`, `CitationItem` | LLM structured calls in understand/grade/generate/reflect/grounding nodes | LIVE |
| `model_runtime.py` | `ModelRuntimeConfig` frozen runtime spec (+`ProviderRuntime` w/ plain api_key, `mask()`, `compute_version_hash`) | model_resolver → LLM router | LIVE |
| `notify_channel.py` | `NotifyChannelConfig` webhook target (system_config OR env), `render_url/mask_for_log` | `notify_channel_resolver.py:77,92` | LIVE |

### 1.3 `commands/`, `queries/`, `events/`, `use_cases/`

| File | Purpose | Consumers | Status |
|---|---|---|---|
| `commands/chat_commands.py` | `AnswerQuestionCommand` (4-key + content + mode) / `GiveFeedbackCommand` | `routes/chat.py:49,104` | LIVE |
| `commands/document_commands.py` | `IngestDocumentCommand` / `DeleteDocumentCommand` / `RechunkDocumentCommand` / `RechunkByDocumentIdCommand` / `ReindexCorpusCommand` | documents routes; **`ReindexCorpusCommand` = 0 consumers** | LIVE / 1 dead |
| `queries/chat_queries.py` | `GetJobStatusQuery` (used, `routes/jobs.py:23`); `GetConversationHistoryQuery` / `GetTraceQuery` / `ListDocumentsQuery` = **0 consumers** | — | 1 live / 3 dead (F7) |
| `events/chat_completed.py` | `ChatCompletedEvent` + `ChatCompletionHookPort` + `ChatHookRegistry` (2-stage db/post_commit, semaphore + timeout + isolation) | `bootstrap.py:763`; fired at `chat_worker/callbacks.py:267-269` and `test_chat/chat_routes.py:681-683` | LIVE |
| `use_cases/answer_question.py` | 202-accepted: budget check → idempotency → persist msg → job row → `ChatReceived` outbox | `routes/chat.py:49` | LIVE |
| `use_cases/ingest_document.py` | 202-accepted: idempotency (tenant+source_url) → draft/reactivate doc row → job → `DocumentUploaded` outbox | `routes/documents.py:102` (canonical `/documents/create`) | LIVE (F1, F10) |
| `use_cases/delete_document.py` | sync vector delete + archive + stats-index purge + outbox | `routes/documents.py:202` | LIVE |
| `use_cases/rechunk_document.py` | wipe chunks + re-enqueue (URL-keyed + id-keyed variants; `_assert_reingestable` guard) | `routes/documents.py:239,286` | LIVE |
| `use_cases/get_job_status.py` / `give_feedback.py` | job poll; feedback → `request_logs` + outbox | `routes/jobs.py:19` / `routes/chat.py:104` | LIVE |

---

## PART 2 — Findings

### F1 — [HIGH · multi-bot / multi-doc] Ingest idempotency key omits `record_bot_id`, `workspace_id` and `document_name` → second bot / second doc on the same `source_url` is silently swallowed for 24h

**FACT (code):**
- Key = `sha256("ingest|{tenant}|{source_url}|0")` — `domain/value_objects/idempotency_key.py:40-52`; called from `use_cases/ingest_document.py:58-62` with only `(record_tenant_id, source_url, corpus_version=0)`.
- Gate: `use_cases/ingest_document.py:76` — `if existing is None and await self._idem.is_duplicate(idem_key): return prior job`. `existing` is looked up per-bot by `tool_name` (:73-75), so for a *different bot* in the same tenant it is always `None`.
- TTL = `DEFAULT_IDEMPOTENCY_TTL = 86_400` (`shared/constants/_04_jwt_auth.py:94`), registered at `ingest_document.py:145`.
- This use case IS the canonical path: `routes/documents.py:102` (`POST /documents/create`).

**HYPOTHESIS (concrete scenario, not runtime-reproduced):** tenant T, bot A ingests `https://example.com/menu.pdf` → key registered. Within 24h bot B (same tenant, any workspace) POSTs the same URL → `existing=None` (B has no doc row) → dedup hit → API returns bot A's `job_id` with 202; **no document row, no job, no chunks are ever created for bot B**, and the response looks successful. Same shape for the same bot ingesting a *second logical document* that shares `source_url` (multi-tab Google Sheets export — a case the codebase itself acknowledges in `document_commands.py:61-69`).
**Expert fix**: include `record_bot_id` + `workspace_id` + `tool_name` in `for_ingest_document` parts (mirror `for_chat_message`, which correctly includes tenant+bot+user, `idempotency_key.py:24-38`).

### F2 — [HIGH · built-not-wired · T1-Safety] CleanBase Tier-0 `SanitizerPort` can never run: `DocumentService` has no `sanitizer` constructor parameter

**FACT:**
- `SanitizerPort` + `SanitizeReport` fully specced (`ports/sanitizer_port.py:39-110`); live registry `infrastructure/safety/registry.py:88` (`build_sanitizer`), tier0 + null impls exist.
- `grep -rn build_sanitizer src/ragbot` outside the safety package → **0 hits**.
- `DocumentService.__init__` parameter list (`document_service/__init__.py:194-210`) has **no sanitizer param**; nothing ever sets `self._sanitizer`.
- Hot path reads `_sanitizer = getattr(self, "_sanitizer", None)` (`ingest_stages.py:311`) → always `None` → even with `system_config.cleanbase_tier0_enabled=true` the branch logs `cleanbase_tier0_skipped … reason="no_sanitizer_wired"` at **DEBUG** (`ingest_stages.py:330-341`).

**Consequence (HYPOTHESIS):** operator flips the documented flag expecting HTML-strip + zero-width removal + prompt-injection scrub at ingest; nothing changes, and the only trace is a debug log. Corpus poisoning defence advertised in the port docstring is inert platform-wide. **Fix**: add `sanitizer` ctor param + wire `build_sanitizer(cfg)` at the 4 construction sites (or better: inside `DocumentService.__init__` default, config-driven).

### F3 — [HIGH · config-knob dead · multi-bot] `system_config.pii_redactor_provider` is documented everywhere but read nowhere — PII redaction is hard-frozen to `null` at boot

**FACT:**
- `bootstrap.py:441-450`: comment says "Provider resolved PER-CALL from ``system_config.pii_redactor_provider``" but code passes the compile-time constant `provider=DEFAULT_PII_REDACTOR_PROVIDER` (= `"null"`, `shared/constants/_13_adapchunk_ocr_parser.py:100`) into a **Singleton**.
- `grep -rn pii_redactor_provider src/ragbot` → hits are comments/docstrings only (bootstrap.py:442, ports/pii_redactor_port.py:14, shared/bot_limits.py:117). **No `cfg.get("pii_redactor_provider")` exists.**
- `NullPiiRedactor.redact` returns `(text, [])` (`infrastructure/pii/null_pii_redactor.py:16-17`); chat boundary then passes through (`chat_worker/payload.py:59-66`); ingest boundary same (`ingest_helpers.py:299`).
- `PresidioPiiRedactor` is an explicit STUB whose ctor raises `NotImplementedError` (`presidio_pii_redactor.py:17-23`) → registry fail-soft returns Null (`pii/registry.py:47-54`).

**Consequence (HYPOTHESIS):** a bot owner who flips `plan_limits.pii_redaction_enabled=true` (the per-bot half of the contract, honored at `payload.py:61` and `ingest_helpers.py:299`) still gets **zero redaction** — the per-bot toggle gates a Null strategy, and no config/DB change can activate `vn_regex` without editing the constant + redeploy. Multi-bot promise broken at the platform half. Also note both live redactors are **language-blind** (`vn_regex_pii_redactor.py:76` takes text only; legacy `regex_pii_redactor.py:27` declares `language: str = "vi"  # noqa: ARG002` — argument ignored), while the language-aware async `pii_port.PIIRedactorPort` (:11-19) is an orphan still exported from `ports/__init__.py:23`.

### F4 — [HIGH · dead code / orphan features] 12 Strategy registries are 100% commented out — the ports exist, the Null objects exist, the features are unreachable

**FACT** — registry body entirely `#`-commented (verified per file): `cag/registry.py:50-86`, `chunk_quality/registry.py:57-99`, `convo_summary/registry.py:47-77`, `hyde/registry.py:46-76`, `proximity_cache/registry.py:50-85`, `query_router/registry.py:51-81`, `self_rag_router/registry.py:53-82`, `sentence_similarity/registry.py:51-88`, `tenant_model_tier/registry.py:54-83`, `text_normalizer/registry.py:53-84`, `tokenizer/registry.py:61-106`, `tools/registry.py:47-78`; plus `embedding/multi_vector_registry.py:64` (build fn commented).

Impact highlights:
- **Tokenizer (multilingual axis)**: `TokenizerPort` promises per-language tokenization (vi underthesea); warmup probes `hasattr(container, "tokenizer")` which is **always False** (`observability/warmup.py:240-246`, comment even says "silent skip … opt-in per deployment") — so Vietnamese word segmentation is never available to any consumer; BM25 relies on Postgres tsquery alone.
- **HyDE**: the Port + registry are dead, but the *feature* ships through a concrete `HyDEGenerator` class injected directly (`bootstrap.py:584-587`, class at `application/services/hyde_generator.py:67`) — Port bypassed, so an alternative HyDE strategy cannot be swapped by config despite the port docstring promising `system_config.hyde_enabled` + registry.
- **CAG / proximity-cache / query-router / self-RAG router / convo-summary / tenant-model-tier / text-normalizer / tool-client / chunk-quality(registry) / multi-vector / sentence-similarity(embedding variant)**: pure scaffolding. Every one of these port docstrings advertises a `system_config.<x>_provider` knob that does not exist at runtime. `DEFAULT_CONVO_SUMMARY_*` constants (`_00_app_env_taxonomy.py:132-133`) and `bots.convo_summary_enabled` doc references are dead weight.
- **Decision needed (T3 hygiene, per `block-integrity-quality-gate` skill spirit)**: revive (uncomment + wire) or delete; the half-state is the worst option — 12 knobs documented in code that do nothing.

### F5 — [MEDIUM-HIGH · port-contract drift] Orchestration depends on `find_similar_with_text`, which is NOT on `SemanticCachePort`

**FACT:** `SemanticCachePort` declares only `find_similar` + `store` (`ports/cache_port.py:52-76`). The cache node calls `semantic_cache.find_similar_with_text(...)` (`orchestration/nodes/check_cache.py:96`) — a concrete-adapter method (`infrastructure/cache/semantic_cache.py:191-204`); the port-level `find_similar` is effectively legacy (impl comment at :174-179 admits the hash path is impossible through the port shape). Also the impl adds `embedding_column`, `redis_client`, `step_tracker` kwargs unknown to the Port.
**Consequence (HYPOTHESIS):** any alternative adapter written against the Port (the whole point of the Protocol) breaks `check_cache` with `AttributeError`; the Port is a stale contract. **Fix**: lift `find_similar_with_text` (and the extra kwargs) into the Port, or narrow the node to the Port surface.

### F6 — [MEDIUM · orphan ports] 4 of 5 `strategy_ports.py` contracts have zero implementations; the 5th has a live registry that nothing calls

**FACT:**
- `ModelSelectionStrategyPort.select_llm/select_fallback_chain` (`strategy_ports.py:36-53`) — no class implements it; the real resolver exposes `resolve_llm/resolve_reranker/resolve_embedding/resolve_runtime` (`model_resolver/service.py:182,239,400,435`) so it does not even structurally satisfy the port. `resolve_fallback_chain`/`resolve_prompt` are commented out in the service (:708,:722).
- `PromptStrategyPort`, `RerankerStrategyPort`, `EmbeddingStrategyPort` — grep finds no implementing class anywhere.
- `ChunkingStrategyResolverPort` has real impls + live registry (`chunking_strategy/registry.py:34-43`), but `build_chunking_resolver` has **0 callers**; the actual strategy choice at ingest goes through the plain function `select_strategy` (`ingest_stages.py:620,665`). The AdapChunk LLM-resolver adapter (`chunking_strategy/llm_resolver.py`) is unreachable.
- `ChunkingDecision` dataclass is likewise produced by nothing on a live path.

### F7 — [MEDIUM · dead read-side] CQRS query objects + history DTO never used

**FACT:** `GetConversationHistoryQuery`, `GetTraceQuery`, `ListDocumentsQuery` (`queries/chat_queries.py:22,38,45`) and `ConversationHistoryDTO` (`dto/chat_dto.py:69`) have zero non-definition references; `ReindexCorpusCommand` (`document_commands.py:80`) has zero consumers. Only `GetJobStatusQuery` is live (`routes/jobs.py:23`). There is no conversation-history read API and no corpus reindex path via commands — either wire them or remove (documented-but-nonexistent surface misleads BE consumers).

### F8 — [MEDIUM · built-not-wired] Blocks API (`dto/block.py`) is fully orphan; `plan_limits.blocks_api_enabled` flips nothing

**FACT:** `Block`, `ChunkType`, `from_chunk_dict` (201 lines with dict-compat glue) have **no importer in `src/`** (only `tests/unit/test_dto_block.py`). The gate key exists in the bot-limit schema (`shared/bot_limits.py:289`) — a per-bot flag whose read count is zero. The docstring claims "every wrap site (rerank output, MMR output, generate input) should funnel through it" (`block.py:174-177`) — no wrap site exists.

### F9 — [MEDIUM · multi-format/multi-path] `DocumentService` is hand-assembled at 4 sites with diverging capability sets — the "one canonical funnel" only fully exists on the worker path

**FACT** (constructor calls):
- worker (`document_worker.py:597-610`): source_validator ✅, narrate ✅, chunk-ctx enricher ✅, corpus_version ✅, stats ✅.
- sync route (`sync.py:519-531`): **no `source_validator`** → `plan_limits.allowed_source_domains` is NOT enforced for documents entering via `/sync` (validation helper degrades to passthrough when validator is None, `ingest_helpers.py:381`).
- test-chat harness (`test_chat/_shared.py:326-337`): no source_validator, no narrate, no enricher, no corpus_version.
- All four sites: no sanitizer (see F2).
**Consequence (HYPOTHESIS):** identical bytes produce different ingest post-processing depending on entry door — exactly the parallel-path drift the `canonical-ingest-flow` rule forbids. **Fix**: one factory (container provider) that assembles DocumentService with the full dependency set; entry points stop hand-building it.

### F10 — [MEDIUM · multi-doc / happy-case identity] `tool_name = slugify(document_name)[:255]` — diacritic-folding collisions silently merge two logical documents into one row

**FACT:** `ingest_document.py:63` derives the natural key by slugify; `:73-75` looks up by that slug; `:105-110` **reuses the surviving row's PK** (`doc = replace(doc, id=existing.id)`) so save() UPDATEs in place.
**HYPOTHESIS (concrete):** Vietnamese corpus: "Bảng giá" and "Bang gia" (or "Bảng giá!") slugify to the same `bang-gia` → the second ingest *reactivates and overwrites* the first document row (different `source_url`, different content) instead of creating a second document; the first document's identity survives but its content/chunks are replaced on job completion. No warning is emitted. Also `[:255]` is an inline magic number duplicating `MAX_DOCUMENT_NAME_LENGTH` intent (`document_commands.py:33` uses the constant; the use case does not).
**Related (FACT):** `DocumentRepositoryPort.get_by_source_url` returns a single `Document | None` (`repository_ports.py:81-87`) while the code elsewhere admits multiple docs can share `source_url` (`document_commands.py:61-69`) → URL-keyed rechunk can pick an arbitrary sibling.

### F11 — [LOW-MED · DTO/doc drift, known bug-class] `BindingRow.purpose` doc comment contradicts the runtime enum — the historical `rerank`-vs-`reranker` drift is re-documented

**FACT:** `ports/ai_config_port.py:69` documents `purpose: str # llm_primary | llm_fallback | embedding | reranker | moderation_input | moderation_output`, but the runtime SSoT is `BindingPurpose` = `llm_primary | llm_intent | llm_rewrite | embedding | rerank` (`dto/ai_specs.py:21-27`), and the resolver dispatches on `BindingPurpose.RERANK.value == "rerank"` (`model_resolver/_binding_mixin.py:110`). Memory file `feedback_v2_bug_lessons` records that exactly this string drift once produced a silent NullReranker in production. A dev seeding a binding from the port docstring (`purpose='reranker'` or `'llm_fallback'`) recreates it. **Fix**: make the port comment reference `BindingPurpose` instead of restating values.

### F12 — [LOW · zero-hardcode] Magic defaults inside port/DTO signatures drift from (or duplicate) `shared/constants`

All FACT:
- `guardrail_port.check_output(..., shingle_size: int = 8, ...)` (`guardrail_port.py:104`) vs platform constant `DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE = 24` (`_06_llm_defaults.py:123`). Latent only — `guard_output.py:244` passes the configured value — but any new caller relying on the port default gets 3× looser leak detection.
- `SemanticCachePort.find_similar(threshold=0.97)` and `store(ttl_s=3600)` inline literals (`cache_port.py:61,75`) while constants exist — and are themselves **duplicated**: `SEMANTIC_CACHE_THRESHOLD=0.97` lives in `_04_jwt_auth.py:151` (misfiled module) AND `DEFAULT_SEMANTIC_CACHE_THRESHOLD=0.97` in `_05_embedding_circuitbreaker.py:91`. Three places to update one number.
- `reranker_port.rerank(top_n: int = 5)` (`reranker_port.py:29`) inline vs `DEFAULT_SPEC_RERANK_TOP_N=5` (`_05:33`).
- `bus_port.request(timeout_s: float = 5.0)` (`bus_port.py:44`).
- `AnswerQuestionCommand.history_limit … le=50` (`chat_commands.py:50`); `BotSettingOptions.max_tokens le=32000` (`bot_config.py:80`); `NotifyChannelConfig.conversation_id max_length=128` (`notify_channel.py:47`).
- `repository_ports.list_by_bot(limit=DEFAULT_RAG_TOP_K)` (`repository_ports.py:102`) — semantically wrong constant (retrieval top-k reused as document page size).

### F13 — [LOW · label drift] `DEFAULT_RERANK_SKIP_INTENTS` contains `"oos"` but the classifier can only emit `"out_of_scope"`

**FACT:** skip set `{"chitchat","oos","greeting","feedback","vu_vo","factoid"}` (`_02_per_intent_rerank_skip_gate_.py:14`); gate compares `state["intent"].lower()` membership (`nodes/rerank.py:134-142`); the only intent vocabulary the LLM can produce is the `UnderstandOutput` Literal — which spells it `out_of_scope` (`dto/llm_schemas.py:80-90`). The `"oos"` member can never match (OOS turns also usually refuse before rerank, so impact ≈ dead config member + copy-paste trap). Similarly `QueryIntent` Literal (`query_router_port.py:33-40`) manually mirrors `QUERY_INTENT_TYPES` (`_17_pipeline_audit.py:55-62`) — in sync today (6/6 labels), but two hand-maintained vocabularies with a "keep in sync" comment is drift-by-design; and the whole `QueryRouterPort` is dead anyway (F4).

### F14 — [LOW · misleading API field] `DeleteResultDTO.corpus_version` is always `CorpusVersion(0)`

**FACT:** `use_cases/delete_document.py:112-115` returns the literal `CorpusVersion(0)` while a real `corpus_version_service` exists and is injected elsewhere (`bootstrap.py:673`). A BE consumer using this field for cache-busting will bust nothing / always see 0.

### F15 — [LOW · naming-convention] Job payloads store the internal UUID under the external key name `bot_id`

**FACT:** `answer_question.py:108`, `ingest_document.py:121`, `rechunk_document.py:100,171` all write `"bot_id": str(cmd.record_bot_id)` into `jobs.payload`. Per the project convention (no-prefix = external slug), this is an internal `record_bot_id` masquerading as `bot_id`. Consumers reading `payload.get("bot_id")` exist for other streams (`chat_worker/pipeline.py:106` reads the *event's* genuine slug), so the mislabeled jobs payload is a live confusion trap for future readers of `document.ingest` jobs (the document_worker indeed treats it as UUID: `document_worker.py` `UUID(str(bot_id))`).

### F16 — [LOW · comment/config lies in DI wiring]

FACT, all in `bootstrap.py`:
- :441-443 PII "resolved PER-CALL from system_config" — actually a boot-time constant Singleton (see F3).
- :560-566 rate limiter comment: "Tests + dev runs may swap to ``in_memory`` via system_config without code edits" — code passes literal `provider="redis_sliding"`; no config read exists.
- :640-643 conversation-state `provider="jsonb"` literal (comment claims per-request Null-vs-Jsonb decision; the registry choice itself is hardcoded, not config-driven).
These matter because the platform's own review checklist treats "config-driven provider" as the invariant; comments claiming it while code hardcodes it will pass casual review.

### F17 — [INFO · graceful-degradation hazard on a core path] Unknown vector-store provider silently degrades to `NullVectorStore`

**FACT:** `vector/registry.py:59-67` — unknown/empty provider logs a warning and returns `NullVectorStore` (0 results, 0 upserts). For optional strategies Null-fallback is correct; for the PRIMARY store a config typo means every ingest "succeeds" with zero persisted vectors and every retrieve returns nothing — the precise "silent retrieval death" failure class already lived through (embedding=NULL campaign incident). Fail-loud at boot would match the stated policy ("Required outputs … fail-fast", CLAUDE.md async rule 5). Same pattern in `build_embedder` (unknown → default provider, `embedding/registry.py:73-80`).

### F18 — [INFO · happy-case shape assumptions worth knowing]

- `CragGraderPort` contract: chunks "MUST carry `chunk_id` or `id` … `content` or `text`" (`crag_grader_port.py:70-73`) — the dual-key dict shape is the de-facto chunk schema; `Block.from_chunk_dict` was the attempt to formalize it and is orphan (F8). Until then every node re-implements `c.get("content") or c.get("text")`.
- `GenerateOutput.sub_answers` is explicitly SHAPE-only (`llm_schemas.py:126-129` — app never reads it) — compliant with sacred rule #10, worth preserving in review.
- `LLMSpec.to_litellm_kwargs` merges `extra_params` LAST (`ai_specs.py:56`) — a binding's `extra_params` can silently override `model`/`temperature`/`max_tokens`; no key blacklist. Owner-side foot-gun, tenant-scoped only.
- `ChatReceivedPayload` keeps triple tenant aliases (`record_tenant_id`/`tenant_uuid`/`tenant_id` INT, `chat_payload.py:35-37`) — migration-window bridge; resolver enforces at worker. Fine, but the INT path is the last remaining "legacy upstream" surface; flag for eventual removal.
- `answer_question.py:73` uses `__import__("uuid").UUID(prior)` inline — works, but is a lint-dodge style smell.
- `GetJobStatusUseCase` fills `status=row.get("status", "queued")` (`get_job_status.py:26`) — a missing column silently reports "queued" forever instead of failing loud.
- `rechunk_document._assert_reingestable` (`rechunk_document.py:41-57`) reads `getattr(doc, "metadata", None)` + repo-derived `has_raw_content` marker (`document_repository.py:50-57` sets it; `:66` strips it before persist) — currently correct, but the guard depends on a *non-persisted, repo-injected* metadata key; renaming it in the repo silently converts the guard into "refuse every bytes-doc rechunk" (defaults hide the drift — the classic getattr-with-default DTO-drift pattern this review was asked to hunt).

### F19 — [INFO · multi-tenant scoping audit of the port surface]

- Tenant-scoped kwargs are enforced on all repository ports (`repository_ports.py` — every method takes `record_tenant_id`), vector store (`vector_store_port.py:48-80`), semantic cache (`cache_port.py:52-76`), AI-config bindings (`ai_config_port.py:136-167`). Good.
- `LexicalRetrievalPort.search` scopes by `record_bot_id` only (`lexical_retrieval_port.py:44-50`) — safe because `record_bot_id` is a UUID PK unique across tenants, but defence-in-depth (RLS GUC) is the only tenant check on that path.
- `ProximityCachePort` explicitly pushes tenant scoping to DI time (`proximity_cache_port.py:45-47`) — acceptable pattern but currently moot (dead, F4).
- `TokenLedgerEntry` snapshots the full 4-key identity (`token_ledger_port.py:26-32`) — exemplary.
- `ConversationStatePort.save_state` takes optional `record_tenant_id` for GUC-less defence (`conversation_state_port.py:70-88`) — good, though "optional" means call sites can forget it silently.

### F20 — [INFO · broad-except audit within scope]

Only two `except Exception` in scope, both policy-compliant with `# noqa: BLE001` + reason: `events/chat_completed.py:133` (hook isolation wrapper) and `use_cases/give_feedback.py:58` (best-effort write). `payload._maybe_redact_chat_query` broad-except is outside scope but same pattern. No violations found in ports/dto/commands/queries/use_cases. Domain literals: none found in scope (all port docstrings are domain-neutral). Version-refs: none (checked `_v[0-9]|_legacy` over scope — 0 hits).

---

## PART 3 — Answers to the mandate questions

**"Is every port implemented + registered + injected?" — NO.** Traced to `bootstrap.py`:
- Fully live (impl + registry/factory + injection): ai_config, audit_logger, bus, cache/semantic-cache, crag_grader, conversation_state, doc_profile, document_parser, embedding, embedding_text, entity_extractor, guardrail, language_pack(+repo), lexical_retrieval, llm, metadata_filter, metrics, narrate, notify_channel, ocr, outbox, pii(strategy — but see F3), rate_limiter, repositories, reranker(+resolver), retrieval_fallback, secrets, system_config_reader, token_ledger, vector_store, proposition_decomposer (flag-gated), response-delivery (duck-typed).
- **Registry commented out (feature OFF platform-wide, not even the Null is wired)**: cag, chunk_quality(registry), convo_summary, hyde(port), proximity_cache, query_router, self_rag_router, sentence_similarity(embedding variant), tenant_model_tier, text_normalizer, tokenizer, tool_client, multi_vector. (F4)
- **Port + live registry but zero callers**: sanitizer (F2), chunking-strategy resolver (F6).
- **Ports with zero implementation**: ModelSelectionStrategyPort, PromptStrategyPort, RerankerStrategyPort, EmbeddingStrategyPort (F6), async `pii_port.PIIRedactorPort` (impl exists but outside the live registry, F3).
- **Ports with only-Null-live behavior in production today**: PII (provider constant `"null"`, F3) — every other Null-wired feature at least has a config-reachable real strategy.

**DTO drift risks (getattr-with-default class)**: `rechunk_document.py:41-57` (`has_raw_content` repo-marker, F18), `documents route _record_tenant` getattr on request.state (`documents.py:85` — middleware-dependent), `Block.get` metadata fall-through (orphan anyway, F8), `GetJobStatusUseCase` `.get("status","queued")` (F18), `BindingRow.purpose` free-string vs enum (F11), `ChatReceivedPayload` triple tenant alias (F18).

---

## PART 4 — Priority recommendations (short list)

1. **Fix `for_ingest_document` key** to include bot + workspace + tool_name (F1) — 1-line domain change + tests; closes a silent multi-bot data-loss hole on the canonical API.
2. **Wire or delete the sanitizer** (F2) and **read `pii_redactor_provider` from system_config** (F3) — both are advertised safety features that are inert; each is a small ctor/DI change.
3. **Registry triage** (F4): one decision per commented registry — revive (uncomment + container provider + smoke test) or remove port+impls+constants. Highest-value revive candidates: tokenizer (vi BM25), sentence-similarity embedding variant (semantic chunking quality); best delete candidates: tools, tenant_model_tier, proximity_cache (superseded by semantic cache).
4. **Lift `find_similar_with_text` into `SemanticCachePort`** (F5) and delete the 4 unimplemented strategy ports or implement them (F6).
5. **Single DocumentService factory** in the container so sync/test paths stop dropping source-validator/narrate deps (F9).
6. De-slug the document natural key or add a collision check (F10); fix the `BindingRow.purpose` doc comment (F11); collapse the duplicated 0.97 semantic-cache constants (F12).
