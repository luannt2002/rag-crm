# DEEPDIVE — Multi-Tenant RAG Platform Architecture: 2025–2026 Best Practices (Web Research)

- **Slug**: web-multitenant-arch
- **Date**: 2026-07-02
- **Scope**: tenant isolation in vector DBs (pgvector RLS vs namespace vs collection-per-tenant) · per-tenant config/model routing · noisy-neighbor control · per-tenant cost attribution · headless B2B RAG-as-a-service API design (idempotency, webhooks, versioning) · production postmortems · recommendations for ragbot's 4-key identity + RLS design.
- **Evidence discipline (rule #0)**: every claim is labeled **FACT** (has URL or `file:line`) or **HYPOTHESIS** (unverified inference — needs measurement). Web claims cite the source URL; ragbot claims cite `file:line` read during this session.
- **Mode**: READ-ONLY on `src/`, `tests/`, `alembic/`. Only this report file was created.

---

## 0. Executive summary

Ragbot's core multi-tenant design — **pooled pgvector with Postgres RLS (GUC `app.tenant_id` + NOBYPASSRLS runtime role + `SET LOCAL` on `after_begin`), 4-key bot identity resolve, per-tenant token ledger + cost caps, idempotent single-funnel ingest API, header-based schema versioning** — matches or exceeds the documented 2025–2026 industry pattern ("pool" model with database-enforced deterministic filtering). The research surfaced five concrete hardening opportunities where industry practice is ahead of the current code: (1) filtered-HNSW recall risk (pgvector 0.8 `iterative_scan` exists precisely for tenant/bot-filtered ANN, default OFF); (2) noisy-neighbor control is per-token/per-user only — no tenant-aggregate or token-aware (vs request-count) limits; (3) cache-aware cost attribution (industry: naive attribution over-reports 35–50% at high cache-hit rates); (4) tenant-facing webhooks (ingest completion) remain deferred while the delivery adapter already implements the correct timestamped-HMAC signature; (5) per-tenant model-tier allow-listing is SOTA (LiteLLM org→team→key hierarchy) but ragbot's equivalent module is declared dead code. 2025 postmortems (CVE-2025-66566 cross-tenant buffer-reuse leak in a vector-DB compression path; exposed standalone vector DBs holding plaintext credentials) validate ragbot's choice of Postgres-with-RLS as a single hardened security boundary over a bolt-on vector store.

---

## 1. Tenant isolation in vector DBs — the 2025–2026 landscape

### 1.1 The taxonomy: Silo / Pool / Bridge

**FACT (web)** — AWS's reference architecture for multi-tenant RAG formalizes three patterns ([AWS ML Blog — Multi-tenant RAG with Amazon Bedrock Knowledge Bases](https://aws.amazon.com/blogs/machine-learning/multi-tenant-rag-with-amazon-bedrock-knowledge-bases/)):

| Dimension | Silo (stack per tenant) | Pool (shared + metadata filter) | Bridge (KB per tenant, shared store) |
|---|---|---|---|
| Isolation | Maximum | Minimum (logical) | Moderate |
| Cost | Highest | Lowest | Medium |
| Noisy-neighbor risk | None | High | Low |
| Management complexity | Highest | Lowest | Medium |
| Per-tenant customization | Full | Minimal | High |

Selection guidance from the same source: Silo for the largest / most security-sensitive tenants; Pool for many small tenants where cost wins; Bridge for moderate counts needing per-tenant customization. Offboarding: Pool = delete rows/objects only; Silo = tear down the stack.

**FACT (web)** — The same taxonomy appears academically as "Silo, Pool, and Bridge for Multi-Tenant RAG" ([IJETCSIT paper](https://ijetcsit.org/index.php/ijetcsit/article/download/551/493)) and in the 2026 enterprise guide from Truto, which adds the key security principle: *"Filtering restricted data must occur deterministically at the database level before the context window is ever populated"* — relying on LLM system prompts for access control is an anti-pattern ([Truto — Multi-Tenant RAG Data Isolation](https://truto.one/blog/how-to-architect-strict-data-isolation-in-multi-tenant-rag-pipelines/)).

**FACT (code)** — Ragbot is a **Pool** design with database-enforced isolation, which is the correct quadrant for "many small-to-medium tenants on one platform": shared `document_chunks` table, mandatory `record_bot_id` filter fragment in the vector store (`src/ragbot/infrastructure/vector/pgvector_store.py:218-258` — `clause = "record_bot_id = :record_bot_id"`), plus RLS on `record_tenant_id` (§1.3). This matches Truto's "deterministic at the DB level" mandate — isolation does not depend on the sysprompt.

### 1.2 Vector-DB-native multitenancy (what the dedicated engines do)

- **Pinecone — namespace-per-tenant.** FACT: data-plane ops always target one namespace; serverless namespaces are stored separately giving physical isolation and independent scaling ("if one customer is seeing high activity, queries and writes for other customers won't run slower"); offboarding = delete the namespace; 100k+ namespaces per index, million-scale on higher plans ([Pinecone docs — implement multitenancy](https://docs.pinecone.io/guides/index-data/implement-multitenancy), [Pinecone — Multi-Tenancy in Vector Databases](https://www.pinecone.io/learn/series/vector-databases-in-production-for-busy-engineers/vector-database-multi-tenancy/), [Pinecone serverless architecture](https://www.pinecone.io/blog/serverless-architecture/)).
- **Qdrant — payload-partitioning in ONE collection.** FACT: official guidance is a single collection with a `group_id` payload per tenant; *"You should only create multiple collections when your data is not homogenous or if users' vectors are created by different embedding models"*; collection-per-tenant "creates resource overhead and causes dependencies". Per-tenant HNSW: disable the global graph (`m=0`) and enable per-group indexing (`payload_m=16`) so each tenant gets its own subgraph ([Qdrant — multitenancy article](https://qdrant.tech/articles/multitenancy/), [docs](https://qdrant.tech/documentation/manage-data/multitenancy/)). Qdrant 1.16 added **Tiered Multitenancy** — a shared fallback shard for small tenants + dedicated shards for whales ([Qdrant 1.16 release](https://qdrant.tech/blog/qdrant-1.16.x/)).
- **Milvus** — database-level / collection-level / partition-level / partition-key-level multitenancy, the last scaling to millions of tenants in one collection ([Milvus blog — multi-tenancy RAG best practices](https://milvus.io/blog/build-multi-tenancy-rag-with-milvus-best-practices-part-one.md)).
- **Weaviate** — multi-tenancy "built into the core"; each tenant is a separate shard object with its own lifecycle (hot/warm/cold), generally cited as the most mature native tenant model ([Weaviate vs Qdrant 2026](https://pecollective.com/tools/weaviate-vs-qdrant/)).

**Takeaway for ragbot** — FACT (web): every major engine converged on the same shape ragbot already has: **one shared physical store + a mandatory tenant/bot partition key + optional per-tenant index sub-structures + a tiered escape hatch for whale tenants**. Nobody recommends collection-per-tenant at platform scale.

### 1.3 pgvector + Postgres RLS specifics

**FACT (web)** — Postgres-side isolation options ladder (table-level → schema-level → logical DB → separate instance), with schema-level as the usual balance point and RLS as the pooled-model enforcement tool ([TigerData/Timescale — Building Multi-Tenant RAG Applications With PostgreSQL](https://www.tigerdata.com/blog/building-multi-tenant-rag-applications-with-postgresql-choosing-the-right-approach)). Nile (Postgres re-engineered for multi-tenant B2B) warns the bare tenant-column approach is a *"fragile guarantee"* — "data is accidentally leaked between tenants" via developer error — which is exactly the argument for RLS instead of hand-written WHERE clauses ([Nile — multi-tenant RAG](https://www.thenile.dev/blog/multi-tenant-rag)).

**FACT (web)** — Canonical RLS mechanics for pooled RAG ([Pedro Alonso — Multi-Tenant Search in PostgreSQL with RLS](https://www.pedroalonso.net/blog/postgres-multi-tenant-search/), [rivestack — PostgreSQL RLS complete guide](https://rivestack.io/blog/postgresql-row-level-security), [techtush — Multi-Tenant RAG: RLS in pgvector](https://blog.techtush.in/multi-tenant-rag-row-level-security-in-pgvector-with-mcp)):
1. Policy compares the tenant column to `current_setting('app.tenant_id')`.
2. Bind per transaction with **`SET LOCAL`** (never `SET`) — critical under PgBouncer transaction pooling because `SET LOCAL` rolls back at transaction end while `SET` leaks across pooled sessions.
3. **Index on the tenant column is mandatory** — "catastrophically slow" full scans otherwise; with the index the RLS filter is a negligible index lookup.
4. Superuser / `BYPASSRLS` roles ignore every policy — the app must connect as a NOSUPERUSER + NOBYPASSRLS role or RLS is decorative.

**FACT (code)** — Ragbot implements all four, plus fail-closed semantics:
- 3-layer stack documented at `src/ragbot/infrastructure/db/session.py:1-21`: (1) policies installed by alembic `0069`/`0141`, re-asserted by `0187`; (2) NOSUPERUSER+NOBYPASSRLS `ragbot_app` runtime role (`session.py:66-71`) vs BYPASSRLS `ragbot_system` role for outbox/recovery/cache-GC/cost-cap workers (`session.py:72-76`, `engine.py:101-103`); (3) per-transaction `SET LOCAL app.tenant_id` bound on `after_begin` (correct per Async rule 7 — `SET LOCAL` needs an open transaction, `session.py:36-38`).
- The hook is attached in production wiring: `src/ragbot/bootstrap.py:186` calls `create_rls_session_factory(engine=db_engine, ...)`.
- `session_with_tenant()` **fails loud** when no tenant is bound — raises `RuntimeError` instead of silently skipping `SET LOCAL` (`src/ragbot/infrastructure/db/engine.py:143-165`).
- Policies use `current_setting('app.tenant_id', true)` (missing_ok) so an unbound GUC yields NULL → predicate NULL → **zero rows (fail-closed)** rather than an ERROR that would break non-tenant paths (`alembic/versions/20260626_rls_missing_ok_setting.py:61-71`), with an additional workspace GUC dimension (`workspace_id = current_setting(...)` OR unset ⇒ tenant-wide).
- GUC value is validated as a UUID before interpolation (SET LOCAL takes no bind params) — SQL-injection defence (`session.py` doc: "No bind params ... We validate it parses as a UUID first").

**Verdict (FACT-grounded)**: ragbot's RLS design is *ahead* of the published tutorials — none of the cited articles cover the dual-role (app vs BYPASSRLS system) split, fail-loud unbound-tenant guard, or the missing_ok fail-closed nuance.

**Residual risk (HYPOTHESIS — needs runtime verify)**: `session.py:30-35` states the hook design is "Default OFF (rule #0) — opt-in ... Until a coordinator attaches it **and the runtime DSN points at the NOBYPASSRLS role**, behaviour is byte-for-byte unchanged." `bootstrap.py:186` attaches the hook, but whether the deployed `DATABASE_URL` actually logs in as `ragbot_app` (NOBYPASSRLS) vs a superuser is an **ops/env fact not verifiable from code**. If the DSN is a superuser/BYPASSRLS login, every policy is silently ignored and isolation degrades to the application-level `record_bot_id`/`record_tenant_id` WHERE clauses. → Recommendation R1.

### 1.4 The filtered-ANN recall problem (pgvector-specific, directly hits per-bot filtering)

**FACT (web)** — pgvector walks the HNSW graph, then applies the filter. When the filter (e.g. `record_bot_id = X`) matches only a small fraction of rows, the index can surface candidates that all fail the filter → **recall collapses or fewer than K rows return**. pgvector **0.8.0** added *iterative index scans* specifically for this: `hnsw.iterative_scan` (default **off**; `strict_order`/`relaxed_order` modes) keeps scanning until the filter is satisfied or `hnsw.max_scan_tuples` (default 20,000) is hit ([pgvector 0.8.0 release note](https://www.postgresql.org/about/news/pgvector-080-released-2952/), [AWS — Supercharging vector search with pgvector 0.8.0 on Aurora](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/), [Nile pgvector 0.8.0 announcement](https://www.thenile.dev/blog/pgvector-080), [pgEdge filtering docs](https://docs.pgedge.com/pgvector/v0-8-1/filtering/)). A related planner failure mode: the HNSW index gets bypassed entirely when LIMIT/filter selectivity crosses a threshold ([pgvector issue #721](https://github.com/pgvector/pgvector/issues/721)). Cost of the fix: iterative scans raise CPU + tail latency while restoring recall.

**FACT (code)** — Ragbot's every retrieval is exactly this shape: shared `document_chunks` HNSW index + `WHERE record_bot_id = :record_bot_id` (`pgvector_store.py:218-276`) + RLS tenant predicate on top.

**HYPOTHESIS (needs measurement)** — For a *small bot* (few hundred chunks) inside a *large shared table* (many tenants × many bots), ef_search-bounded HNSW candidates may be dominated by other bots' vectors, returning < K chunks or silently degrading recall — a plausible hidden contributor to historical "chunks=0 / refuse-when-corpus-has-answer" classes of bugs. **CHƯA verify — cần**: (a) `SELECT current_setting('hnsw.iterative_scan')` on prod, (b) EXPLAIN ANALYZE one small-bot query, (c) recall A/B with `iterative_scan=relaxed_order`. → Recommendation R2.

**FACT (web)** — The Qdrant analogue confirms the pattern is universal (per-tenant subgraphs via `payload_m`; global scans get slower — the accepted trade-off) ([Qdrant multitenancy](https://qdrant.tech/articles/multitenancy/)). Postgres analogue for whale tenants: partial indexes or partitioning by tenant — the same "tiered" idea as Qdrant 1.16.

---

## 2. Per-tenant config & model routing

**FACT (web)** — The 2025–2026 reference implementation is the AI-gateway hierarchy, best documented by LiteLLM ([LiteLLM — Multi-Tenant Architecture](https://docs.litellm.ai/docs/proxy/multi_tenant_architecture), [Virtual Keys](https://docs.litellm.ai/docs/proxy/virtual_keys)):
- 4 levels: **Organization → Team → User → Virtual Key**; "Organizations represent the highest level of tenant isolation".
- Budgets cascade with inheritance constraints (team budget ≤ org budget); requests are blocked (429) when any level exceeds budget; spend tracked at all four levels on every call.
- **Model access control per tenant**: org-level allowed-model lists, inherited by teams; keys carry `user_id`/`team_id` so attribution is implicit — no manual tagging.
- Gateways (LiteLLM, Portkey, Kong AI) centralize rate limiting, quotas, retries, provider routing, cost attribution, caching ([Spheron — AI Gateway Setup 2026](https://www.spheron.network/blog/ai-gateway-litellm-portkey-kong-gpu-cloud/)).

**FACT (code)** — Ragbot's equivalents:
- Per-bot model routing via `bot_model_bindings` with a mandated 3-tier fallback chain "per-bot binding → system_config + ai_models → NullObject" (memory: `feedback_resolver_must_fallback_system_config.md`; enforced pattern at `reranker_resolver.py::_lookup_platform_default` per that record).
- Per-bot config chain `column > plan_limits > system_config > schema default` (`src/ragbot/shared/bot_limits.py` per CLAUDE.md "Architecture & key files").
- **Per-tenant model-tier allow-listing exists but is DEAD CODE**: `src/ragbot/infrastructure/tenant_model_tier/static_tenant_model_tier.py:1-23` carries a "DEAD-CODE NOTICE — 2026-06-03 ... NOT reachable from any production entry point ... never imported outside its own dir". The commented design (map `record_tenant_id` → allowed tier subset, unknown tenants fall back to full `DEFAULT_MODEL_TIERS`) is precisely LiteLLM's org-level allowed-model-list pattern.

**Gap (FACT)**: ragbot has per-**bot** routing but the per-**tenant** tier-ceiling layer (e.g. "free-plan tenants may not bind frontier models") is unwired. → Recommendation R5.

---

## 3. Noisy-neighbor control

**FACT (web)** — 2025–2026 consensus for LLM/RAG SaaS ([TrueFoundry — Rate Limiting in AI Gateway](https://www.truefoundry.com/blog/rate-limiting-in-llm-gateway), [Spheron — Multi-Tenant LLM Serving](https://www.spheron.network/blog/multi-tenant-llm-serving-gpu-cloud/), [metacto — LLM Rate Limiting and Token Quotas in Production](https://www.metacto.com/blogs/llm-rate-limiting-token-quotas-production), [systemdr — Designing for Noisy Neighbors](https://systemdr.systemdrd.com/p/designing-for-noisy-neighbors-multi)):
1. **Request-count limits are insufficient for LLM traffic** — one long-context prompt costs orders of magnitude more than a short one; limits must count **tokens and spend**, not requests (Redis-backed budget per key: tokens/day, RPM, daily cap).
2. **Per-tenant concurrency caps** — "global caps without them are an illusion of fairness".
3. **Weighted fair queuing / fair-share scheduling** — each tenant capped at a weighted share of concurrency; free tier gets 1/10 the share of enterprise without starving.
4. **Reserved + burstable** — guaranteed per-tenant slice of the upstream TPM ceiling + shared burst pool.
5. Storage-plane noisy neighbor: Pinecone's namespace-isolation pitch ("one customer's high activity won't slow others") is the same concern at the index layer — in pooled pgvector this maps to statement timeouts, work_mem discipline, and (if needed) tenant-tiered partitioning.

**FACT (code)** — Ragbot today:
- Redis sliding-window limiter with burst support (ZSET algorithm, `src/ragbot/infrastructure/rate_limiter/sliding_window.py:1-23`).
- Key granularity = **per token / per user-per-tenant**: `tok:{record_tenant_id}:{user_id}` else bearer-hash (`src/ragbot/interfaces/http/middlewares/rate_limit.py:51-71`), composed per-endpoint (`rate_limit.py:172-174`). Pre-auth callers get an IP limiter (`rate_limit.py:154-157`).
- Per-tenant **ingest quota** (daily docs) with row-locked atomic check+increment, RLS-scoped (`src/ragbot/application/services/ingest_quota_service.py:1-35`).
- Per-tenant **monthly token cap**: `cost_cap_alerter.py` aggregates `request_logs.total_tokens` per `record_tenant_id` vs `tenants.quota_monthly_tokens`, emitting `cost_cap_warning` / `cost_cap_exceeded` (`src/ragbot/application/services/cost_cap_alerter.py:3-4,91-109`; thresholds in `shared/constants/_16_prompt_token_squeeze.py:207-209`); zero quota = "block all ... enforced elsewhere" (`cost_cap_alerter.py:108-109`).

**Gaps (FACT by absence, verified by key-derivation code)**:
- No **tenant-aggregate** request/concurrency limit — a tenant with many users/tokens can multiply its share; the limiter key never aggregates at `record_tenant_id` alone (`rate_limit.py:51-71`).
- No **token-aware** (vs request-count) rate limiting at request admission; token control is retrospective (monthly alerter), not per-window budget → a runaway tenant burns a month's tokens in hours before the alerter trips. → Recommendation R3.

---

## 4. Per-tenant cost attribution

**FACT (web)** — 2026 playbooks ([Braintrust — How to track LLM costs (2026)](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026), [Particula — Per-Tenant LLM Cost Attribution](https://particula.tech/blog/per-tenant-llm-cost-attribution-multi-tenant-saas), [SoftwareSeni — Token Attribution and Cost Governance](https://www.softwareseni.com/token-attribution-and-cost-governance-for-multi-tenant-llm-products-in-production/), [Opsmeter — tenant profitability](https://opsmeter.io/blog/tenant-profitability-ai-costs)):
1. Attach tenant/user/task IDs to **every** LLM request at creation time; attribution is "the prerequisite for every other cost lever".
2. **Cache-aware pricing**: ingest cached-read and cached-write token counters separately — naive attribution **over-reports spend 35–50%** at high cache-hit rates (Anthropic cached-read = 10% of list input price; at 80% hit rate effective per-token cost ≈ 18% of list).
3. Watch the **long tail**: "3% of tenants consume 60% of tokens" — averages hide collapsing per-tenant margins.
4. Daily per-tenant spend alerts vs a 7-day rolling baseline; automated rate-limit tightening on breach.

**FACT (code)** — Ragbot's token ledger writes **full 4-key + internal-key attribution per event**: `record_tenant_id, record_bot_id, bot_id, workspace_id, channel_type, ...` (`src/ragbot/infrastructure/token_ledger/async_db_token_ledger.py:36-58`), backed by `request_logs.total_tokens` aggregation per tenant (`cost_cap_alerter.py:130-136`). This satisfies playbook item 1 at finer granularity (per-bot, per-channel) than the LiteLLM baseline.

**HYPOTHESIS (needs code trace)** — Cache-read vs cache-write token counters and the "3%/60%" tail report were not verified in this session's reads; if `token_ledger`/`request_logs` don't split cached tokens, per-tenant margin reporting will over-state cost materially per the 35–50% figure above. **CHƯA verify — cần** grep `cache_read`/`cache_creation` columns in ledger schema. → Recommendation R4.

---

## 5. Headless B2B API design (idempotency · webhooks · versioning)

### 5.1 Idempotency

**FACT (web)** — Stripe's canon ([Designing robust and predictable APIs with idempotency](https://stripe.com/blog/idempotency), [Stripe API — idempotent requests](https://docs.stripe.com/api/idempotent_requests)): idempotency key header on all mutating POSTs; server correlates the key with stored operation state and **replays the cached result** on retry; clients retry with exponential backoff + jitter; Stripe v2 makes all POST/DELETE idempotent with a 30-day replay window scoped per account. An IETF Idempotency-Key RFC draft standardizes the header ([httptoolkit — Idempotency Keys RFC](https://httptoolkit.com/blog/idempotency-keys/)).

**FACT (code)** — Ragbot's canonical ingest endpoint implements exactly this: `X-Idempotency-Key` header, replay of the original response within the window, and — a nuance Stripe also documents — **quota charged only after replay check** so retries aren't double-billed (`src/ragbot/interfaces/http/routes/documents.py:11-14, 116-166`, `ingest_idempotency_replay` event at `:148`; Redis SETNX adapter `src/ragbot/infrastructure/idempotency/__init__.py:1`). Aligned with SOTA; the optional-header design ("no header → pre-idempotency contract, partner BE owns retry semantics", `documents.py:14,116`) matches Stripe's opt-in model.

### 5.2 Webhooks

**FACT (web)** — Consolidated best practice ([Standard Webhooks spec](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md), [Svix — Building a Webhook Sender](https://www.svix.com/resources/webhook-university/implementation/building-a-webhook-sender/), [Svix — retry best practices](https://www.svix.com/resources/webhook-best-practices/retries/), [Hooklistener — webhook security](https://www.hooklistener.com/learn/webhook-security-fundamentals), [Hookdeck — reliable outbound webhooks](https://hookdeck.com/blog/building-reliable-outbound-webhooks)):
1. HMAC-SHA256 over **timestamp + payload** (not payload alone), timestamp in the header, receiver rejects events older than ~5 min (replay defence — Stripe's `t=` scheme).
2. Exponential backoff **with jitter** (thundering-herd defence); Stripe retries up to 3 days.
3. After 5–10 attempts → **dead-letter queue** + manual/endpoint-level replay; disable chronically failing endpoints.
4. Consumers implement the Idempotent Receiver pattern (store processed event IDs).
5. Webhooks primary + periodic API polling as backup channel ([Stigg — Stripe webhook lessons](https://www.stigg.io/blog-posts/best-practices-i-wish-we-knew-when-integrating-stripe-webhooks)).
6. Secret rotation support is part of the sender contract.

**FACT (code)** — Ragbot has two webhook paths:
- **Chat-answer callback delivery** (`src/ragbot/infrastructure/delivery/callback_delivery.py`): signs `f"{timestamp}." + body` with HMAC-SHA256 → `X-Ragbot-Signature: sha256=...` (`:105-118`) — **correct timestamped scheme**, matching Standard Webhooks; exponential-backoff retry with a reused client (`:26-31, 84-86`).
- **Ops alert notifier** (`src/ragbot/infrastructure/notify/webhook_dispatcher.py`): bounded retry `attempts = max_retries + 1`, multiplicative backoff capped (`:226-298`), 4xx = config bug → no retry (`:266`), 5xx/timeout → retry (`:285-289`). Plus `application/services/webhook_secret_rotation.py` (secret rotation) and `infrastructure/security/hmac_signer.py:9-16` (constant-time verify helper).

**Gaps (FACT by inspection + memory)**: (a) no **jitter** in either backoff loop (`webhook_dispatcher.py:294-298` grows deterministically); (b) no **dead-letter persistence/replay** — the full tenant-facing webhook system (tenant_webhooks + webhook_deliveries tables, document state machine, 21 control points) was designed 2026-05-12 and explicitly **DEFERRED** (memory: `project_webhook_callback_design_20260512.md` — "Implementation effort 20h/6PR DEFERRED MVP"); ingest completion is currently poll-only for partners. → Recommendation R6.

### 5.3 Versioning

**FACT (web)** — 2025–2026 consensus is nuanced: URL-path versioning for **public** APIs (discoverability, gateway routing, CDN-friendliness); **header-based** versioning for **enterprise/contract-oriented, controlled-client ecosystems** ([Speakeasy — versioning best practices](https://www.speakeasy.com/api-design/versioning), [DreamFactory — Top 5 API Versioning Strategies 2025](https://blog.dreamfactory.com/top-5-api-versioning-strategies-2025-dreamfactory), [ASOasis — URL vs Header](https://asoasis.tech/articles/2026-04-21-0254-rest-api-versioning-url-vs-header/), [Spring — API Versioning](https://spring.io/blog/2025/09/16/api-versioning-in-spring/)). Known header-versioning cost: reduced visibility/debuggability for consumers.

**FACT (code)** — Ragbot chose header (`X-Schema-Version`) with allow-list validation and 4xx on unsupported values (`src/ragbot/interfaces/http/middlewares/schema_version.py:53-90`, constant at `shared/constants/_09_message_feedback_thumbs_verd.py:130`), and a body-level mirror field for forward-compat (`interfaces/http/schemas/document_schema.py:59-67`). Given ragbot is **headless B2B server-to-server with a controlled partner set** (CLAUDE.md "HEADLESS BE PLATFORM"), this sits on the *defensible* side of the industry split — provided the version is documented in partner onboarding and (HYPOTHESIS — not checked) echoed in responses/errors for debuggability.

---

## 6. Production postmortems & lessons (2025–2026)

1. **Cross-tenant leak via infrastructure, not queries** — FACT: CVE-2025-66566: a race in buffer-recycling compression paths (lz4-family) of a vector-DB stack let responses carry **memory fragments of other tenants' data**; malicious tenants could trigger it with malformed-query streams ([Penligent — forensic analysis of CVE-2025-66566](https://www.penligent.ai/hackinglabs/the-glass-floor-of-ai-infrastructure-a-deep-forensic-analysis-of-cve-2025-66566/)). Lesson: logical filters don't protect against shared-process memory bugs; minimizing the number of data-plane engines (ragbot: everything in Postgres) shrinks this surface.
2. **Exposed standalone vector DBs** — FACT: Orca found internet-exposed vector databases containing plaintext credentials usable for lateral movement ([Orca — vector database security risks](https://orca.security/resources/blog/vector-database-security-risks/)). Lesson: a second data store = a second auth/patch/exposure boundary; pgvector-inside-Postgres keeps one boundary.
3. **35% of real AI incidents started with simple prompts**; most breaches were "improper validation, infrastructure gaps, missing human oversight" — FACT ([Adversa AI — 2025 AI security incidents report](https://adversa.ai/blog/adversa-ai-unveils-explosive-2025-ai-security-incidents-report-revealing-how-generative-and-agentic-ai-are-already-under-attack/), [Reco — AI & Cloud Security Breaches 2025](https://www.reco.ai/blog/ai-and-cloud-security-breaches-2025)). Reinforces Truto's "never do ACL in the prompt".
4. **ZenML meta-study of 1,200 production LLM deployments** — FACT ([ZenML — What 1,200 Production Deployments Reveal About LLMOps in 2025](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025)):
   - "Reaching 80% quality happened quickly, but pushing past 95% required the majority of development time" (LinkedIn) — matches ragbot's own plateau history (code-only ceiling 78–79%, memory `project_v2_3round_final.md`).
   - "Context rot" begins 50k–150k tokens regardless of model limits (Manus); tool outputs consume "100x more tokens than user messages" (Shopify).
   - Prompt caching cut one medical-records workload's cost **86%** and improved speed 3× (Care Access) — "infrastructure engineering rather than model upgrades".
   - Circuit breakers on cost/turn P95 (Cox Automotive); golden datasets reviewed independently of user behavior + shadow-mode + separate LLM-judge before enabling automation (Ramp); durable execution (Temporal) for resumable agent work (Slack).
   - Core insight: *"the bottleneck is engineering rather than intelligence."*
5. **RAG still central; hybrid BM25+dense + reranking (20–30% top-k lift) + semantic chunking are the surviving practices** — FACT ([kapa.ai — RAG pipeline from scratch 2026](https://www.kapa.ai/blog/how-to-build-a-rag-pipeline-from-scratch-in-2026), [Morphik — RAG in 2025](https://www.morphik.ai/blog/retrieval-augmented-generation-strategies)) — all three already in ragbot's pipeline.

---

## 7. Recommendations for ragbot (4-key identity + RLS design)

Ranked by (risk × effort⁻¹). Each is labeled with the CORE-MVP tier.

### R1 [T1/T2 — HIGH] RLS runtime-role preflight + cross-tenant canary
**FACT**: the whole RLS stack is only live when the runtime DSN is the NOBYPASSRLS `ragbot_app` role (`session.py:12-16, 66-71`); a `rolbypassrls` login "ignores every policy" (`session.py:14-15`). The hook is attached (`bootstrap.py:186`) but role-of-DSN is an env fact.
**Do**: startup preflight — `SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user` → structured WARNING (engine.py already logs an RLS-bypass warning per `engine.py:65` — extend to a `/health` surface); plus a permanent canary test: open a session bound to tenant A, assert zero rows visible from tenant B's `document_chunks`. This converts isolation from "configured" to "continuously proven" — the AWS JWT+FGAC reference makes the same move with runtime-scoped credentials ([AWS — multi-tenant RAG with JWT](https://aws.amazon.com/blogs/machine-learning/multi-tenant-rag-implementation-with-amazon-bedrock-and-amazon-opensearch-service-for-saas-using-jwt/)).

### R2 [T1 — HIGH] Measure filtered-HNSW recall; enable `hnsw.iterative_scan` if needed
**FACT**: pgvector 0.8 iterative scans exist precisely for ragbot's query shape (§1.4) and default **off**. **HYPOTHESIS**: small bots in the shared index lose recall/rows today. **Do**: EXPLAIN ANALYZE + recall A/B on a small-corpus bot; if confirmed, set `hnsw.iterative_scan = relaxed_order` + tune `hnsw.max_scan_tuples` via system_config (zero-hardcode), and monitor tail latency (the documented cost). Long-term (only if a whale tenant appears): tiered layout — partial index or partitioned table for the whale, mirroring Qdrant 1.16 tiered multitenancy — via ADR (EVOLVE, not rewrite).

### R3 [T2 — MEDIUM] Tenant-aggregate + token-aware admission control
**FACT**: current limiter keys are per-token/per-user (`rate_limit.py:51-71`); token control is retrospective monthly (`cost_cap_alerter.py`). Industry: token-budget windows + per-tenant concurrency caps + reserved/burstable shares (§3). **Do**: add a second limiter layer keyed `tenant:{record_tenant_id}` (the sliding-window adapter already supports arbitrary keys + burst, `sliding_window.py:17-23`), and a Redis daily token budget decremented from ledger events — both config-driven per plan (`plan_limits`), no new infra. Aligns with "bypass flags = revenue features" (memory: paid-tier semantics).

### R4 [T2 — MEDIUM] Cache-aware cost attribution + long-tail tenant report
**FACT**: naive attribution over-reports 35–50% at high cache-hit rates; cached-read ≈ 10% of list price (§4). **HYPOTHESIS**: ledger doesn't split cached counters (unverified). **Do**: verify ledger columns; if missing, add `cache_read_tokens`/`cache_write_tokens` to the ledger event (alembic) and a "top-N tenants by token share" report — guards margin against the "3% of tenants = 60% of tokens" tail.

### R5 [T3 — LOW] Decide the fate of `tenant_model_tier` (revive or delete)
**FACT**: dead code since 2026-06-03 (`static_tenant_model_tier.py:1-23`) while the pattern it implements (per-tenant model allow-list, LiteLLM org-level) is the SOTA monetization/guardrail lever (§2). **Do**: if plan-tiered model access is on the roadmap, wire it in bootstrap behind `plan_limits`; else delete per the module's own notice. Leaving designed-but-dead multi-tenant controls contradicts the "wire, don't rewrite" program stance.

### R6 [T2 — MEDIUM] Ship the deferred tenant-facing ingest webhook, on the existing signer
**FACT**: partners poll for ingest completion today; the full design (tenant_webhooks, webhook_deliveries, state machine) exists and was deferred (memory 2026-05-12); the correct timestamped-HMAC signature is already implemented in `callback_delivery.py:105-118`. **Do** (when prioritized): reuse that signer; add jitter to both backoff loops (`webhook_dispatcher.py:294-298` — one-line change each per Svix guidance) and a `webhook_deliveries` DLQ table with replay; document the 5-minute receiver tolerance and Idempotent-Receiver expectations in partner docs.

### R7 [T3 — LOW] Keep header versioning; document + echo it
**FACT**: header versioning is the accepted enterprise/B2B pattern; its known cost is debuggability (§5.3). **Do**: ensure error payloads echo the negotiated schema version (the 4xx on unsupported version already lists supported ones, `schema_version.py:83-90`) and partner docs pin it — no URL migration needed; CLAUDE.md's no-URL-version rule stays intact.

### R8 [T1 — MEDIUM] Tenant offboarding completeness (pool model)
**FACT**: in a pool design offboarding = explicit row deletion across **every** tenant-scoped table (AWS §1.1: pool offboarding = delete objects; Pinecone's one-call namespace delete is the bar). Working tree already contains `tests/unit/test_purge_content_tables.py` (git status, this session) — **Do**: drive the purge path from a single authoritative list of tenant-scoped tables (models metadata, not a hand-maintained list) so a future table can't be silently skipped; add a post-purge canary (zero rows for tenant across all scoped tables + vector index).

---

## 8. Source index

**Isolation / vector DB**: [Truto 2026 guide](https://truto.one/blog/how-to-architect-strict-data-isolation-in-multi-tenant-rag-pipelines/) · [AWS Bedrock KB multi-tenant](https://aws.amazon.com/blogs/machine-learning/multi-tenant-rag-with-amazon-bedrock-knowledge-bases/) · [AWS JWT+FGAC](https://aws.amazon.com/blogs/machine-learning/multi-tenant-rag-implementation-with-amazon-bedrock-and-amazon-opensearch-service-for-saas-using-jwt/) · [AWS Aurora multi-tenant vector](https://aws.amazon.com/blogs/database/multi-tenant-vector-search-with-amazon-aurora-postgresql-and-amazon-bedrock-knowledge-bases/) · [Silo/Pool/Bridge paper](https://ijetcsit.org/index.php/ijetcsit/article/download/551/493) · [TigerData Postgres multi-tenant RAG](https://www.tigerdata.com/blog/building-multi-tenant-rag-applications-with-postgresql-choosing-the-right-approach) · [Nile multi-tenant RAG](https://www.thenile.dev/blog/multi-tenant-rag) · [Nile platform](https://www.thenile.dev/) · [Pinecone multitenancy docs](https://docs.pinecone.io/guides/index-data/implement-multitenancy) · [Pinecone learn](https://www.pinecone.io/learn/series/vector-databases-in-production-for-busy-engineers/vector-database-multi-tenancy/) · [Pinecone serverless](https://www.pinecone.io/blog/serverless-architecture/) · [Qdrant multitenancy](https://qdrant.tech/articles/multitenancy/) · [Qdrant docs](https://qdrant.tech/documentation/manage-data/multitenancy/) · [Qdrant 1.16 tiered](https://qdrant.tech/blog/qdrant-1.16.x/) · [Milvus best practices](https://milvus.io/blog/build-multi-tenancy-rag-with-milvus-best-practices-part-one.md) · [Weaviate vs Qdrant 2026](https://pecollective.com/tools/weaviate-vs-qdrant/)

**RLS / pgvector**: [Pedro Alonso RLS search](https://www.pedroalonso.net/blog/postgres-multi-tenant-search/) · [rivestack RLS guide](https://rivestack.io/blog/postgresql-row-level-security) · [techtush RLS pgvector](https://blog.techtush.in/multi-tenant-rag-row-level-security-in-pgvector-with-mcp) · [pgvector 0.8.0 release](https://www.postgresql.org/about/news/pgvector-080-released-2952/) · [AWS pgvector 0.8 Aurora](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/) · [Nile pgvector 0.8](https://www.thenile.dev/blog/pgvector-080) · [pgEdge filtering](https://docs.pgedge.com/pgvector/v0-8-1/filtering/) · [pgvector issue #721](https://github.com/pgvector/pgvector/issues/721) · [ParadeDB pgvector limitations](https://www.paradedb.com/learn/postgresql/pgvector-limitations)

**Routing / noisy neighbor / cost**: [LiteLLM multi-tenant](https://docs.litellm.ai/docs/proxy/multi_tenant_architecture) · [LiteLLM virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys) · [Spheron multi-tenant LLM serving](https://www.spheron.network/blog/multi-tenant-llm-serving-gpu-cloud/) · [Spheron AI gateways](https://www.spheron.network/blog/ai-gateway-litellm-portkey-kong-gpu-cloud/) · [TrueFoundry rate limiting](https://www.truefoundry.com/blog/rate-limiting-in-llm-gateway) · [metacto token quotas](https://www.metacto.com/blogs/llm-rate-limiting-token-quotas-production) · [systemdr noisy neighbors](https://systemdr.systemdrd.com/p/designing-for-noisy-neighbors-multi) · [Braintrust LLM cost tracking 2026](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026) · [Particula per-tenant attribution](https://particula.tech/blog/per-tenant-llm-cost-attribution-multi-tenant-saas) · [SoftwareSeni token attribution](https://www.softwareseni.com/token-attribution-and-cost-governance-for-multi-tenant-llm-products-in-production/) · [Opsmeter tenant profitability](https://opsmeter.io/blog/tenant-profitability-ai-costs)

**API design**: [Stripe idempotency blog](https://stripe.com/blog/idempotency) · [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) · [Idempotency-Key RFC](https://httptoolkit.com/blog/idempotency-keys/) · [Standard Webhooks spec](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md) · [Svix sender guide](https://www.svix.com/resources/webhook-university/implementation/building-a-webhook-sender/) · [Svix retries](https://www.svix.com/resources/webhook-best-practices/retries/) · [Hooklistener webhook security](https://www.hooklistener.com/learn/webhook-security-fundamentals) · [Hookdeck outbound webhooks](https://hookdeck.com/blog/building-reliable-outbound-webhooks) · [Stigg Stripe webhook lessons](https://www.stigg.io/blog-posts/best-practices-i-wish-we-knew-when-integrating-stripe-webhooks) · [Speakeasy versioning](https://www.speakeasy.com/api-design/versioning) · [DreamFactory versioning 2025](https://blog.dreamfactory.com/top-5-api-versioning-strategies-2025-dreamfactory) · [ASOasis URL vs header](https://asoasis.tech/articles/2026-04-21-0254-rest-api-versioning-url-vs-header/) · [Spring API versioning](https://spring.io/blog/2025/09/16/api-versioning-in-spring/)

**Postmortems / lessons**: [ZenML 1,200 deployments](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025) · [Penligent CVE-2025-66566](https://www.penligent.ai/hackinglabs/the-glass-floor-of-ai-infrastructure-a-deep-forensic-analysis-of-cve-2025-66566/) · [Orca vector DB risks](https://orca.security/resources/blog/vector-database-security-risks/) · [Adversa 2025 incidents](https://adversa.ai/blog/adversa-ai-unveils-explosive-2025-ai-security-incidents-report-revealing-how-generative-and-agentic-ai-are-already-under-attack/) · [Reco 2025 breaches](https://www.reco.ai/blog/ai-and-cloud-security-breaches-2025/) · [kapa.ai RAG 2026](https://www.kapa.ai/blog/how-to-build-a-rag-pipeline-from-scratch-in-2026) · [Morphik RAG 2025](https://www.morphik.ai/blog/retrieval-augmented-generation-strategies)

**Ragbot code evidence read this session**: `src/ragbot/infrastructure/db/session.py` (1-84, 117-243) · `src/ragbot/infrastructure/db/engine.py` (65-165, 202) · `src/ragbot/bootstrap.py:186` · `alembic/versions/20260626_rls_missing_ok_setting.py` (7-71) · `src/ragbot/infrastructure/vector/pgvector_store.py` (3, 116-292) · `src/ragbot/interfaces/http/middlewares/rate_limit.py` (51-185) · `src/ragbot/infrastructure/rate_limiter/sliding_window.py` (1-72) · `src/ragbot/application/services/ingest_quota_service.py` (1-96) · `src/ragbot/application/services/cost_cap_alerter.py` (3-157) · `src/ragbot/infrastructure/token_ledger/async_db_token_ledger.py` (36-58) · `src/ragbot/interfaces/http/routes/documents.py` (11-166) · `src/ragbot/interfaces/http/middlewares/schema_version.py` (3-90) · `src/ragbot/infrastructure/delivery/callback_delivery.py` (26-118) · `src/ragbot/infrastructure/notify/webhook_dispatcher.py` (51-298) · `src/ragbot/infrastructure/security/hmac_signer.py` (9-16) · `src/ragbot/infrastructure/tenant_model_tier/static_tenant_model_tier.py` (1-40) · `src/ragbot/application/services/bot_registry_service.py:3` · `src/ragbot/infrastructure/cache/semantic_cache.py` (167-244)
