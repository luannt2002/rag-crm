# DB Schema & Migration Mindset

> Canonical reconstruction of the Ragbot Postgres schema **as it stands at alembic head `0235`**, plus the architectural narrative behind the 273-migration history.
> Sources: `src/ragbot/infrastructure/db/models.py` (+ `models_monitoring.py`, `models_invocation.py`, `models_guardrail.py`) for ORM-managed tables, and `alembic/versions/*.py` raw-SQL migrations for the pgvector / cache / stats / RBAC / guardrail tables. Every claim is cited to a migration number.

**Schema authority split (important):**
- Migration `0001` runs `Base.metadata.create_all` — so the **v0 + AI-config core** tables are defined by the SQLAlchemy ORM, then evolved by later raw-SQL `ALTER`s.
- `document_chunks`, `semantic_cache`, `document_service_index`, `language_packs`, `system_config`, `guardrail_rules`, `api_keys`, `tenant_webhooks`, etc. are **raw-SQL only** (no full ORM model — `document_chunks` has a minimal `Table` shim in `models_monitoring.py` so FK references resolve).
- The high-throughput `document_chunks` INSERT path is owned by `infrastructure/vector/pgvector_store.py` (raw SQL), not the ORM.

---

## 1. Current schema (per table)

### Identity & tenancy core

#### `tenants`
- **Purpose:** root multi-tenant entity; quota + callback + CORS + rate-limit defaults.
- **Key columns:** `id UUID PK`; `name VARCHAR(255) UNIQUE`; `quota_monthly_tokens BIGINT`; `monthly_token_cap INT NULL`; `rate_limit_per_min INT NULL` (NULL=inherit system_config); `bypass_rate_limit BOOL`; `allowed_origins JSONB`; `callback_url`, `callback_hmac_secret`; `config JSONB`; soft-delete `deleted_at`.

#### `workspaces` (entity — additive, alembic 0199)
- **Purpose:** first-class workspace row anchoring RBAC/quota/lifecycle for a `workspace_id` slug. **Does NOT** join the 4-key identity tuple (ADR-W2-D2 rejected adding `record_workspace_id`).
- **Key columns:** `id UUID PK`; `record_tenant_id UUID FK→tenants CASCADE`; `slug VARCHAR(64)` (= `bots.workspace_id`); `name`; `deleted_at` (soft-delete only).
- **Constraint:** `uq_workspaces_tenant_slug (record_tenant_id, slug)`. RLS ENABLE+FORCE on `record_tenant_id`. Backfilled from distinct `(record_tenant_id, workspace_id)` on `bots`.

#### `bots` — the 4-key identity anchor
- **Purpose:** per-bot config; single source of truth for `system_prompt`, limits, per-bot toggles.
- **4-key identity columns:** `record_tenant_id UUID FK→tenants RESTRICT`, `workspace_id VARCHAR(64)`, `bot_id VARCHAR(64)`, `channel_type VARCHAR(32)` — all NOT NULL.
- **UUID FKs:** `id UUID PK` (= `record_bot_id` everywhere else); `record_model_id`, `record_embedding_model_id` (nullable).
- **Config columns:** `system_prompt TEXT`, `setting_options JSONB` (temperature/top_p/max_tokens/penalties), `custom_vocabulary JSONB`, `plan_limits JSONB`, `threshold_overrides JSONB` (per-bot resolve chain top tier), `oos_answer_template VARCHAR(1000)` (refusal text origin — NOT i18n fallback), `language VARCHAR(8)`.
- **Limit columns:** `max_history`, `max_documents`, `prompt_max_tokens`, `rerank_top_n`; token-quota (alembic 0100): `tokens_used BIGINT`, `extra_max_tokens BIGINT`, `extra_output_tokens_per_response INT`, `bypass_token_check BOOL`; `bypass_token_limit`, `bypass_rate_limit`.
- **Feature-config JSONB (nullable, default OFF):** `rerank_intent_whitelist` (Phase 14), `action_config` (slot-filling, alembic 0150), `metadata_extraction_config` (alembic 0162).
- **Constraints:** `uq_bots_record_tenant_workspace_bot_channel(record_tenant_id, workspace_id, bot_id, channel_type)`; `ck_bot_id_not_empty`. Indexes `ix_bots_record_tenant_bot_channel`, `ix_bots_model`.

#### `conversations` / `messages`
- **`conversations`:** `id UUID PK`; `record_tenant_id`, `workspace_id`, `record_bot_id FK→bots CASCADE`; external `connect_id VARCHAR(255)`; `rolling_summary TEXT`, `turn_count`, `last_message_at`. Unique `uq_conv_bot_connect(record_bot_id, connect_id)`.
- **`messages`:** `id UUID PK`; `record_conversation_id FK→conversations CASCADE`; `record_tenant_id`, `workspace_id`, `record_bot_id`; `role`, `content TEXT`, `citations JSONB`, `tokens_used`, `cost_usd NUMERIC(10,6)`, `status`. Indexes on `(record_conversation_id, created_at)` and `(record_tenant_id, record_bot_id)`.

#### `documents`
- **Purpose:** one row per ingested source doc.
- **Key columns:** `id UUID PK`; `record_tenant_id`, `workspace_id`, `record_bot_id` (channel_type NOT persisted — `record_bot_id` is 1:1 with the external triple); `source_url TEXT`, `document_name`, `tool_name`, `mime_type`, `language VARCHAR(8)`, `state VARCHAR(32)` (default `active`), `version INT`, `content_hash`, `acl ARRAY(String)`, `content_chars INT`, `raw_content TEXT` (pre-chunk source for BM25/audit; nullable until re-ingest), `summary_json JSONB` (aggregate blob — alembic 0118).
- **Constraint:** `uq_doc_tool(record_tenant_id, record_bot_id, tool_name)`. Indexes on `record_bot_id`, `state`, `created_at`.
- *Dropped in 0010:* `authority_score / valid_from / valid_until / superseded_by` (advanced features never wired).

### Retrieval core

#### `document_chunks` (raw SQL — created alembic 0013, "thay Qdrant")
- **Purpose:** pgvector chunk store; hybrid retrieval (dense HNSW + BM25 GIN).
- **Current columns (post-all-migrations):**
  - `id UUID PK`; `record_document_id UUID FK→documents CASCADE` (renamed from `document_id` in 0034); `record_bot_id UUID FK→bots CASCADE` (denormalized in 0108 for RLS + selectivity); `chunk_index INT`; `content TEXT`; `content_hash`; `metadata_json JSONB`.
  - `embedding vector(1024)` — **final dim after 0228** (see Wave 1 below).
  - `search_vector tsvector` — BM25 column, trigger-maintained (alembic 0028).
  - `content_segmented TEXT` — VN word-segmented BM25 source, preferred over `content` by the trigger when non-NULL (alembic 0046).
  - `chunk_chars INT` (alembic 0031); `parent_chunk_id UUID` self-FK SET NULL (alembic 0023); `chunk_type VARCHAR(32)` default `'text'`, CHECK in (text/table/table_row/code) (alembic 010k); `chunk_context VARCHAR(1024)` situated-context string, Anthropic Contextual Retrieval (alembic 010l); `doc_deleted_at TIMESTAMPTZ` denormalized from `documents.deleted_at` via trigger (alembic 010p).
- **Indexes:** `ix_chunks_embedding_hnsw` HNSW `vector_cosine_ops` **m=32, ef_construction=200** (created 0013 at m=16, rebuilt 0051); `idx_chunks_search_vector` GIN tsvector (0028); `idx_chunks_search_vector_combined` GIN functional over `content || chunk_context` (010n); `ix_chunks_chunk_context_trgm` GIN pg_trgm (010l); `ix_chunks_type`; partial `idx_chunks_parent`; `ix_chunks_bot`, `ix_chunks_bot_doc`, partial `ix_chunks_bot_active WHERE doc_deleted_at IS NULL`.
- **Triggers:** `trg_chunk_search_vector` (BEFORE INS/UPD → `coalesce(content_segmented, content)` → tsvector); `trg_sync_doc_deleted_at` (propagates `documents.deleted_at`).
- *Dropped:* `bot_id`/`tenant_id` (added 0013, dropped 0033 — redundant via doc FK).

#### `semantic_cache` (raw SQL — created alembic 0014)
- **Purpose:** tier-2 semantic answer cache (pgvector cosine over question embedding).
- **Current columns:** `id UUID PK`; `record_bot_id UUID` (renamed from `bot_id` 0034); `record_tenant_id UUID NULL`; `bot_version`, `corpus_version` (cache-bust keys); `query_embedding vector(1024)` — **final dim after 0235**; `query_hash`; `answer TEXT`; `citations JSONB`; `model_name`; `cached_at_ts BIGINT`; `expires_at` (TTL).
- **Indexes:** `ix_semantic_cache_qe_hnsw` HNSW cosine m=32 ef=200; `ix_semantic_cache_versions(record_bot_id, bot_version, corpus_version)` (010c); `ix_semantic_cache_bot`.

#### `document_service_index` (raw SQL — created alembic 0118, "Stats Index")
- **Purpose:** one row per extracted entity (service/product/item) from a table/CSV chunk → enables deterministic SQL count/range queries that bypass the top_k vector cap. **HALLU=0 preserved** (count is Python SQL, not LLM).
- **Key columns:** `id UUID PK`; `record_tenant_id`, `workspace_id`, `record_bot_id FK→bots CASCADE`, `record_document_id FK→documents CASCADE`, `record_chunk_id FK→document_chunks SET NULL`; `entity_name TEXT`, `entity_category TEXT` (opaque, domain-neutral — no spa/service literals); `price_primary NUMERIC`, `price_secondary NUMERIC`; `attributes_json JSONB`.
- **Indexes:** `idx_dsi_bot_price1(record_bot_id, price_primary)`, `idx_dsi_bot_price2`, `idx_dsi_doc`, `idx_dsi_attrs` GIN. RLS tenant-scoped.

#### `request_chunk_refs` (raw SQL — created alembic 0109, "G15")
- **Purpose:** relational split of the old `request_logs.retrieved_chunks` JSONB (dropped in 0109 — held no FK, bloated logs ~16MB/10k req/day, leaked PII preview).
- **Columns:** `id UUID PK`; `record_request_id FK→request_logs CASCADE`; `record_chunk_id FK→document_chunks CASCADE`; `rank INT`; `score NUMERIC(8,6)`. Indexes on both FKs.

#### `knowledge_edges` (raw SQL — created 0022, altered 0037)
- **Purpose:** GraphRAG subject-relation-object triples per bot.
- **Columns (post-0037):** `id UUID PK`; `record_bot_id FK→bots CASCADE`; `channel_type VARCHAR(64)` (added 0037); `subject TEXT`, `relation TEXT` (renamed from `predicate`), `object TEXT`; `source_document`, `source_chunk_id`, `confidence FLOAT`. Unique `(record_bot_id, channel_type, subject, relation, object)`.

### AI configuration

#### `ai_providers`
- **Purpose:** LLM/embedding/rerank provider registry (config-driven, no per-brand literals in code).
- **Key columns:** `id UUID PK`; `name UNIQUE`, `type`, `base_url`, `auth_type`; `code`, `api_key_ref`, `api_key_encrypted`; timeouts/retries/concurrency; `requires_prefix BOOL` (alembic 010e — TRUE emits `{code}/{model_name}` for Cohere/Jina/Voyage, FALSE for OpenAI/Anthropic native — replaced a per-brand literal in `model_resolver`); `enabled`, soft-delete.
- *Dropped 0010:* `credentials_vault_path`.

#### `ai_models`
- **Purpose:** per-provider model catalogue + pricing + capability flags.
- **Key columns:** `id UUID PK`; `record_provider_id FK→ai_providers CASCADE`; `name`, `kind`, `model_id` (wire id); context/output windows; `input_price_per_1k_usd`, `output_price_per_1k_usd`, `input_price_per_1k_cached_usd`; `supports_streaming/tools/vision/json_mode/caching/reasoning`; `embedding_dimension INT NULL`; `quality_tier`; latency p50/p95. Unique `uq_ai_model_provider_name(record_provider_id, name)`. Index on `kind`.

#### `bot_model_bindings`
- **Purpose:** per-bot model selection by purpose (llm / embedding / rerank / grader / decompose…), with rank/variant/fallback for A-B + failover.
- **Key columns:** `id UUID PK`; `record_tenant_id`, `workspace_id`, `record_bot_id FK→bots CASCADE`; `purpose VARCHAR(32)`; `record_model_id FK→ai_models RESTRICT`; `rank`, `variant`, `weight`, temperature/max_tokens/top_p, `extra_params JSONB` (carries embedding `dimension` matryoshka knob); `record_fallback_model_id`, `record_prompt_template_id/version_id`; `active`, `effective_from/to`.
- **Constraint:** `uq_binding_unique(record_tenant_id, record_bot_id, purpose, rank, variant)`. Index `ix_binding_bot_purpose(record_bot_id, purpose, active)`.
- **Resolve mindset (sacred):** resolver must fall back to platform default `system_config + ai_models` when no per-bot binding — never NullObject silently. `purpose` value drift `'rerank'` vs `'reranker'` caused historical 0-chunk bugs.

#### `model_capabilities` (1:1 extends ai_models), `tenant_model_policy` (knowledge-source ratio + fallback; CHECK ratios sum=100), `prompt_templates`, `prompt_versions`, `model_invocations` — ORM-managed AI-config support tables.

### Observability & governance

#### `request_logs` (ORM `models_monitoring.py`)
- **Purpose:** one row per chat request (final aggregate). Privacy-2.B: stores **hashes not raw text**.
- **Key columns:** `request_id UUID PK`; identity `record_tenant_id`, `workspace_id`, `channel_type`, `connect_id`, `record_bot_id`, `record_conversation_id`, `message_id BIGINT` (upstream customer id), `trace_id`; `question_hash`, `answer_hash`, `refusal_reason`; routing `record_model_id`, `model_name`, `record_binding_id`; timing/token/cost; `status` (success|failed|timeout|moderated|refused); `citations JSONB`; quality `feedback_score`, `is_correct`, `feedback_comment`. (Inline `retrieved_chunks` JSONB dropped 0109 → `request_chunk_refs`.)

#### `request_steps` (ORM)
- **Purpose:** per-pipeline-step latency/token/cost (forensic = `audit_log`; per-step timing lives HERE).
- **Key columns:** `id UUID PK`; `record_request_id FK→request_logs CASCADE`; `record_tenant_id`, `workspace_id`, `channel_type`; `step_name` (router|rewrite|hyde|retrieve|rerank|grade|generate|reflect|narrate|embed|guardrail_input|guardrail_output…), `step_order`, `model_used`, timing/tokens/cost, `status`.

#### `audit_log` (ORM — unified, alembic 0010)
- **Purpose:** admin RBAC forensic trail (replaces ai_config_audit_log + policy_audit_log).
- **Key columns:** `id UUID PK`; `record_tenant_id`, `workspace_id`, `actor_user_id`, `action`, `resource_type`, `resource_id VARCHAR(128)`, `before_json/after_json JSONB`, `reason`, `trace_id`; `row_hash` tamper-detect chain `sha256(prev.row_hash || canonical_fields)` (alembic 010g). Indexed on `(record_tenant_id, resource_type, created_at)` + `(resource_type, resource_id, created_at)`.

#### `monitoring_log` (0217) / `token_ledger` (0226) / `token_budgets` (0219)
- **`monitoring_log`:** durable per-request billing mirror that **survives bot deletion** (NO FKs); BigSerial; carries 4-key + tokens + cost + status.
- **`token_ledger`:** per-call durable audit of EVERY token-spending action (LLM/embedding/rerank) across ingest + query; NO FKs; `mode` (ingest/query) + `action` + `purpose` + 4-key snapshot + token counts + unit-price snapshot + cost.
- **`token_budgets`:** per-level (tenant/workspace/bot) token+cost cap + alert threshold; UNIQUE on the scope tuple.

### Config / content / guardrails / webhooks

#### `system_config` (raw SQL — created alembic 0016)
- **Purpose:** runtime-tunable global knobs (Redis-cached). `key VARCHAR(128) PK`, `value JSONB`, `value_type`, `description`, `updated_at`.
- **Rows:** LLM params, RAG retrieval (top_k, rrf_k, reranker config), `embedding_provider` / `embedding_model` / `embedding_dimension`, chunking thresholds, `contextual_retrieval_enabled`, pipeline flags, CB/rate-limit settings. **Provider history visible here:** `embedding_provider` litellm → zeroentropy (0085) → jina (0228); `embedding_dimension` 1536 → 1280 → 1024.

#### `language_packs` (raw SQL — created alembic 0055)
- **Purpose:** locale-keyed prompt + i18n content. PK `(code, prompt_key)`; `content TEXT`, `version`.
- **Seeds:** 0056 (vi+en base: generator/grader/understand/condense/rewriter/reflector/decompose/multi_query_*), 0099 (VN normalization vocab), 0112/0114 (aggregation + money-norm rules), 0136 (refuse_message), 0137 (oos_template_system_default), **0146 (`sysprompt_default_rules` vi+en — platform-default, domain-neutral, the governed APPEND seed per ADR-W1-S10)**, 0163 (metadata_extract_prompt), 0220 (en multiquery parity).
- **⚠ Per-bot brand/PII flag:** several migrations (0147/0149/0151/0154/0156/0158/0172/0204/0227 + the spa-* series) `UPDATE language_packs WHERE code='vi'` or `bots.system_prompt WHERE bot_id='<demo-bot>'` carrying **demo-bot-specific spa content** (and historically phone/brand literals). These are tracked-migration content for the demo tenant only — **do not treat as platform schema; do not reproduce literals.** The governance rule (CLAUDE.md sacred #1) is that platform rules go into `sysprompt_default_rules` (domain-neutral), not per-bot UPDATEs.

#### `guardrail_rules` (raw SQL — created alembic 010f)
- **Purpose:** DB-driven input/output guardrail regex rules (platform-default + per-tenant override).
- **Key columns:** `id UUID PK`; `record_tenant_id` (NULL = platform default); `workspace_id` default `'system'`; `rule_id`, `pattern`, `pattern_flags`, `severity` (info/warn/block), `action_taken` (allow/redact/block/hitl), `scope` (input/output/both), `enabled`, `priority`. Partial-unique for platform-default vs per-tenant. Seeded 12 default rows; prompt-injection/DAN pattern patched in 0164.

#### `tenant_webhooks` + `tenant_webhook_secrets` (raw SQL — created alembic 010m)
- **`tenant_webhooks`:** `id UUID PK`; `record_tenant_id FK→tenants CASCADE`; `url VARCHAR(2048)`; `revoked_at`. (No `webhook_deliveries` table exists in the chain — that was a deferred design.)
- **`tenant_webhook_secrets`:** HMAC secret rotation with grace period; `version`, `secret_hash` (bcrypt), `grace_period_hours`, `revoked_at`. UNIQUE `(record_tenant_id, webhook_id, version)`.

#### Other tables (one-liners)
- **`api_keys`** (0086 hot-swap): provider key pool; `value_plain` nulled after AES-256-GCM encrypt to `value_encrypted` (0196/0197); `rotation_state` (live/cooldown/revoked); `fingerprint` in metadata.
- **`api_tokens`** (0017): service auth tokens; `token_hash`, `role`, `rate_limit_value/window` (0018).
- **`role_definitions`** + **`module_permissions`** (0036): DB-driven RBAC; numeric role levels (super_admin=100…guest=0); module+permission → min level (see `shared/rbac.py`).
- **`ingest_idempotency_keys`** (010j): BE-to-BE upload dedup; UNIQUE `(record_tenant_id, workspace_id, idempotency_key)` + `request_hash` for "same key, different payload" detection.
- **`event_inbox`** (0198): transactional inbox, exactly-once handler; composite PK `(subscriber_id, msg_id)`.
- **`bot_token_usage_log`** (0101): per-month token roll-up per bot (4-key); `usage_by_month JSONB`.
- **`refuse_suggestions`** (0064): active-learning refusal-frequency tracker per (bot, query_intent).
- **`message_feedback`** (0074): thumbs up/down ENUM signal.
- **`quotas`**, **`jobs`**, **`outbox`** (ORM v0): per-tenant quota counters, async job table, transactional-outbox for Redis Streams.

---

## 2. Major evolution waves (the mindset)

### Wave 0 — Foundation: ORM base + drop-the-cruft (0001–0011)
`0001` builds the entire v0 + AI-config schema from the ORM via `create_all`. `0010` (`cleanup_dead_tables`) is a deliberate **subtraction wave**: drops never-wired advanced columns (`documents.authority_score/valid_from/...`), merges three audit tables into one `audit_log`, drops `IntentRoute/BotAITool/golden_questions`. Mindset: *ship the minimum schema that the pipeline actually exercises; don't carry speculative columns.*

### Wave 1 — pgvector + the embedding-dimension saga (0013, 0014, 0050→0051, 0054→0063, 0085→0105, 0228→0235)
The single most-churned axis. pgvector replaced Qdrant (`0013`, "thay Qdrant"). The dimension history:

| Step | dim | Why (evidence) |
|---|---|---|
| 0013 / 0014 | **1024** | Initial declaration. |
| 0050 → 1536 | **1536** | Z1 audit P0: configured model `text-embedding-3-small` emits 1536 but columns were 1024 → fresh-DB ingest would crash `expected 1024, not 1536`. Idempotent, inspects `format_type` (an earlier draft mis-computed `atttypmod-4` — incident note in 0050). HNSW rebuilt m=16→m=32 (0051). |
| 0054 → parallel `embedding_v3 vector(1024)` | — | Jina v3 migration via **parallel column** (zero-downtime; per-bot binding picks the column). |
| 0063 drop+rename | **1024** | Consolidate to one column whose name reflects PURPOSE not version: `document_chunks.embedding`, `semantic_cache.query_embedding`. Runtime dim lifted from `EmbeddingSpec` per bot — *future swaps need no schema change.* Pre-checked 0 populated rows = loss-less. |
| 0085 → ZeroEntropy | **1280** | ZE `zembed-1` matryoshka (column reshaped, all embeddings NULLed — ZE vs Jina vectors not comparable; re-ingest mandatory). Seeds `embedding_provider="zeroentropy"`. (0105 fixes `semantic_cache` which 0085 missed → caused 0% cache hit, silently swallowed by broad-except.) |
| 0228 → Jina | **1024** | ZE→Jina switch: `jina-embeddings-v3` (1024-dim, multilingual, VN in top-30) + `jina-reranker-v3`. **Late-chunking** (cross-chunk context in the embedding pass, ZERO generative-LLM calls) replaces the O(n²) per-chunk nano Contextual-Retrieval enrichment — so `contextual_retrieval_enabled=false`. `document_chunks.embedding` 1280→1024 + HNSW rebuild. |
| 0235 → fix | **1024** | **0228 missed `semantic_cache.query_embedding`** (left 1280) → every cache write failed `asyncpg DataError: expected 1280, not 1024`. 0235 rebuilds it to the SSoT `DEFAULT_EMBEDDING_DIM = 1024`. |

Mindset: *an embedding-dim change is inherently a re-embed, never an in-place cast; column names encode PURPOSE not version; verify 0-rows before any drop/reshape; provider swap = config flip in `system_config` + binding repoint, not code change.*

### Wave 2 — BM25 Vietnamese hybrid retrieval (0028, 0046)
`0028` adds `search_vector tsvector` + GIN + trigger. `0046` adds `content_segmented` and **retargets the trigger** to prefer it: pre-segmenting VN compounds ("chăm sóc da" → "chăm_sóc da") at ingest fixes a tokenizer asymmetry where multi-word query compounds missed chunks indexed as loose tokens. Crucially **embedding still indexes raw `content`** (embedding models handle VN natively; underscores hurt cosine) — only the BM25 GIN sees the segmented form. Mindset: *fix retrieval bugs at the retrieval layer; keep dense and lexical representations independent.*

### Wave 3 — Chunking modality: parent/child, chunk_type, chunk_context (0023, 010k, 010l, 010n, 010p)
`0023` parent-child chunks (self-FK). `010k` lifts `chunk_type` out of `metadata_json` into a first-class CHECK-constrained column so modality-aware retrieve/rerank filters without JSONB parsing. `010l`+`010n` add `chunk_context` (Anthropic Contextual-Retrieval situated string) + a combined `content||chunk_context` GIN. `010p` denormalizes `doc_deleted_at` onto chunks (trigger-synced) so a partial index can skip soft-deleted docs cheaply. Mindset: *promote hot filter keys out of JSONB; denormalize for index selectivity, keep it consistent with a trigger.*

### Wave 4 — 4-key bot identity / workspace (0062, 0141, 0199)
`0062` lifts identity 3→4 key: adds `workspace_id VARCHAR(64)` to `bots` + 16 data tables, defaulting `workspace_id = record_tenant_id::text` (the null→default-workspace contract). `0141` makes RLS workspace-aware (GUC). `0199` adds the `workspaces` **entity** beside the slug — for RBAC/quota/lifecycle — *without* adding `record_workspace_id` to the identity tuple (ADR-W2-D2). Mindset: *the slug carries identity + data-scoping; the entity carries lifecycle — keep them separate, evolve additively, never gate the write path on a new NOT-NULL FK.*

### Wave 5 — Stats Index / structured-record aggregation (0118)
`document_service_index` + `documents.summary_json`: aggregation/range queries ("how many under 2tr?", "list all") recall only 28-40% because top_k=20 hides matching chunks. Industry pattern (Pinecone/AI21): parse table/CSV → structured rows → deterministic SQL count/filter. **HALLU=0 preserved** (count is Python, not LLM). Domain-neutral: `entity_name`/`entity_category` are opaque VARCHAR. Mindset: *don't fight a retrieval cap with sysprompt rules; add a deterministic structured path at the data layer.*

### Wave 6 — Sysprompt governance + language_packs (0055, 0056, 0146–0158, …)
Early per-bot rules were shipped via `alembic UPDATE bots.system_prompt WHERE bot_id='<demo>'` — an anti-pattern for multi-tenant scaling (N bots = N migrations). `0146` introduces `language_packs[locale][sysprompt_default_rules]` as the **governed APPEND** tier (ADR-W1-S10): domain-neutral rules auto-inherited by all bots of a locale, per-bot opt-out via `plan_limits.sysprompt_rules_disabled`. Mindset (CLAUDE.md sacred #1): *platform rules are domain-neutral and APPEND-only via tracked seed; per-bot brand text stays in the bot's own `system_prompt`/config — never injected by the application at answer-time.*

### Wave 7 — Provider bindings & reranker (010e, 0054, 0085, 0228)
`010e` adds `ai_providers.requires_prefix` to kill a per-brand literal in `model_resolver` (Cohere/Jina/Voyage need `{code}/{model}`, OpenAI/Anthropic don't). Embedding+rerank provider has cycled litellm → ZeroEntropy (0085) → Jina (0228), each time a `bot_model_bindings` repoint + `system_config` flip — **no orchestrator code change** (Port + Registry + DI). Mindset: *every swap-able thing is a config string; add a provider = add a Strategy file + a binding row.*

### Wave 8 — Security hardening: RLS, key encryption, HMAC, hash-chain (0069, 0073, 010g, 010m, 0187, 0196/0197)
`0069` enables RLS tenant isolation; `0073` creates the `ragbot_app` role; `0187` switches policies to `current_setting` GUC; `0141` adds workspace axis. `010g` adds the `audit_log.row_hash` tamper-detect chain. `010m` adds versioned HMAC webhook secrets with grace-period rotation. `0196/0197` encrypt `api_keys` (AES-256-GCM) and null the plaintext. Mindset: *isolation + tamper-evidence + secret-at-rest are migrations, enforced in the DB, not app conventions.*

---

## 3. Current head + invariants

**Head = `0235`** (`20260617_0235_semantic_cache_dim_1024.py`).

### Embedding-dimension invariant — **1024 everywhere**
- `document_chunks.embedding` = `vector(1024)` (since 0228).
- `semantic_cache.query_embedding` = `vector(1024)` (since 0235).
- SSoT constant: `DEFAULT_EMBEDDING_DIM = 1024` (jina-embeddings-v3). HNSW on both: `vector_cosine_ops`, m=32, ef_construction=200.

### 4-key identity invariant
- External resolve boundary uses all 4: `(record_tenant_id, workspace_id, bot_id, channel_type)`.
- DB unique: `uq_bots_record_tenant_workspace_bot_channel` (4 NOT-NULL cols).
- Internal data tables key on `record_bot_id` alone (1:1 with the external triple); `bot_token_usage_log` also carries the full 4-key tuple unique.

### RLS tenant/workspace scoping
- RLS ENABLE+FORCE on tenant-scoped tables (`bots`, `documents`, `document_chunks` via denormalized `record_bot_id`, `document_service_index`, `workspaces`, `message_feedback`, …), policies via `current_setting` GUC (0069 → 0141 workspace-aware → 0187 setting-based). Worker paths must set the GUC.

### Drift / leftover-risk flags (audit findings)
1. **`semantic_cache` dim was the recurring trap** — fixed twice (0105, 0235) because dimension changes on `document_chunks` forgot the cache column. Any *future* embedding-dim migration MUST alter **both** `document_chunks.embedding` and `semantic_cache.query_embedding` (+ both HNSW indexes) in the same migration. (No `vector(1280)` column should remain at head — grep `vector(1280)` over a fresh DB should be 0.)
2. **Per-bot brand/PII in seed migrations** — the spa-* / 0147–0227 `UPDATE` migrations carry demo-tenant-specific Vietnamese content (and historically phone/brand literals). These are tracked content for ONE demo tenant, not platform schema. New platform behavior must go into `language_packs[*][sysprompt_default_rules]` (domain-neutral), per ADR-W1-S10 — not new per-bot UPDATE migrations.
3. **Version-ref names survive only in migration history** — `embedding_v3` / `query_embedding_v3` appear in 0054/0063 as DDL identifiers (legitimately, since those migrations rename them away). They are CORRECT to leave there; the live schema has none (`grep _v3` over `src/` = 0). Do not "fix" historical migration files.
4. **`api_keys.value_plain`** is a deliberately-NULLed legacy column post-0197 (encrypted-at-rest). It is kept for rollback compatibility; treat as deprecated, not active storage.
5. **`webhook_deliveries`** does not exist — only `tenant_webhooks` + `tenant_webhook_secrets`. Webhook delivery tracking was designed (case study 2026-05-12) but deferred; don't assume a deliveries table.

### Schema-authority reminder
- `0001` = `Base.metadata.create_all` → ORM (`models.py` + monitoring/invocation/guardrail) is the SSoT for the v0+AI-config core.
- pgvector/cache/stats/guardrail/webhook/key tables = raw SQL migrations; `document_chunks` write path is owned by `pgvector_store.py`, with a minimal `Table` shim in `models_monitoring.py` purely so FK references resolve at mapper-config time.
