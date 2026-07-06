# DEEPDIVE — application/services (core, excluding document_service/)

- **Date**: 2026-07-02
- **Scope**: every file under `src/ragbot/application/services/` EXCEPT `document_service/` — 84 files, ~19,300 lines, all read line-by-line.
- **Method**: full read of every file; cross-verification of callers via grep into `bootstrap.py`, `orchestration/`, `interfaces/`, `alembic/`; one Python run to confirm a dict-key duplicate. Every claim below carries `file:line` evidence and is labeled **FACT** (verified in code/output) or **HYPOTHESIS** (needs runtime/DB evidence).
- **Rule #0 note**: no runtime/load-test was run for this review; all findings are STATIC code-evidence baseline, not runtime-verified.

---

## Part 1 — Per-file inventory (what it does, how it connects)

### Identity / bot lifecycle

| File | Purpose | Pipeline connection |
|---|---|---|
| `bot_registry_service.py` (322) | Redis-backed 4-key bot config cache (`ragbot:bot:{tenant}:{ws}:{bot}:{channel}`), single-flight on miss, tenant-mismatch cache-poison eviction (`:170-194`), TTL self-heal | Resolve boundary for every chat/ingest request; bootstrapped at FastAPI lifespan; invalidated by `BotManagementService` |
| `bot_management_service.py` (388) | Bot CRUD + audit (HMAC chain via `insert_audit_row`) + registry invalidate + `bot.registry.changed.v1` outbox | Admin routes → this service → registry/outbox |
| `bot_lifecycle_service.py` (430) | Irreversible purge saga (S1 guard → S2 hard-DELETE+audit+outbox in one tx → S3 corpus bust → S4 registry bust → S5 UQ-cache SCAN+UNLINK → S6 deliberate skips), `purge_tenant` sequential fan-out | Admin purge endpoint; RLS-bound `tenant_session` |
| `tenant_guard.py` (45) | Pure assertions `ensure_same_tenant` / `assert_owns` → `TenantIsolationViolation` | Defense-in-depth called from services/routes |
| `tenant_config_cache.py` (255) | Redis cache of `tenants` runtime row (bypass_rate_limit, rate_limit_per_min, monthly_token_cap, allowed_origins), single-flight | Request middleware boundary |

### Model / provider resolution

| File | Purpose | Pipeline connection |
|---|---|---|
| `model_resolver/service.py` (755) | `resolve_llm` / `resolve_reranker` / `resolve_embedding` / `resolve_multi_purpose` / `resolve_runtime` / `preview_runtime` / `resolve_cascade_runtime`; per-bot binding → system_config default → raise | Chat worker, ingest, admin preview; DI singleton |
| `model_resolver/_cache_mixin.py` (306) | `_mem` bindings cache (TTL 60s) + `_l1` runtime LRU + Redis L2; `bootstrap_cache`, `invalidate` | Same |
| `model_resolver/_binding_mixin.py` (386) | binding→spec mappers, A/B variant pick by conversation hash, `_build_runtime` (params/pricing/caps/fallback model), cost-tier by output price | Same |
| `model_resolver/_helpers.py` (242) | `format_litellm_model` (requires_prefix DB-driven), `resolve_purpose_for_intent`, cache (de)serialisation, spec adapters | Re-exported package-wide |
| `reranker_resolver.py` (340) | SEPARATE raw-SQL per-bot reranker resolver: `bot_model_bindings(purpose='rerank')` → `system_config.reranker_*` platform default → NullReranker; Redis 60s cache; multi-key pool factory | Rerank node via `resolve_for_bot(record_bot_id)` |
| `provider_key_resolver.py` (256) | Hot-swap API key: Redis(30s) → `api_keys` table (encrypted preferred, plaintext dual-read) → env map; `upsert_api_key` write path | Embedder/reranker/LLM adapters per call |
| `ai_config_service.py` (872) | Admin CRUD for providers/models/bindings + `ai_keys` add/verify/list + audit + resolver cache invalidation; key-verify registry (Jina only) | `admin_ai` routes |

### Config / language / prompt

| File | Purpose | Pipeline connection |
|---|---|---|
| `system_config_service.py` (365) | `system_config` K/V read (Redis 5-min TTL + jitter) / write (+ outbox `system_config.changed.v1` cross-replica invalidate) + `chat_max_history` trim side-effect | SSoT for every runtime knob |
| `language_pack_service.py` (229) | `LanguagePackPort` impl: per-key + whole-pack Redis cache → DB → default-language DB → in-memory `i18n.py` → `""` | Prompts for orchestration nodes, refusal text tier 6, sysprompt default rules |
| `sysprompt_assembler.py` (247) | ADR-W1-S10 governed append: `bot.system_prompt` + `language_packs[locale].sysprompt_default_rules` − `plan_limits.sysprompt_rules_disabled` (numbered + `# HEADER` addressing) | `chat_worker/pipeline.py:602-608`, `chat_stream.py:272`, test_chat, `admin_bots` effective-prompt |
| `oos_template_resolver.py` (231) | 7-tier refusal-text chain (bot column → plan_limits → [ws/tenant placeholders] → system_config → language pack `refuse_message` → `""`), `{bot_name}` substitution | Wired in bootstrap:619; chat_stream/chat_async/test_chat refuse paths |
| `guardrail_rule_loader.py` (331) | DB-driven moderation `RuleSet` (platform NULL-tenant rows + per-tenant override rows win), L1 TTL cache + per-key lock, compile-failure skip, invalidate publishes Redis event | LocalGuardrail input/output moderation |

### Query-pipeline enrichment (application layer of the LangGraph)

| File | Purpose | Pipeline connection |
|---|---|---|
| `heuristic_intent_classifier.py` (136) | Layer-1 regex intent (greeting/chitchat 0.90, agg/multi-hop/comparison 0.85) from `RoutingSignals` (locale-able) | `orchestration/nodes/understand.py:117` — **called without signals → vi seed always** |
| `multi_query_expansion.py` (724) | N-paraphrase expansion (per-intent prompt from language_packs), variant-0 safety net, Jaccard/cosine dedup, RRF merge, entity-grounded variant append; class facade `MultiQueryExpansionService` | `query_graph`, `nodes/retrieve.py`, `nodes/rrf_round_robin.py` |
| `hyde_generator.py` (165) | HyDE facade with wall-clock timeout; failure → original query | `query_graph._embed_query` when `hyde_enabled` |
| `vocabulary_expander.py` (530) | Generic VN teencode/EN-mix vocab + per-bot `custom_vocabulary` → `context_base.vocabulary` hint (never touches answer); unigram + n-gram with VN negation guard; language-gated (`VI_DOMAIN_LANGUAGES`) | `nodes/retrieve.py:747` via `get_default_expander(lang)` |
| `superlative_context_enricher.py` (442) | Detect superlative intent (vi+en packs) → regex-parse price/duration/discount/bonus from chunks → ranked top-K into `context_base.superlative` | `nodes/retrieve.py:1870` via `get_enricher_for_language(state.language)`; **`query_graph.py:238` creates an unused instance** |
| `adaptive_rerank_weight.py` (208) | Pure per-intent RRF weight resolver (per-bot `pipeline_config.rerank_weights_by_intent` → constants), clamped non-negative | `nodes/retrieve.py` fusion |
| `crag_grader/` (5 files, 514) | Port+Strategy+Registry+Null for CRAG grading: `per_chunk` (legacy N calls, semaphore), `batch` (1 structured call, windowed), `null` (all 1.0); registry fail-soft to Null | `query_graph` grade node via `build_crag_grader(system_config.crag_grader_provider)` |
| `structured_output_helper.py` (739) | Provider-enforced JSON schema: capability-driven mode select (json_object/json_schema/tool/plain), schema hardening for OpenAI strict, reasoning-model fallback parse, bounded repair retry, usage sink | `query_graph._invoke_structured_llm_node`, `SlotExtractor` |
| `query_intent_extractor.py` (113) | LLM labels query with operator-seeded metadata vocab → `metadata_json @>` filter dict; direct `litellm` call | `nodes/retrieve.py` metadata pre-filter |
| `structured_ref_extractor.py` (96) | Regex Điều/Chương/Khoản/Mục/Phụ lục → chunk metadata + query prefilter | ingest (document_service) + retrieve |
| `citation_policy.py` (30) | Enforce ≥1 citation + citations ⊆ retrieved chunk ids → `CitationHallucinated` | Generate/validation path |
| `hallu_verifier.py` (339) | Speculative-streaming draft-vs-main verifier: shingle overlap + numeric-fact subset + optional embedding cosine → `redo` SSE | bootstrap + `infrastructure/llm/speculative_router.py` |

### Ingest-side enrichment (used by document_service — read for context)

| File | Purpose | Pipeline connection |
|---|---|---|
| `chunk_context_enricher.py` (330) | Anthropic-CR situated-context per chunk → dedicated `document_chunks.chunk_context` column (BM25 signal only); Null provider default; storage cap 1024 | document_service ingest, `llm_chunk_context_provider` |
| `contextual_chunk_enrichment.py` (369) | Inline CR wrap (`<chunk_context>…</chunk_context>\n\nchunk`) with Anthropic cache_control + OpenAI cached-token accounting; `score_chunk_quality` 4×0.25 heuristic | document_service ingest stages |
| `narrate_service.py` (213) + `narrate_dispatch.py` (173) + `narrate/{table,formula}_narrator.py` | Narrate-then-Embed Layer 7: block-type classify (dominant block), TABLE rule-based lineariser, FORMULA LLM narrator, dual-content metadata (raw preserved) | `document_service` ingest stages; feature-flag default OFF |
| `content_type_router.py` (104) | Group blocks by `block_type` + histogram event (observability only) | document_service stats |
| `parsed_md_dump.py` (221) | Debug artefact: write parsed markdown to `{PARSED_MD_DIR}/{tenant}/{doc}.md`, fail-soft | upload path + admin download endpoint |
| `ingest_idempotency_service.py` (295) | `X-Idempotency-Key` dedup rows (tenant+ws+key unique), processing/done/failed states, expired-row retry | documents create endpoint + worker |
| `ingest_quota_service.py` (185) | Per-tenant daily doc quota, `SELECT FOR UPDATE` + rollover, missing-row fail-loud | documents create endpoint |
| `google_link_service.py` (288) | Validate Google Docs/Sheets links (type/access), `to_export_url` (sheet→csv+gid, doc→docx), has-data probe | sync/upload routes + worker fetch |

### Ops / observability / quota

| File | Purpose | Pipeline connection |
|---|---|---|
| `step_tracker.py` (294) | Per-step timing/tokens/cost → `request_steps` (optional batch flush; PII redaction at persistence boundary when bot opted in) | chat_worker pipeline, document_worker, chat_stream |
| `tenant_rate_limiter.py` (250) | Layer-1 per-tenant fixed-window INCR; bypass observability-preserving; fail-open | Request middleware |
| `tenant_token_meter.py` (335) | Monthly Redis hash meter + DB checkpoint/restore; warn/block % of cap | LLM router boundary |
| `token_budget.py` (45) | `ensure_affordable`/`record_usage` over `QuotaRepositoryPort` | quota gate |
| `cost_cap_alerter.py` (208) | Read-only tenant usage-vs-quota sweep → structured warn/error events | `embedded_workers` + audit script |
| `tenant_analytics_service.py` (842) | Tenant/workspace/bot analytics over `request_logs` (pass-rate, cost, latency percentiles, drift 2-window compare), super-admin cross-tenant rollup | `admin_analytics` routes |
| `crm_analytics_service.py` (271) | CRM dashboard aggregates (tokens/latency/nodes/top-questions/quality/budget-status) with `(:tid IS NULL OR …)` scoping | `crm` routes |
| `error_notify_hook.py` (117) | Exception→severity map, fire-and-forget webhook dispatch, strong-ref task set | Outermost worker catch sites |
| `notify_channel_resolver.py` (195) | Notify channel config: Redis → system_config row → env fallback → None (negative sentinel) | WebhookNotifyDispatcher |
| `webhook_secret_rotation.py` (440) | Versioned HMAC secrets (scrypt hash-only storage, grace-period verify chain, tenant-scoped) | `admin_webhooks` route |
| `jwt_token_service.py` (482) | Service JWT mint/verify/revoke with version bump + Redis version cache + revoke outbox | `app.py`, `tenant_context` middleware |
| `audit_log_hasher.py` (138) + `audit_verifier.py` (151) | SHA-256 audit chain hash (bit-stable with alembic 010g SQL) + tenant-scoped chain verifier | `audit_chain_writer`, admin verify endpoint |
| `idempotency.py` (36) | Redis SETNX chat dedup | chat enqueue |
| `retry_policy.py` (212) | Pure retry/backoff + adaptive-cooldown CircuitBreaker | in-process consumers |
| `slot_extractor.py` (268) | LLM JSON-mode slot extraction from owner-declared `action_config.slots_schema` (dynamic Pydantic, `extra=forbid`) | bootstrap:664 → query_graph action flow |
| `action_config_validator.py` (99) | Shape/bounds gate for owner `action_config` before persist | admin bot API |
| `faq_candidate_service.py` (395) | Cluster REFUSE_NO_DOCS questions (greedy cosine) → operator review candidates + SQL repo | `admin_refuse_suggestions` route + script |
| `corpus_version_service.py` (239) | 12-char corpus tag from `MAX(GREATEST(updated_at,deleted_at))` per bot; Redis 5-min; legacy tag on error | semantic-cache key + purge saga |
| `ragas_metric_adapter.py` (99) | Deterministic RAGAS stub (Port for future real impl) | `scripts/eval_ragas_metrics.py` only (dev tool by design) |
| `persona_quality_gate.py` (104) | Audit-only sysprompt anti-pattern scan (oversized/pollution/conflict) | **NO production caller** (tests only) |

### Dead code (explicit DEAD-CODE-NOTICE headers, fully commented out) — FACT

- `boilerplate_resolver.py` — 3-tier boilerplate pattern chain, never wired.
- `cag_service.py` — CAG retrieve-bypass coordinator; "shipped but never plumbed into query_graph".
- `google_sheets_test_fetcher.py` — test-only tab enumerator.
- `multi_agent_review/` — ALL 7 sub-files carry the dead-code header (verified: 7/7 headers).
- `model_resolver/service.py:707-755` — `resolve_fallback_chain()` + `resolve_prompt()` commented dead (0 callers).

---

## Part 2 — Bootstrap wiring vs orphaned

**Wired via `bootstrap.py` DI (imports at bootstrap.py:14-46)** — FACT: AIConfigService, AuditVerifier, BotLifecycleService, BotManagementService, BotRegistryService, CitationPolicyService, CorpusVersionService, build_crag_grader, ErrorNotifyHook, GuardrailRuleLoader, HALLUVerifier, HyDEGenerator, IdempotencyService, IngestIdempotencyService, IngestQuotaService, LanguagePackService, ModelResolverService, NotifyChannelResolver, OosTemplateResolver, ProviderKeyResolver, RerankerResolver, SlotExtractor, SysPromptAssembler, SystemConfigService, TenantConfigCache, TenantGuardService, TenantRateLimiter, TenantTokenMeter, TokenBudgetPolicy.

**Wired directly by orchestration/routes/workers (no DI, module import)** — FACT: heuristic_intent_classifier (understand.py), multi_query_expansion + adaptive_rerank_weight + vocabulary_expander + superlative_context_enricher + query_intent_extractor (retrieve.py / query_graph.py), structured_output_helper, step_tracker (workers + routes), tenant_analytics_service (admin_analytics), crm_analytics_service (crm route), faq_candidate_service (admin_refuse_suggestions), webhook_secret_rotation (admin_webhooks), jwt_token_service (app + middleware), cost_cap_alerter (embedded_workers), google_link_service (sync route + worker), audit_log_hasher (audit_chain_writer), narrate_* + chunk/contextual enrichers + content_type_router + parsed_md_dump + structured_ref_extractor (document_service — out of scope but confirmed callers).

**Orphaned / built-but-not-wired** — FACT:
- `persona_quality_gate.py` — zero production callers (grep: only its own file + `tests/unit/test_persona_quality_gate.py`). The dashboard-warning feature it documents does not exist.
- `ragas_metric_adapter.py` — stub only, dev script consumer (by design, but the "real ragas provider" was never registered).
- `query_graph.py:238` `_SUPERLATIVE_ENRICHER = _SuperlativeContextEnricher()` — module-level instance with only ONE grep hit (its own definition); the live path uses `retrieve.py:1870` per-language factory. Dead object + misleading (fixed-vi).
- `token_budget.py:11-13` — `soft_warn_ratio=0.8` stored in `self._soft` and **never read**; the soft-warn feature was never implemented (also an inline-hardcode).
- The commented dead modules listed above.

---

## Part 3 — Findings

### F1 — Heuristic intent classifier: locale support built-but-not-wired (vi-only in production) — FACT · HIGH · axis: multi-bot/multi-locale/T1

`heuristic_intent_classifier.classify_heuristic(query, *, signals)` was refactored to take a per-locale `RoutingSignals` pack (heuristic_intent_classifier.py:90-121, docstring: "a non-Vietnamese bot classifies on ITS locale's patterns"). The ONLY production call site is `orchestration/nodes/understand.py:117`:

```python
_h_result = _classify_heuristic(state.get("query") or "")
```

— no `signals` argument → `_DEFAULT_SIGNALS = get_routing_signals(DEFAULT_LANGUAGE)` (heuristic_intent_classifier.py:67, `DEFAULT_LANGUAGE="vi"` at `shared/constants/_02_per_intent_rerank_skip_gate_.py:230`). **Every bot on the platform, regardless of `bots.language`, is Layer-1 classified against the Vietnamese regex seed.** Failure scenario: an EN/JP bot's greeting/chitchat patterns never fast-path (perf loss ~1.6s p50 on those turns) and, worse, an EN query that happens to contain a substring matching a vi mid-string aggregation/comparison pattern gets a wrong intent hint at confidence 0.85. The DB-hydrated language-pack `RoutingSignals` path exists but is never threaded from bot config into the node.

### F2 — SuperlativeContextEnricher parser is happy-case-only (VN currency format, no unit conversion, cross-chunk misattribution) — FACT · HIGH · axis: multi-doc/multi-locale/T1-smartness

Three independent defects in `superlative_context_enricher.py`:

1. **Price regex is VN-format-only** (`:243-246`): `(?P<price>\d{1,3}(?:[.,]\d{3})+)\s*(?:đ|đồng|VND)?` requires thousand-separator groups. Plain `500000`, `1,5 triệu`, `$49.99`, `1.2M`, or any price < 1000 NEVER parses. Despite the `en` intent pack (`:107-152`) letting an English bot *detect* "most expensive", `parse_chunks` can rarely extract anything from an EN/USD corpus → enrichment silently empty (state unchanged, no log).
2. **Duration units not converted** (`:261-269`): `(?P<duration>\d+)\s*(?:phút|giờ|buổi)` stores the bare number into `duration_minutes` for ALL units. Failure scenario: corpus "Gói A 2 giờ" + "Gói B 90 phút" → `longest_duration` ranks B (90) over A (should be 120) → the LLM receives a pre-sorted context asserting the WRONG longest item. That is application-produced misleading context feeding the answer (HALLU-misinterpret class). Also units are VN-only — "90 minutes" never parses.
3. **Discount/bonus cross-chunk conflation** (`:231`, `:279-284`, `:293-299`): `items` dict persists across the chunk loop; a `giảm 30%` in chunk N with no items parsed in chunk N attaches to the LAST item inserted from chunk N-1 (`list(items.keys())[-1]`). Failure scenario: chunk 1 = "Gói VIP: 1.500.000đ", chunk 2 = promo text "Giảm 30% cho khách mới" about a different service → context_base says VIP has 30% discount.

Wired live at `nodes/retrieve.py:1868-1875` with per-bot language — so the intent gate is multi-locale but the extraction layer is not.

### F3 — structured_ref_extractor: Vietnamese keyword list hardcoded in a structure-deciding path — FACT · MEDIUM-HIGH · axis: multi-format/multi-locale

`structured_ref_extractor.py:30-45` compiles `Điều/Khoản/Mục/Phụ lục/Chương` literals in code. The docstring claims domain-neutral, but this is exactly the "hardcoded single-language word list in a structure-deciding path" the multilingual-no-vocab rule bans: an English/French/Chinese legal or policy corpus ("Article 3", "Chapter II", "第3条") gets zero `article_no/chapter_no` metadata → the metadata pre-filter feature (retrieve-side "Điều 3" fast path) is silently unavailable for every non-VN tenant. Correct home per platform rules: language packs / per-bot `custom_vocabulary`-style config keyed by locale.

### F4 — SystemConfigService: unguarded Redis on the hottest config path — FACT · HIGH · axis: T2-robustness

`system_config_service.py:79` (`get`), `:93` (cache set), `:242` (delete after write) call Redis with **no try/except**. Contrast with `get_many` which explicitly catches `RedisError` (`:154-158`). `SystemConfigService.get` is the SSoT read used by resolvers, notify resolver, slot extractor, boot config, etc. Failure scenario: Redis blip/outage → `RedisError` propagates from every `get()` → services that did not wrap the call (e.g. `model_resolver._system_config_default_model` service.py:156; `notify_channel_resolver.resolve` :74) crash instead of degrading to the DB that is still up. This violates the stated graceful-degradation pattern (transport error → degrade silent). Also: `row is None` (missing key) is NOT negative-cached (`:89-90`) → every read of an unset key hits the DB (minor perf).

### F5 — resolve_embedding lacks the system_config fallback the other two kinds have — FACT · MEDIUM · axis: multi-bot

`model_resolver/service.py:400-433`: no binding → `raise InvariantViolation` (`:411-412`). `resolve_llm` (`:203-225`), `resolve_reranker` (`:250-269`) and `resolve_runtime` (kind-matched map `:459-494`) all fall back to `system_config.*_model`. This breaks the mandated "per-bot binding → system_config + ai_models → NullObject" chain (memory: feedback_resolver_must_fallback_system_config — "Đã lặp lại nhiều lần") for exactly one kind. Callers:
- `document_service/__init__.py:387` — wrapped in its own `except Exception → system_config fallback` (mitigated locally).
- `interfaces/http/routes/admin_refuse_suggestions.py:167` — **unguarded**: a binding-less bot → InvariantViolation → HTTP 500 on the refuse-suggestions admin endpoint. Concrete failure: any tenant relying on the platform-default embedding model cannot use the FAQ-candidate feature.

### F6 — GENERIC_VOCABULARY duplicate key `"kh"` — FACT (python-verified) · MEDIUM · axis: T1-smartness

`vocabulary_expander.py:54` (`"kh": ["không"]`) and `:81` (`"kh": ["khách hàng"]`) — Python dict literal: last wins. Verified: `duplicate keys: ['kh']`. The teencode negation "kh" (extremely common: "kh có", "dịch vụ này kh?") now expands to "khách hàng" (customer). Failure scenario: query "spa kh nhận trẻ em à?" → context hint tells the LLM the token means "customer" → comprehension hint inverts a negation. Silent, no lint gate caught it.

### F7 — ModelResolver invalidation doesn't clear the runtime L1/L2 caches — FACT · MEDIUM · axis: multi-bot/T2

`_cache_mixin.py:90-103` `invalidate()` filters only `self._mem` (bindings cache). It does **not** touch `self._l1` (runtime cache keyed `model_runtime:{tenant}:{bot}:{purpose}`, TTL `DEFAULT_MODEL_RESOLVER_L1_TTL_S=60` at `constants/_05_embedding_circuitbreaker.py:38`) nor the Redis L2 written at `service.py:543-547`, despite its own docstring "Clear caches (in-process + Redis prefix)". `AIConfigService.create/update/delete_binding` → `_safe_invalidate` (ai_config_service.py:730/768/803) therefore leaves `resolve_runtime` consumers on the OLD model/params until TTL. Also: provider/model updates call only in-process `invalidate_all` (`:276,305,370,441,606,638`) — the chat_worker process (separate PID) never sees that invalidate; only the `BotConfigUpdated` outbox on binding-create covers cross-process, and only for bindings. Failure scenario: admin rotates a binding to a new model; the worker keeps answering with the old model for up to L1/L2 TTL, and the admin-visible `effective_config` (API process) disagrees with the worker.

### F8 — Four parallel API-key/config resolution stacks; reranker encrypted-key path unimplemented — FACT · MEDIUM-HIGH · axis: T3-design with T2 blast radius

Key material is resolved via four disjoint mechanisms:
1. `ai_providers.api_key_ref` (env-var name) + `ai_providers.api_key_encrypted` — `reranker_resolver.py:56-77, 293-311`. The encrypted branch is a stub: `"AES decrypt path not implemented; fall back to NullReranker"` (`:298-304`) → **a bot whose reranker key exists only encrypted silently loses reranking** (logged, but degrade-to-RRF is the exact silent-fallback class banned in memory feedback_v2_bug_lessons).
2. `ai_providers.metadata.api_key_encrypted` + `credentials_vault_path` via `SecretsPort` — `_binding_mixin.py:157-186` (`resolve_runtime` path).
3. `api_keys` table (dual-read encrypted/plain, 30s Redis) — `provider_key_resolver.py:100-169`.
4. `ai_keys` table (alembic 0066) — `ai_config_service.add_key/verify_key/list_keys` (`:372-508`).

Rotating a key in one store does not affect adapters reading another; ops must know which adapter reads which. Additionally the reranker stack duplicates the model_resolver reranker resolution (`resolve_reranker` + `RerankerResolver`) with different fallback semantics and different column names (`b.record_model_id`/`m.record_provider_id` vs `b.model_id`/`m.provider_id` port fields) — two sources of truth for "which reranker does this bot use".

### F9 — TenantTokenMeter checkpoint can regress after Redis eviction — FACT (code logic) / HYPOTHESIS (frequency) · MEDIUM-LOW · axis: multi-tenant/T2

`tenant_token_meter.py:182-185`: `ON CONFLICT … DO UPDATE SET prompt_tokens = EXCLUDED.prompt_tokens` overwrites the DB checkpoint with the current Redis totals. The evict-recovery path (`get_monthly_usage` `:253-269`) only restores when the Redis hash reads zero AND is only exercised by `check_token_cap`; for a tenant with `cap=None`, `check_token_cap` short-circuits at `:289-292` **without ever reading/restoring**, while `increment_tokens` (`:142-147`) happily checkpoints the small post-evict counters over the large pre-evict checkpoint. Failure scenario: tenant at 900k tokens (checkpointed), Redis evicts under memory pressure, next turns re-count from ~500; ops later sets a monthly cap → gate sees ~500 used. Fix shape: `GREATEST(tenant_token_usage.prompt_tokens, EXCLUDED.prompt_tokens)` within the same period.

### F10 — SlotExtractor prompts hardcoded in Vietnamese; anthropic-assumed alias resolution — FACT · MEDIUM · axis: multi-locale/multi-bot

`slot_extractor.py:39-47` (`_EXTRACT_SYSTEM_PROMPT` — "Bạn là slot extractor…"), `:119` ("bắt buộc"/"tùy chọn"), `:123-129` (user prompt in Vietnamese). A non-VN bot's slot extraction runs with Vietnamese instructions over, say, English user messages — extraction quality for EN/other-locale tenants is untested/degraded and the prompt is not language-pack-sourced (violates language-as-data). Also `_resolve_model` (`:168-183`) is an inline `if/elif` alias ladder that defaults unknown aliases to the anthropic provider (`:183`) rather than resolving via the DB model registry.

### F11 — Refusal text can still originate from hardcoded i18n.py — FACT (chain) · MEDIUM · axis: CLAUDE.md Application MINDSET #3

`oos_template_resolver.py:136-138` (tier 6) → `language_pack_service.get(locale, "refuse_message")` → on DB miss `language_pack_service.py:129-131` falls into `_inmemory_fallback` (`:170-197`) which reads `shared/i18n.py` hardcoded text. CLAUDE.md MINDSET #3: "Refusal text origin: bots.oos_answer_template … KHÔNG fallback i18n.py hardcoded text — empty string nếu bot không set." The chain is documented as a "boot-time DB outage guard" (language_pack_service.py:16), but functionally an unseeded/blank `language_packs` deployment serves platform-authored refusal prose the bot owner never wrote. Design tension to adjudicate: either exempt `refuse_message` from the i18n fallback or get an ADR.

### F12 — google_link_service: Vietnamese-only errors + locale-fragile access sniffing — FACT · MEDIUM-LOW · axis: multi-locale/multi-format

- All user-facing validation errors are Vietnamese literals (`google_link_service.py:63,74,81,85,92-119`) — a headless B2B API returning vi strings to EN consumers; not language-pack-driven.
- `_PRIVATE_INDICATORS` (`:42-47`) matches only EN+VN Google HTML phrases and `_check_access` (`:274-277`) checks `"sign in"`/`"đăng nhập"` — Google serves its interstitials in the request's locale; a doc probed via a server whose Google locale is neither → "private" misread as `public`/`unknown`, mis-signaling upload validity (worker fetch will still fail later — degraded UX, not data loss).

### F13 — multi_query_expansion: prompt `.format(n=…)` outside the failure guard — HYPOTHESIS · LOW-MEDIUM · axis: T2-robustness

`multi_query_expansion.py:388`: `prompt = _template.format(n=n_paraphrases)` — the template comes from admin-editable `language_packs`. If a seeded/edited template contains any literal `{`/`}` (e.g. a JSON output example — common for "return a JSON array" prompts), `str.format` raises `KeyError/ValueError` OUTSIDE the try at `:394` → the expand node raises instead of degrading to `[query]`. Not verified against current seeds (20260627/0099 seeds not audited here); flagged because every other failure path in this function is deliberately fail-soft.

### F14 — `DEFAULT_RERANKER_EMBEDDING_DIM` (=1024) used as the embedding-dimension fallback — FACT (code) / PLAUSIBLE (impact) · MEDIUM · axis: multi-bot

`constants/_16_prompt_token_squeeze.py:168` defines `DEFAULT_RERANKER_EMBEDDING_DIM=1024`; it is the default `EmbeddingSpec.dimension` at `model_resolver/service.py:429`, `_binding_mixin.py:131-133`, `_helpers.py:215`. A bot binding lacking `extra_params.dimension` silently gets 1024 — the platform's live embedding is ZE zembed-1 @1280 matryoshka (memory: feedback_retrieval_lessons_20260514: "phải pass dimensions:1280"). Failure scenario: new bot binding seeded without the extra_param → embed requests at 1024-dim vs a 1280-dim pgvector column → insert/query dimension mismatch errors or silent wrong-space vectors depending on adapter. Also a naming smell (reranker constant governing embedding behaviour).

### F15 — Audit verifier vs global backfill chain — HYPOTHESIS · LOW · axis: multi-tenant/forensics

Writer chains per-tenant (`audit_chain_writer.py:59-78`: tail `WHERE record_tenant_id = :tid … FOR UPDATE`; NULL-tenant rows a separate segment). `AuditVerifier` recomputes per-tenant from `prev_hash=""` (`audit_verifier.py:112-144`). Its own comment says the on-disk chain from the alembic-010g backfill was **global** ("rn ordered across all tenants in the backfill", `:56-59`). If any pre-migration rows exist, a tenant-scoped verify would flag the entire backfilled prefix as mismatched (each stored hash chains to a global, cross-tenant predecessor). Not verified against a live DB — needs `SELECT` evidence before claiming false positives in production.

### F16 — AIConfigService key verification: only Jina implemented; others silently "verified" — FACT · LOW-MEDIUM · axis: multi-bot/happy-case

`_KEY_VERIFY_REGISTRY` (`ai_config_service.py:161-163`) registers only `jina_ai`. `verify_first=True` for any other provider dispatches `_skip_verify` (`:150-155`) which returns `ok=True, "skip_unsupported_provider"` — the admin believes the key was verified when nothing was tested. Also `_curl_verify_jina_rerank` (`:110-144`) + `DEFAULT_JINA_API_BASE_URL`/`DEFAULT_JINA_RERANKER_MODEL` constants keep a provider-specific code path warm while the fleet has moved to ZeroEntropy (memory: zeroentropy ship 2026-05-12) — verification coverage for the ACTIVE provider is zero.

### F17 — Zero-hardcode drift cluster — FACT · LOW-MEDIUM each · axis: CLAUDE.md compliance

- `heuristic_intent_classifier.py:127,129` — confidence `0.90`/`0.85` inline (behaviour-tuning numbers, not whitelisted).
- `provider_key_resolver.py:37` — `_CACHE_TTL_S = 30` inline.
- `chunk_context_enricher.py:58` — `_CHUNK_CONTEXT_STORAGE_LIMIT_CHARS = 1024` inline (tied to DDL, but should live in constants next to the alembic reference).
- `jwt_token_service.py:141,232` — `"iss": "ragbot"` literal while `_decode` verifies against imported `JWT_ISSUER` (`:96`) — a change to the constant would break every newly minted token; also `"ragbot-owner"`/`"owner"`/`"service"` role strings inline (`:444-468`).
- `retry_policy.py:35-39` — `RetryPolicy` defaults (3/100/10_000/2.0) inline while `CircuitBreakerPolicy` correctly uses constants.
- `tenant_analytics_service.py:44-51` — module-level thresholds with a "lift to constants.py later" note (agent-collision workaround) never lifted; plus `Any` used without import (`:327,345` — harmless at runtime under `from __future__ import annotations`, latent NameError under `typing.get_type_hints`, verified not imported).
- `crm_analytics_service.py:266` — `alert_pct or 80` inline fallback.
- `bot_management_service.py:366` — outbox subject literal `"bot.registry.changed.v1"` inline instead of a `SUBJECT_*` constant (peers use constants: `SUBJECT_BOT_PURGED`, `SUBJECT_TOKEN_REVOKED`).
- `bot_registry_service.py:56` — `_reload_debounce_sec: float = 1.0` inline.

### F18 — Application-layer imports of infrastructure — FACT · LOW · axis: T3 (hexagonal)

`bot_management_service.py:30-32` imports `infrastructure.db.uow`, `infrastructure.repositories.audit_chain_writer`, `infrastructure.repositories.bot_repository`; `tenant_config_cache.py:45`, `tenant_analytics_service.py:28`, `cost_cap_alerter.py:29-30`, `ingest_idempotency_service.py:55`, `ai_config_service.py:24` similarly import `infrastructure.db.models*`. Meanwhile `bot_lifecycle_service.py:110-114` explicitly documents the boundary rule ("application/ MUST NOT import infrastructure/ — gate tests/unit/test_hexagonal_boundary.py") and injects everything. The boundary is inconsistently enforced — either the gate has broad exemptions or these files pre-date it. Not a runtime bug; it blocks the ports-and-adapters swap story for those services.

### F19 — Dead code inventory / cleanup debt — FACT · INFO

Fully-commented dead modules: `boilerplate_resolver.py`, `cag_service.py`, `google_sheets_test_fetcher.py`, entire `multi_agent_review/` (7 files), `model_resolver` `resolve_fallback_chain`/`resolve_prompt` (service.py:707-755). Live-but-orphaned: `persona_quality_gate.py` (zero prod callers), `query_graph.py:238` unused `_SUPERLATIVE_ENRICHER`, `token_budget.soft_warn_ratio` (stored, never read). The mixin split (`_cache_mixin`/`_binding_mixin`/`_helpers`) triple-duplicates ~70 lines of identical imports + the `_ENRICHMENT_INTENTS` frozenset (service.py:91, _binding_mixin.py:78, _cache_mixin.py:78) — drift risk if one copy is edited.

### F20 — Positive verification (things that are correctly built) — FACT

- 4-key identity is honoured end-to-end in the registry (`bot_registry_service._key` :61-74; poison-row eviction :184-193) and management service (CrossTenantForbiddenError :119-123; tenant-scoped repo calls).
- Reranker chain per-bot→system_config→Null with loud logging on drift (`reranker_resolver.py:260-274`) implements the memory-mandated fallback.
- Guardrail loader's tenant-override-wins merge (`guardrail_rule_loader.py:237-253`) and compile-failure skip are correct multi-tenant behaviour.
- SysPromptAssembler append-only + dual addressing opt-out (numbered + `# HEADER`, `sysprompt_assembler.py:72-76, 206-242`) matches the ADR-W1-S10 pins; seeds (alembic 20260627/20260701) start with `\n\n#` so they are strippable. (Un-verified corner: a base-seed FIRST rule not preceded by `\n\n` would be un-strippable — HYPOTHESIS, low.)
- `structured_output_helper` capability-driven mode select + bounded repair is solid and domain-neutral.
- Audit hash chain writer/verifier are bit-stable by construction and tenant-scoped (F15 caveat aside).
- `webhook_secret_rotation` (hash-only storage, grace-window verify, tenant-scoped) and `ingest_idempotency_service` (tenant-scoped unique, expired-row retry) are well-designed.

---

## Part 4 — Owner's #1 concern: happy-case-only synthesis

The engine skeleton (registry, resolvers, caches, guardrails, audit) is multi-tenant-correct. The **content-understanding periphery is where happy-case assumptions concentrate**, and they share one root cause: *language/format treated as code, not data*:

1. `understand` Layer-1 intent → vi regex for all bots (F1).
2. Superlative parsing → VN currency/duration shapes only, no unit normalisation, cross-chunk attach (F2).
3. Structured-ref metadata → VN legal keywords only (F3).
4. Slot extraction prompts → Vietnamese only (F10).
5. Google-link errors/access-detection → vi/en page-phrase sniffing (F12).
6. Chunk-quality scorer word-count component breaks for unspaced scripts (whitespace `split()` — contextual_chunk_enrichment.py:309-311; CJK chunk = 1 "word" → always fails the band; observability-only). — FACT, low.

Each degrades **silently** (empty enrichment/metadata, no per-bot signal), which is precisely the "silently degrades otherwise" failure mode the owner flagged. The pattern fix is the one already used by `vocabulary_expander`/`superlative` intent gate: locale packs + per-bot config, threaded from `bots.language` — extend it to F1/F2-parsing/F3/F10.

## Part 5 — CLAUDE.md compliance summary for the scope

| Rule | Verdict | Evidence |
|---|---|---|
| Zero-hardcode | ⚠️ mostly good; drift cluster F17 | see F17 lines |
| Domain-neutral (brand/tenant) | ✅ no brand/tenant literals found in scope | grep + read |
| Language-neutral | ❌ six vi-baked paths | F1,F2,F3,F10,F12 + word-count |
| No version-ref | ✅ in code names; event subjects use `.v1` wire convention (accepted pattern, constants exist) | bot_management_service.py:366 literal noted |
| Strategy+DI | ✅ crag_grader/narrate/chunk-context exemplary; ⚠️ slot_extractor alias ladder (F10), reranker dual-stack (F8) | |
| Broad-except policy | ✅ nearly all `# noqa: BLE001` sites carry reasons and sit at legit boundaries | e.g. step_tracker.py:129, batch_grader.py:157 |
| App KHÔNG inject/override answer | ✅ enrichers write `context_base` hints only, explicit non-injection notes | vocabulary_expander.py:489-494, superlative :379 |
| 4-key identity | ✅ | F20 |
| Tenant isolation | ✅ scoped queries throughout; `all_tenants_summary` correctly RBAC-delegated | tenant_analytics_service.py:486-491 |
