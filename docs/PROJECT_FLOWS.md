# Project Flows Documentation

**Version:** `57ffdda` family (current HEAD `862ff12` — post multi-agent R3, alembic head `010e`, 2026-05-16)
**Last Updated:** 2026-05-16
**Mô tả ngắn:** Ragbot là một nền tảng RAG (Retrieval Augmented Generation) multi-tenant SaaS phục vụ chat bot tùy chỉnh per-tenant. Hệ thống vận hành một pipeline LangGraph 24-step canonical (32-step observable trong `request_steps`) gồm `retrieve → rerank → grade → generate`, isolation theo 4-key identity `(record_tenant_id, workspace_id, bot_id, channel_type)`, semantic cache 2-tier (exact-hash + pgvector cosine), và async job/outbox over Redis Streams. Audit score hiện tại 8.6/10.

> **Đọc bổ sung**:
> - `RAGBOT_MASTER.md` (`/var/www/html/ragbot/RAGBOT_MASTER.md`) — TOC điểm vào kiến trúc
> - `STATE_SNAPSHOT.md` — current state, always-updated
> - `CLAUDE.md` — sacred rules (4-key identity / Zero-hardcode / Strategy+DI / Domain-neutral / App-mindset)
> - `docs/master/04-D-pipeline-orchestration.md` — chi tiết 24-step canonical pipeline
> - `docs/master/11-K-pipeline-code-mapping.md` — step → file:line
> - `docs/master/05-E-cross-cutting-patterns.md` — RLS / RBAC / observability
> - `docs/master/15-O-anti-hallu-tuning.md` — 9-layer HALLU=0 config
> - `reports/MULTI_AGENT_20260516/00_MASTER_VERDICT.md` — current open findings
> - `reports/SECURITY_AUDIT_20260516/00_MASTER_SECURITY_FINDINGS.md` — 48 finding (5 fixed, 42 còn lại)
> - `reports/LOAD_TEST_VERDICT_20260516/07_POST_FIXES_VERDICT.md` — perf baseline

---

## 1. High-Level System Architecture

### 1.1 Stack at a glance

| Layer | Tech |
|---|---|
| Runtime | Python 3.12+, type-strict, async-first |
| HTTP | FastAPI + Uvicorn (4 workers) — REST + SSE |
| Orchestration | LangGraph `StateGraph` — singleton compiled graph |
| Vector | pgvector (HNSW 1280-dim, ZeroEntropy `zembed-1` matryoshka) + tsvector BM25 + RRF |
| Tenant isolation | App-layer repo scoping + PostgreSQL Row-Level Security (alembic `0069` policy `FORCE`d on 20+ tables) |
| AI bindings | per-bot via `bot_model_bindings(purpose)` — embedding (`zembed-1`) / rerank (`zerank-2`) / `llm_answer` (`gpt-4.1-mini`) / `llm_enrich` (`claude-haiku-4-5` ingest-only) |
| Cache | Redis L1 + pgvector L2 (cosine ≥ 0.97) + Anthropic prompt-cache |
| Events | Redis Streams + dedup ledger (XREADGROUP consumer groups, NOT NATS) |
| Auth | JWT bearer (HS256 service tokens + RS256 user tokens) + 7-tier numeric RBAC |
| Observability | structlog JSON + Prometheus + `request_steps` row-per-stage table |
| Deploy | Docker Compose + systemd templates (`ragbot-document-worker@.service` 4× scale) |
| DB | PostgreSQL 16 (admin DSN + non-superuser app DSN — RLS gate) |

### 1.2 Topology diagram

```mermaid
graph TD
  subgraph Browser["Browser / SDK / Channel adapter (Zalo / LINE / Viber / web)"]
    UI[Frontend / Channel webhook]
  end

  subgraph API["FastAPI process (`asgi.py` → `interfaces.http.router`)"]
    MW[TenantContextMiddleware<br/>JWT verify + rate limit]
    ChatR[/POST /api/ragbot/chat<br/>202 + job_id/]
    StreamR[/POST /api/ragbot/chat/stream<br/>SSE/]
    TestR[/POST /api/ragbot/test/*<br/>demo CRUD + chat/]
    DocsR[/POST /api/ragbot/documents/*<br/>upload + sync/]
    AdminR[/admin: tenants / bots / ai / policy / audit/]
  end

  subgraph Workers["Background workers (systemd unit per worker)"]
    CW[chat_worker<br/>Redis consumer<br/>chat.received.v1]
    DW[document_worker<br/>x4 instances<br/>document.uploaded.v1]
    OB[outbox_publisher<br/>FOR UPDATE SKIP LOCKED]
    AsyncCW[chat_async_worker<br/>G25 D1 - /test/chat-async]
    CfgL[ai_config_listener<br/>cache invalidate]
  end

  subgraph Graph["LangGraph StateGraph singleton"]
    Q[24-step pipeline<br/>guard_input -> cache -> understand -> router -> retrieve -> rerank -> mmr_dedup -> grade -> generate -> guard_output -> reflect -> persist]
  end

  subgraph DB[("PostgreSQL 16 + pgvector + RLS")]
    BotsT[(bots / bot_model_bindings)]
    Chunks[(document_chunks<br/>HNSW 1280-dim)]
    SemC[(semantic_cache<br/>L2 cosine >= 0.97)]
    Outbox[(outbox<br/>txnal event log)]
    Audit[(audit_log<br/>forensic trail)]
    ReqLog[(request_logs<br/>+ request_steps<br/>+ request_chunk_refs)]
    AICfg[(ai_providers / ai_models<br/>+ system_config)]
  end

  subgraph Redis[("Redis 7 - caches + streams")]
    L1[(L1 caches:<br/>bot config, JWT version,<br/>token quota, system_config)]
    Streams[(Streams: chat.received,<br/>chat.answered, document.uploaded)]
    Locks[(stampede-avoid locks<br/>semantic cache)]
  end

  subgraph External["External AI providers"]
    ZE[ZeroEntropy<br/>zembed-1 + zerank-2]
    OAI[OpenAI<br/>gpt-4.1-mini]
    Anth[Anthropic<br/>claude-haiku-4-5<br/>ingest enrich only]
  end

  UI -->|JWT bearer + 4-key body| MW
  MW --> ChatR
  MW --> StreamR
  MW --> TestR
  MW --> DocsR
  MW --> AdminR

  ChatR -->|enqueue| Outbox
  DocsR -->|enqueue| Outbox
  Outbox -.->|publish| Streams

  Streams --> CW
  Streams --> DW
  Streams --> AsyncCW

  CW --> Q
  StreamR --> Q
  AsyncCW --> Q
  Q --> External
  Q --> DB
  Q --> Redis

  DW --> ZE
  DW --> Anth
  DW --> DB

  CfgL -->|invalidate| Redis
  AICfg -.->|event| Streams
```

### 1.3 5-Layer Hexagonal / DDD Architecture

Source layout (root `src/ragbot/`):

```
src/ragbot/
├── domain/              # Entities, value objects, events (pure Python, no I/O)
├── application/
│   ├── ports/           # 15+ Protocol/ABC interfaces (LLMPort, RerankerPort, VectorStorePort, …)
│   ├── services/        # Use cases (no I/O; depends on Ports)
│   ├── use_cases/       # Command/Query handlers (AnswerQuestionUseCase, IngestDocumentUseCase, …)
│   ├── commands/        # Command DTOs (`AnswerQuestionCommand`, `GiveFeedbackCommand`, …)
│   ├── events/          # `ChatCompletedEvent`, `ChatHookRegistry`
│   └── dto/             # Pydantic schemas (`AISpecs`, `model_runtime`, `chat_payload`)
├── infrastructure/
│   ├── llm/             # `dynamic_litellm_router` (prompt-cache + failover)
│   ├── embedding/       # litellm + zeroentropy + null + registry
│   ├── reranker/        # zeroentropy + litellm + jina + viranker + null + registry
│   ├── vector/          # `pgvector_store` (HNSW + BM25 + RRF), `registry`
│   ├── cache/           # `redis_cache` (L1) + `semantic_cache` (L2 pgvector)
│   ├── parsers/         # PDF / Excel / Sheets / Markdown / Kreuzberg + registry
│   ├── guardrails/      # `local_guardrail` (input + output) + `pii_universal`
│   ├── repositories/    # 30+ SqlAlchemy* repos (BotRepository, DocumentRepository, …)
│   ├── events/          # `redis_streams_bus`
│   ├── delivery/        # callback channel adapter
│   ├── notify/          # `webhook_dispatcher`, `chat_innocom_notifier`
│   └── observability/   # `circuit_breaker`, `invocation_logger`, `pipeline_audit_logger`
├── orchestration/       # `query_graph.py` (LangGraph build_graph + get_graph singleton) + `state.py` (`GraphState` TypedDict)
├── interfaces/
│   ├── http/
│   │   ├── routes/      # `chat.py`, `chat_stream.py`, `chat_async.py`, `test_chat.py`, `documents.py`, `sync.py`, `admin_*.py`, `health*.py`, `feedback.py`, `jobs.py`, `honeypot.py`
│   │   ├── middlewares/ # `tenant_context.py`, `rbac.py`, `_resource_ownership.py`, anti-abuse
│   │   ├── schemas/     # Pydantic request/response models
│   │   └── _sse_helper.py # SSE streaming framing
│   └── workers/         # `chat_worker.py`, `chat_async_worker.py`, `document_worker.py`, `outbox_publisher.py`
└── shared/              # `constants.py` (SSoT defaults), `bot_limits.py`, `errors.py`, `types.py`,
                         # `rbac.py` (numeric levels), `api_key_pool.py`, `workspace_id_validator.py`,
                         # `bot_bindings.py`, `vi_tokenizer.py`, `hashing.py`, `clock.py`, `json_io.py`
```

DI container `src/ragbot/bootstrap.py:135` wires every Port to its Strategy via `dependency_injector` (`providers.Singleton` / `providers.Factory`).

### 1.4 Multi-tenancy at a glance

| Layer | Keys carried |
|---|---|
| HTTP request body | `(bot_id, channel_type)` REQUIRED + `workspace_id` OPTIONAL slug |
| JWT bearer claim | `record_tenant_id: UUID` REQUIRED |
| External resolve (`bots` lookup) | `(record_tenant_id, workspace_id, bot_id, channel_type)` — ALL 4 |
| Redis registry cache key | `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}` |
| DB unique constraint | `uq_bots_record_tenant_workspace_bot_channel(...)` — 4 cột NOT NULL |
| Internal queries | `record_bot_id` UUID only (post-resolve, scoped + unique) |
| Composite index on data tables | `record_bot_id` (+ `workspace_id` for forensic scoping) |
| Tenant-level / forensic rows | `workspace_id = WORKSPACE_SYSTEM_SLUG ("system")` |

Workspace concept (V10 pass-through, `RAGBOT_MASTER.md` §9): platform validates slug FORMAT (`^[a-zA-Z0-9-]+$`, 1-64) but NEVER manages workspace lifecycle. Missing slug → fallback `str(record_tenant_id)`.

---

## 2. Core Data Flows

### 2.1 Ingestion / Upload Flow

**Trigger:** `POST /api/ragbot/test/documents/upload` (multipart UploadFile) OR `POST /api/ragbot/sync/documents` (URL pull from upstream).

**Steps:**

| # | Step | Component | File:line |
|---|---|---|---|
| 1 | Auth + 4-key resolve | `TenantContextMiddleware` → `BotRegistryService.lookup` | `tenant_context.py:88` / `bot_registry_service.py` |
| 2 | Body validate (size, hash dedup) | `ChatRequest`-equivalent doc schema | `interfaces/http/schemas/` |
| 3 | Quota check | `TenantTokenMeter` + `bot_limits.resolve_bot_limit` | `tenant_token_meter.py` / `bot_limits.py` |
| 4 | Persist doc row + enqueue | `IngestDocumentUseCase` → `OutboxRepository.append` | `application/use_cases/ingest_document.py` |
| 5 | Return `202 + job_id` | `JobRepository` row | `documents.py` / `test_chat.py` |
| 6 | Worker consume | `document_worker.handle_document_uploaded` | `interfaces/workers/document_worker.py:57` |
| 7 | Run 7-step ingest graph (U1-U7) | `DocumentService.ingest()` | `application/services/document_service.py` |
| 8 | Vector upsert | `PgVectorStore.upsert_chunks` (RLS-scoped session) | `infrastructure/vector/pgvector_store.py:84` |
| 9 | Outbox emit `document.ingested.v1` | OutboxRepository + `redis_streams_bus` | `infrastructure/events/redis_streams_bus.py` |
| 10 | Side effect: invalidate semantic cache for bot | `SystemConfigService` + Redis publish | `ai_config_service.py` |

**7 Ingest steps (U1-U7)** — `docs/master/04-D-pipeline-orchestration.md` §16:

```
U1 VALIDATE → size guard (max 500k chars) + source_url dedup + content_hash dedup
U2 PARSE    → mime detect → parser registry (excel / sheets / pdf / md / kreuzberg / null)
U3 CLEAN    → NFC normalize → hyphenation fix → whitespace collapse → prompt-injection strip
U4 CHUNK    → analyze_document() → strategy score → (table_csv / recursive / hdt / semantic / hybrid / proposition)
U5 ENRICH   → whole-doc bypass → parent-child split → CR (contextual retrieval) → metadata extract
              (claude-haiku-4-5 enrichment, concurrency 20, skip <50K chars docs)
U6 VN SEG   → underthesea compound segmentation (per-bot, P22)
U7 EMBED+STORE → ZeroEntropy zembed-1 (dimensions: 1280 matryoshka) → pgvector document_chunks
                + tsvector BM25 + HNSW index
```

**Output:** Each chunk lands in `document_chunks` table with `record_bot_id` (denormalized post alembic `0108`), `chunk_index`, `content`, `content_hash`, `embedding` (vector 1280), `metadata_json`. `request_logs` row written with `connect_id="ingest"` sentinel; `request_steps` rows per U1-U7 with latency.

**Side effects:**
- `outbox` row → `document.uploaded.v1` then `document.ingested.v1` published to Redis Streams.
- `audit_log` row written via `AuditLoggerPort`.
- Bot's semantic cache rows invalidated (corpus version bump).

### 2.2 Query / Retrieval Flow

**Trigger:** `POST /api/ragbot/chat` (4-key body + JWT bearer; returns `202 + job_id`).

**Steps:**

```mermaid
sequenceDiagram
  participant Client
  participant MW as TenantContextMiddleware
  participant ChatR as chat.py (route)
  participant UC as AnswerQuestionUseCase
  participant Outbox as OutboxRepository
  participant Stream as Redis Stream chat.received.v1
  participant CW as chat_worker
  participant Graph as LangGraph 24-step
  participant DB as PostgreSQL (RLS)
  participant Redis as Redis L1/L2

  Client->>MW: POST /chat {body, JWT}
  MW->>MW: verify JWT (HS256->RS256 fallback)
  MW->>MW: lift record_tenant_id -> request.state
  MW->>MW: Layer-1/1.5/2 rate-limit gate (fail-CLOSED)
  MW->>ChatR: dispatch
  ChatR->>ChatR: resolve_workspace_id(req.workspace_id, record_tenant_id)
  ChatR->>UC: AnswerQuestionCommand
  UC->>DB: bot_registry_service.lookup(4-key) (cached Redis 60s)
  UC->>Outbox: INSERT outbox(chat.received.v1, payload)
  UC->>DB: INSERT request_logs(request_id, status=queued)
  ChatR-->>Client: 202 + {job_id, status_url}
  Note over Outbox,Stream: outbox_publisher poll_one_for_update<br/>(FOR UPDATE SKIP LOCKED)
  Outbox->>Stream: XADD chat.received.v1
  Stream->>CW: XREADGROUP consumer
  CW->>CW: ChatReceivedPayload.parse + bind_request_context
  CW->>DB: _build_pipeline_config (batched get_many 65 keys -> 1 round trip)
  CW->>Redis: load history (chat_histories LIMIT max_history)
  CW->>Graph: graph.ainvoke(initial_state)
  Graph->>Graph: run 24 nodes (cache_check -> understand -> retrieve -> rerank -> grade -> generate -> guard_output -> reflect -> persist)
  Graph->>DB: persist node writes request_logs.status=done<br/>+ request_chunk_refs (alembic 0109)
  Graph->>Outbox: emit chat.answered.v1 (or chat.failed.v1)
  Outbox->>Stream: XADD chat.answered.v1
  Note over Stream: callback adapter delivers to caller-provided URL<br/>(if mode=callback)
```

**24 LangGraph nodes** (verified `query_graph.py:5328-5443` — names match singleton compile):

| Node | Source line | Purpose |
|---|---|---|
| `guard_input` | `query_graph.py:5329` | Q1 — length / PII / injection scan via `LocalGuardrail.check_input` |
| `cache_check_and_understand_parallel` | `query_graph.py:5334` | Q2+Q3 fused — exact hash + cosine ≥ 0.97 lookup against `semantic_cache` in parallel with intent classify |
| `understand_query` | `query_graph.py:5338` | Q3 fallback — intent + condense merged 1-call LLM (Haiku) |
| `condense_question` | `query_graph.py:5339` | History condense (multi-turn) |
| `router` | `query_graph.py:5340` | Static router (rewrite / retrieve / decompose) |
| `rewrite_and_mq_parallel` | `query_graph.py:5345` | Q4 — HyDE-style rewrite + multi-query expansion in parallel |
| `decompose` | `query_graph.py:5346` | Q5 — multi-hop sub-query decomposition |
| `query_complexity` | `query_graph.py:5351` | Adaptive Router L1 — heuristic complexity classifier |
| `adaptive_decompose` | `query_graph.py:5352` | Adaptive Router L3 — LLM decomposer gated on L1 |
| `retrieve` | `query_graph.py:5353` | Q6 — abbrev expand → vocab → metadata filter → multi-query fanout → hybrid (dense+BM25 RRF) → fallback ladder → parent-child expand → autocut |
| `graph_retrieve` | `query_graph.py:5354` | Q7 — GraphRAG knowledge-graph traversal (off by default) |
| `rerank` | `query_graph.py:5355` | Q10 — ZeroEntropy `zerank-2` cross-encoder (per-bot DI via `reranker_resolver`) |
| `mmr_dedup` | `query_graph.py:5356` | Q9/Q11 — cosine 0.88 dedup (called pre + post rerank) |
| `grade` | `query_graph.py:5357` | Q12 — CRAG grader (structured output → text-parse fallback) |
| `rewrite_retry` | `query_graph.py:5358` | Q13 — CRAG retry loop (max 2) |
| `generate` | `query_graph.py:5359` | Q14 — prompt compress → lost-in-middle reorder → platform_rules + bot persona compose → LLM `gpt-4.1-mini` → citation parse → math lockdown |
| `guard_output` | `query_graph.py:5360` | Q15 — system-prompt-leak shingle (platform-only since Phase 2.11) + grounding judge + PII filter |
| `reflect` | `query_graph.py:5361` | Q16 — self-reflection (skip for factoid) |
| `persist` | `query_graph.py:5362` | Q17 — semantic_cache write + ChatAnswered outbox emit + request_logs update |

24 step canonical = 19 explicit nodes + 5 fused/observable substeps in `request_steps` (see §2.5).

**Output:** `ChatAcceptedResponse {job_id, status, status_url, trace_id}` returned synchronously (202). The actual answer is delivered via:
- callback URL (if `mode="callback"` provided in body), OR
- polling `GET /api/ragbot/jobs/{job_id}` until `status="done"`.

**Side effects:**
- `request_logs` updated with `status=done`, `chunks_used`, `top_score`, `cost_usd`, `tokens` JSONB.
- `request_steps` rows: ≤32 rows per request (one per instrumented step).
- `request_chunk_refs` rows: one row per chunk used (alembic `0109` — split from JSONB blob).
- `semantic_cache` row inserted with TTL = 24h (or per-bot override).
- `chat.answered.v1` outbox event → Redis Stream → optional callback delivery.

### 2.3 Conversation Flow (Multi-turn)

Conversation context arrives via the `conversation_history` GraphState slot and flows through `condense_question` node into the generate LLM prompt.

```mermaid
sequenceDiagram
  participant CW as chat_worker
  participant Convs as ConversationRepository
  participant DB as chat_histories (PostgreSQL)
  participant Graph as LangGraph

  CW->>Convs: get_history(record_tenant_id, record_bot_id, connect_id, limit=max_history)
  Convs->>DB: SELECT … LIMIT N (keyset paginated)
  DB-->>Convs: list[Message]
  Convs-->>CW: trim by Python summary (history_limit)
  CW->>Graph: initial_state.conversation_history = [...]
  Graph->>Graph: condense_question merges history -> standalone query
  Graph->>Graph: generate LLM call sees both history + retrieved chunks
```

- `chat_max_history` from `system_config` (default 4 turns; overridable per-bot via `plan_limits.history_limit`).
- Per-message body cap: 800 chars (`Phase 2.11`) to keep prompt under budget.
- Citation-marker strip applied to history before resend.
- `chat_histories` table FK `record_bot_id` + `connect_id` index for hot lookup.

### 2.4 Setting & Configuration Flow

**3-tier resolve order** (highest wins) — `RAGBOT_MASTER.md` §14:

```
1. bots.threshold_overrides[key] (per-bot JSONB; bot owner explicit tune)
2. bots.<dedicated_column>      (e.g. max_documents, oos_answer_template)
3. bots.plan_limits[key]        (per-bot JSONB; plan-tier override)
4. system_config.<key>          (live UPSERT; deployment-wide; Redis-cached)
5. PLAN_LIMIT_SCHEMA[key]["default"] (validation default from bot_limits.py)
6. constants.DEFAULT_<KEY>      (final fallback in shared/constants.py)
```

Files:
- `shared/bot_limits.py` — `resolve_bot_limit(bot_cfg, key, system_default)` central helper.
- `application/services/system_config_service.py` — read-through SystemConfigService (`get_int`, `get_float`, `get_bool`, `get_many` batched).
- `application/services/bot_registry_service.py` — 4-key cached BotConfig lookup.
- `application/services/model_resolver.py:1` — DB-driven AI strategy resolver (`bot_model_bindings → ai_models → ai_providers`); 2-tier cache LRU + Redis; invalidated on `bot.config_updated.v1`.

**Per-bot binding table** (`bot_model_bindings`):

| Column | Purpose values |
|---|---|
| `purpose` | `embedding`, `rerank`, `llm_answer`, `llm_enrich`, `llm_decompose`, `llm_understand`, `llm_grade`, `llm_grounding`, `llm_hyde` |
| `record_model_id` | FK to `ai_models` (provider + name + dim + price) |
| `record_provider_id` | FK to `ai_providers` (code + base_url) |
| `active` | tinyint — only one active binding per `(bot, purpose)` |

**3-tier fallback for missing binding** (`reranker_resolver._lookup_platform_default`):
```
per-bot binding -> system_config + ai_models JOIN -> NullObject (silent OFF, KHONG raise)
```

Force reload after `system_config` UPSERT:
```bash
curl -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" http://localhost:3004/admin/cache/reload
```

### 2.5 Event Logging & Analytics Flow

| Table | Purpose | Owner | RBAC |
|---|---|---|---|
| `audit_log` | Forensic admin trail (CRUD on bot / token / settings) | `audit_repository.py` | `super_admin` read |
| `request_logs` | Per-request envelope (status, latency, cost, tokens, chunks_used) | `request_log_repository.py` | tenant-scoped |
| `request_steps` | Per-step latency (24-32 rows per request) | `StepTracker` | tenant-scoped |
| `request_chunk_refs` | Per-chunk reference used to answer (alembic `0109`) | `RequestChunkRefsRepository` | tenant-scoped |
| `guardrail_events` | Input/output guardrail blocks (with raw match) | `GuardrailRepository` | tenant-scoped |
| `message_feedback` | Thumbs up/down feedback for training-loop | `MessageFeedbackRepository` | tenant-scoped |
| `bot_token_usage_log` | Per-bot quota deduction trail (alembic `0101`) | hooks in chat_worker | tenant-scoped |
| `outbox` | Transactional event log (exactly-once delivery) | `outbox_repository.py` | n/a |

**24 step `step_name` values instrumented** (live in `request_steps`):
`guard_input`, `cache_check`, `understand_query`, `condense_question`, `router`, `rewrite_and_mq_parallel`, `decompose`, `query_complexity`, `adaptive_decompose`, `retrieve`, `graph_retrieve`, `rerank`, `mmr_dedup_pre`, `mmr_dedup_post`, `grade`, `rewrite_retry`, `generate`, `guard_output`, `reflect`, `persist`, plus sub-steps `filter_min_score`, `multi_query_fanout`, `rrf_fuse`, `litm_order`, `citations_extract` (mapped via `StepTracker` wraps).

Observable 32-step trace = 19 explicit nodes + 5 fused-parallel subspans + 8 sub-steps recorded inside `retrieve` / `rerank` / `grade` / `guard_output`.

### 2.6 WebSocket / SSE Flow

**SSE only** — project does NOT use WebSocket. SSE endpoint:

```
POST /api/ragbot/chat/stream -> text/event-stream
```

Helper: `interfaces/http/_sse_helper.py:stream_real_llm` and `_STREAM_SENTINEL`.

```mermaid
sequenceDiagram
  participant Client
  participant Stream as chat_stream.py
  participant Helper as _sse_helper
  participant Graph as LangGraph (same 24-step)
  participant LLM as gpt-4.1-mini

  Client->>Stream: POST /chat/stream {body, JWT}
  Stream->>Stream: 4-key resolve (mirror /chat)
  Stream->>Stream: streaming_response_enabled + streaming_enabled (kill-switch)
  Stream->>Graph: ainvoke (sync in-request)
  Graph->>LLM: stream=True (token deltas)
  loop per token chunk
    LLM-->>Graph: delta
    Graph-->>Helper: yield (status/replace/done frame)
    Helper-->>Client: data: {...}
  end
  Helper-->>Client: data: {event: done}
  Stream-->>Client: HTTP close
```

SSE contract events: `status`, `replace`, `done`. In-process memory sink, max `DEFAULT_SSE_SINK_MAXSIZE` frames; back-pressure shuts the request if exceeded. No Redis/Outbox for SSE — single-process lifetime.

### 2.7 Background & Async Jobs

| Worker | Module | Stream subject consumed | Purpose |
|---|---|---|---|
| `chat_worker` | `interfaces/workers/chat_worker.py:1` | `chat.received.v1` | Main RAG pipeline runner (24-step graph) |
| `document_worker` | `interfaces/workers/document_worker.py:57` | `document.uploaded.v1` | Async parse + chunk + embed (×4 instances via systemd template) |
| `chat_async_worker` | `interfaces/workers/chat_async_worker.py` (G25, anchor `a7579d6`) | `chat.received.v1` (test variant) | Alternate async path for `/test/chat-async` POST + GET polling |
| `outbox_publisher` | `interfaces/workers/outbox_publisher.py:1` | (writes) | DB outbox poll → Redis Stream publish (exactly-once via `FOR UPDATE SKIP LOCKED`, Agent N 2026-05-16) |
| `ai_config_listener` | `infrastructure/events/ai_config_listener.py` | `bot.config_updated.v1` / `system_config.changed.v1` | Invalidate model_resolver + bot_registry caches |

**Stream subjects** (verified `shared/constants.py:1169-1178`):

```python
SUBJECT_CHAT_RECEIVED = "chat.received.v1"
SUBJECT_CHAT_ANSWERED = "chat.answered.v1"
SUBJECT_CHAT_FAILED   = "chat.failed.v1"
SUBJECT_CHAT_DELIVERY_FAILED = "chat.delivery_failed.v1"
SUBJECT_DOCUMENT_UPLOADED = "document.uploaded.v1"
SUBJECT_DOCUMENT_INGESTED = "document.ingested.v1"
SUBJECT_DOCUMENT_FAILED   = "document.failed.v1"
SUBJECT_CORPUS_VERSION_CHANGED = "corpus.version_changed.v1"
SUBJECT_SYSTEM_CONFIG_CHANGED  = "system_config.changed.v1"
SUBJECT_TOKEN_REVOKED          = "token.revoked.v1"
```

Delivery: `at-least-once` via XREADGROUP/XACK + dedup ledger; `exactly-once` for outbox-published events (per-row tx with SKIP LOCKED, `outbox_publisher.py:97-139`).

### 2.8 Authentication & Authorization Flow

```mermaid
flowchart TD
  R[HTTP request] --> P{path in _PUBLIC_PATHS<br/>or /demo-ragbot<br/>or /static/<br/>or /tokens/self?}
  P -->|yes| OK[call_next] --> Done([handler])
  P -->|no| H{Authorization: Bearer ?}
  H -->|missing| U401a[401 missing bearer]
  H -->|present| S[JwtTokenService.verify_token<br/>HS256 service path]
  S -->|valid| RT[resolve record_tenant_id<br/>from claim or upstream INT]
  S -->|invalid| Sf[fall through to user JWT]
  Sf --> U[JwtVerifier.verify<br/>RS256 user path]
  U -->|invalid| U401b[401 invalid token]
  U -->|valid| Rt2[lift record_tenant_id from claim]
  RT --> RL[Layer 1: per-tenant rate limit<br/>+ bypass forward to limiter]
  RL --> RL15[Layer 1.5: per-service token cap<br/>fail-CLOSED on Redis error]
  RL15 --> RL2[Layer 2: per-user cap<br/>keyed on connect_id]
  RL2 --> BS[bind request.state:<br/>record_tenant_id, user_id, role]
  Rt2 --> BS
  BS --> RBAC[RBAC dependency<br/>require_permission_dep / require_min_level]
  RBAC --> Done
```

**RBAC numeric levels** (`shared/rbac.py`):

| Level | Role | Example permissions |
|---|---|---|
| 100 | `super_admin` | cross-tenant CRUD, `/admin/cache/reload`, tenant CRUD |
| 80 | `admin` | platform-level bot management |
| 60 | `tenant_admin` | tenant-scoped CRUD, `chat:submit`, `chat:feedback` |
| 40 | `editor` | document upload, sync |
| 20 | `viewer` | read-only |
| 0 | `guest` | public health only |

35+ routes gated via `Depends(require_permission_dep("resource", "action"))` (e.g. `chat:submit`, `chat:stream`, `chat:feedback`, `bot:cache_reload`, `tenant:list`).

**Module permissions table** (`module_permissions` — alembic `010b` seeds `bot:cache_reload`): RBAC permission catalog mapped to numeric `min_level`. Route checks `_check_min_level(request, _resolve(resource, action))`.

**JWT secret rotation**: `api_tokens.version` bump → Redis key `ragbot:token_ver:{service_name}` updated → all tokens minted with `< version` rejected by `JwtTokenService.verify_token`.

**Tenant context defence-in-depth route guard** (`tenant_context.py:459 enforce_tenant_match`): when a route accepts body-level UUIDs (admin endpoints), enforces JWT `record_tenant_id == body record_tenant_id` (super_admin bypass via `check_min_level(100)`).

---

## 3. Detailed Component Flows

### 3.1 Transaction Management

**Engine pair** (`infrastructure/db/engine.py`):

| Engine | Builder | DSN | Use |
|---|---|---|---|
| Admin engine | `create_engine(settings)` | `DATABASE_URL` (postgres superuser) | alembic migrations + ops scripts |
| App engine | `create_engine_app(settings)` | `DATABASE_URL_APP` (non-superuser, RLS-bound) | runtime HTTP + workers |

App engine refuses to start without `DATABASE_URL_APP` unless `RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV=ragbot:dev-escape` is set (emits `engine.app_dsn_superuser_fallback` WARNING).

**Session pattern** (`session_with_tenant` — `engine.py:103-150`):

```python
@asynccontextmanager
async def session_with_tenant(factory, *, record_tenant_id=None):
    # 1. Bind tenant_id_ctx (contextvar) if not already set
    # 2. SET LOCAL app.tenant_id = '<uuid>' (interpolated — SET LOCAL no bind)
    # 3. SET LOCAL statement_timeout = DEFAULT_STATEMENT_TIMEOUT_MS
    # 4. yield session
    # 5. close session
```

- `SET LOCAL` requires single transaction — caller must NOT `session.commit()` mid-context.
- Raises `RuntimeError` if neither contextvar nor kwarg supplies tenant → cross-tenant write leak prevention.
- UUID-validated before interpolation (defence vs SQL injection — `_assert_uuid_str`).

**Outbox per-row tx** (Agent N 2026-05-16, exactly-once):

```python
# outbox_publisher.py:114
for _ in range(batch_size):
    cm = repo.poll_one_for_update()  # SELECT … WHERE status='pending' FOR UPDATE SKIP LOCKED LIMIT 1
    async with cm as (session, rec):
        if rec is None: return attempted
        await _publish_one(bus=bus, rec=rec)  # XADD
        await repo.mark_processed_in_session(session, rec.id)
        await session.commit()  # atomic publish + mark
```

Crash between publish and commit: lock auto-releases on tx death, row remains visible to peers.

**Atomic ingest write**: chunks INSERT batched in a single transaction inside `pgvector_store.upsert_chunks` (`pgvector_store.py:84-130`) — `DELETE WHERE record_document_id = :doc_id` then `INSERT … VALUES (…)` for each chunk in one session.

### 3.2 Redis Usage & Caching Flows

**Key design** (verified across `tenant_context.py:194`, `bot_registry_service`, `semantic_cache.py`):

| Key pattern | TTL | Purpose |
|---|---|---|
| `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}` | 60s | 4-key BotConfig cache (writer = `BotRegistryService._key`; reader = middleware + use cases) |
| `ragbot:cache:lock:{record_bot_id}:{query_hash}` | `DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S` | Semantic cache stampede single-flight lock |
| `ragbot:bot:tokens_used:{record_bot_id}` | `month` window | L1 token-quota counter (DB hooks reconcile) |
| `ragbot:token_ver:{service_name}` | none (versioned) | JWT revoke watermark |
| `ragbot:rl:{service_or_user}:{bucket}` | window + 1 | Layer-1/1.5/2 rate-limit counter |
| `ragbot:sysconfig:{key}` | per-key TTL | system_config read-through cache |
| `ragbot:model_resolver:{purpose}:{bot}` | `DEFAULT_MODEL_RESOLVER_L2_TTL_S` | Resolved AI bindings (L2 of LRU+Redis) |
| `ragbot:tenant_cfg:{record_tenant_id}` | bootstrap TTL | TenantConfigCache (rate_limit_per_min, monthly_token_cap, allowed_origins) |
| `ragbot:embed_cache:{hash}` | bootstrap TTL | Pre-embed cache for retrieval queries |
| `ragbot:uq:{prompt_version}:{hash}` | bootstrap TTL | UnderstandQueryCache (intent + condense) |

**Redis Streams**:

| Stream name | Producers | Consumer group | Workers |
|---|---|---|---|
| `chat.received.v1` | `AnswerQuestionUseCase` (outbox) | `chat-worker-grp` | `chat_worker` (+ `chat_async_worker`) |
| `chat.answered.v1` | LangGraph `persist` node | `delivery-grp` | callback adapter |
| `document.uploaded.v1` | `IngestDocumentUseCase` (outbox) | `document-worker-grp` | `document_worker` ×4 |
| `bot.config_updated.v1` | admin_bots route | `ai-config-listener-grp` | `ai_config_listener` |
| `system_config.changed.v1` | `/admin/cache/reload` | `ai-config-listener-grp` | `ai_config_listener` |

XREADGROUP consumer groups + `XACK` provide at-least-once; outbox publisher provides exactly-once on the publish side.

**Anthropic prompt cache**: `claude-haiku-4-5` ingest path enables `cache_control` on system prompt + first user message → 90% input-token discount on warm calls (`infrastructure/llm/anthropic_cache.py`).

### 3.3 Database Interaction Flows

**Repository pattern**: every domain entity has a Port (`application/ports/`) and a SqlAlchemy adapter (`infrastructure/repositories/`). Use cases depend on Port → ports injected via DI container.

| Port | Adapter | Hot query |
|---|---|---|
| `VectorStorePort` | `PgVectorStore` (`pgvector_store.py:75`) | `search()` HNSW push-down; `hybrid_search()` RRF dense + sparse BM25 |
| `BotRepositoryPort` | `SqlAlchemyBotRepository` | `find_by_4key(...)` → `record_bot_id` resolve |
| `SemanticCachePort` | `PgSemanticCache` (`semantic_cache.py:105`) | exact SHA256 → cosine ≥ 0.97 fallback |
| `AIConfigRepositoryPort` | `SqlAlchemyAIConfigRepository` | `get_models_by_ids(...)` batched (Agent F P1 — post-Coder P) |
| `OutboxRepositoryPort` | `SqlAlchemyOutboxRepository` | `poll_one_for_update()` SKIP LOCKED |
| `RequestLogRepository` | `request_log_repository.py` | per-request envelope INSERT then UPDATE |
| `AuditRepository` | `audit_repository.py` | append-only forensic events |

**Critical SQL patterns**:

```sql
-- pgvector search (HNSW push-down active post alembic 0108):
SELECT chunk_index, content, content_hash, metadata_json,
       1 - (embedding <=> :query_vec) AS score
FROM document_chunks
WHERE record_bot_id = :rb     -- denormalized, indexed (alembic 0108)
  AND deleted_at IS NULL      -- soft-delete filter (Agent F P0 — pending fix)
ORDER BY embedding <=> :query_vec
LIMIT :top_k;
SET LOCAL hnsw.ef_search = :ef;  -- DEFAULT_EF_SEARCH = 64, MAX 200
```

```sql
-- hybrid_search RRF fusion:
WITH dense AS (
  SELECT chunk_id, rank() OVER (ORDER BY embedding <=> :qv) AS rd
  FROM document_chunks WHERE record_bot_id = :rb
  LIMIT :top_k_dense
),
sparse AS (
  SELECT chunk_id, rank() OVER (ORDER BY ts_rank(tsv, query) DESC) AS rs
  FROM document_chunks WHERE record_bot_id = :rb AND tsv @@ query
  LIMIT :top_k_sparse
)
SELECT chunk_id,
       (:w_vec / (:rrf_k + COALESCE(rd, :penalty)))
     + (:w_bm25 / (:rrf_k + COALESCE(rs, :penalty))) AS score
FROM dense FULL OUTER JOIN sparse USING (chunk_id)
ORDER BY score DESC LIMIT :top_n;
```

```sql
-- semantic cache lookup (exact-hash -> cosine fallback):
SELECT response_json FROM semantic_cache
WHERE record_bot_id = :rb AND query_hash = :h AND expires_at > NOW();
-- miss -> cosine path:
SELECT response_json, 1 - (query_embedding <=> :qv) AS sim
FROM semantic_cache
WHERE record_bot_id = :rb AND expires_at > NOW()
ORDER BY query_embedding <=> :qv
LIMIT 1; -- accept if sim >= SEMANTIC_CACHE_THRESHOLD (0.97)
```

**Recent migrations** (`alembic/versions/`):

| Rev | Date | Description |
|---|---|---|
| `0093` | 2026-05-13 | `documents` progress columns (ingest job state) |
| `0094` / `0094a` | 2026-05-14 | source allowlist + `feature_name` column on `model_invocations` |
| `0095` / `0095a` | 2026-05-14 | adaptive chunking L5 flag + cost knobs |
| `0097` | 2026-05-14 | Kreuzberg parser engine seed |
| `0098` | 2026-05-14 | domain-neutral keys |
| `0099` | 2026-05-14 | VN domain data seed |
| `0100` | 2026-05-14 | bot quota columns |
| `0101` | 2026-05-14 | `bot_token_usage_log` |
| `0102` | 2026-05-14 | quota system_config seeds |
| `0103` | 2026-05-15 | tune query_complexity weight numbers |
| `0104` | 2026-05-16 | enable parallel paths (cache+understand, rewrite+mq) |
| `0105` | 2026-05-16 | fix semantic_cache dim (post ZE 1280 swap) |
| `0106` | 2026-05-16 | GIN index on `documents.metadata` |
| `0107a` | 2026-05-16 | composite doc bot state index |
| `0107b` | 2026-05-16 | drop duplicate `ix_semantic_cache_bot` |
| `0107c` | 2026-05-16 | 15 missing FKs + orphan reset (MEGA-2) |
| `0108` | 2026-05-16 | `document_chunks.record_bot_id` denormalization (MEGA-1 — unlocks HNSW push-down) |
| `0109` | 2026-05-16 | `request_chunk_refs` relational split from JSONB |
| `010a` | 2026-05-16 | merge `0107c` + `0109` heads |
| `010b` | 2026-05-16 | seed `bot:cache_reload` permission |
| `010c` | 2026-05-16 | Agent F P1 perf indexes (5 of 8 added) |
| `010d` | 2026-05-16 | seed `vector_store_provider` |
| `010e` | 2026-05-16 | `providers.requires_prefix` column (Strategy+DI) |

### 3.4 Error Handling & Resilience Flow

**Circuit Breaker** (`infrastructure/observability/circuit_breaker.py` + `application/services/retry_policy.py`):

- `CircuitBreaker(name="litellm", policy=CircuitBreakerPolicy())` — `test_chat.py:75` LLM call wrap (also wrapping `JinaReranker`, `LiteLLMEmbedder`).
- 5 consecutive failures → trip OPEN → 30s cool-down → HALF_OPEN test.
- Trip event logged + Prometheus counter incremented.

**ApiKeyPool failover** (`shared/api_key_pool.py`):

- `DBBackedApiKeyPoolFactory` reads `ai_keys` table per provider; rotates RoundRobin on 401/429.
- Hot-load on `bot.config_updated.v1` event.
- `provider_api_keys` legacy fallback from `PROVIDER_API_KEYS_JSON` env.

**Graceful degradation matrix** (CLAUDE.md mindset patterns):

| Component | Failure | Behavior |
|---|---|---|
| Redis (L1 caches) | Connection timeout / 5xx | Degrade silent — fall through to DB |
| Redis Streams (events) | XADD failure | Outbox row stays `pending`, retried |
| Reranker (Jina/ZE) | 5xx / timeout | `CircuitBreaker` trip → `NullReranker` fallback (RRF stays as best score) |
| Embedder | 401 (key rotated) | `ApiKeyPool` rotates → retry; pool exhaust → request fail-loud |
| Semantic cache | DB error | Cache miss path (no error to caller) |
| Vector store | DB error | `RetrievalError` → CRAG retry → if exhausted → `no_context` answer (refuse) |
| `prometheus_client` import | `ImportError` | Module loads with `metrics_export = None`; pipeline unaffected |
| `audit_log` insert | DB error | `AuditEmitError` swallowed (forensic loss accepted, app continues) |
| Output guardrail | Block (e.g. system-prompt leak shingle hit) | `answer_type="blocked"`, refusal returned |

**Narrow exception classes** (`shared/errors.py`):

`AuditEmitError`, `RetrievalError`, `EmbeddingError`, `IngestError`, `BusError`, `InvariantViolation`, `WorkspaceIdInvalid`.

`except Exception:` count is a decreasing-only metric — sweep regression guard at `tests/unit/test_narrow_exception_hierarchy.py::test_broad_except_count_decreases`. Current count: 196 broad-except sites, ~25 closed in `e9dc6c4` (AUTH-5 + 7 noqa scripts).

**Structlog context**:
- `bind_request_context(trace_id, record_tenant_id, bot_id, user_id)` set per HTTP / worker entry.
- `clear_request_context()` in `finally` block in workers — contextvars are coroutine-scoped, must reset per consumed Stream message.
- JSON output formatter: `ProcessorFormatter` (Caveat: `extra={...}` is swallowed — use `logger.info("event", key=val)` kwargs form).

---

## 4. Multi-Tenancy Flows

### 4.1 4-key propagation across layers

```mermaid
flowchart LR
  subgraph Wire
    JWT[JWT bearer<br/>record_tenant_id UUID claim]
    Body[HTTP body<br/>bot_id, channel_type, workspace_id?]
  end

  subgraph Middleware
    TC[TenantContextMiddleware<br/>lift claim -> request.state.record_tenant_id]
  end

  subgraph Resolve
    WSR[resolve_workspace_id<br/>fallback str record_tenant_id]
    BRS[BotRegistryService.lookup<br/>4-key -> record_bot_id UUID]
  end

  subgraph DB
    SWT[session_with_tenant<br/>SET LOCAL app.tenant_id]
    RLS[(PostgreSQL RLS<br/>20+ tables policy FORCE)]
    Q[Queries scope by<br/>record_bot_id only<br/>unique post-resolve]
  end

  JWT --> TC
  Body --> WSR
  TC --> WSR
  WSR --> BRS
  BRS --> SWT
  SWT --> RLS
  RLS --> Q
```

### 4.2 Resolve flow detail

1. **HTTP body**: `(bot_id, channel_type)` REQUIRED + `workspace_id` OPTIONAL slug.
2. **JWT bearer**: `record_tenant_id: UUID` REQUIRED → `TenantContextMiddleware` lifts onto `request.state.record_tenant_id` (`tenant_context.py:355-365`).
3. **Workspace fallback**: `resolve_workspace_id(req.workspace_id, record_tenant_id=record_tenant_id)` from `shared/workspace_id_validator.py` — slug validated `^[a-zA-Z0-9-]+$` 1-64 chars; missing → `str(record_tenant_id)`; invalid → `WorkspaceIdInvalid` (422).
4. **4-key bot resolve**: `BotRegistryService.lookup(record_tenant_id, workspace_id, bot_id, channel_type)` — Redis cache `ragbot:bot:{...}:60s` then DB `find_by_4key`. Result: `BotConfig` with `.id` = `record_bot_id` UUID.
5. **Internal queries**: every repo method takes `record_bot_id` (the resolved UUID) — composite indexes on `(record_bot_id, ...)` cover hot paths.
6. **Session-level RLS**: `session_with_tenant(factory, record_tenant_id=record_tenant_id)` opens session with `SET LOCAL app.tenant_id = '<uuid>'`. PostgreSQL RLS policy `tenant_isolation` (alembic `0069`) `FORCE`d on 20+ tables checks `record_tenant_id = current_setting('app.tenant_id')::uuid`.

### 4.3 RLS effective state

- Policy created on 20+ tables (alembic `0069`) — `FORCE`d so even table owner cannot bypass.
- **Caveat**: `postgres` (superuser) has `BYPASSRLS=t`. Effective only when app connects as non-superuser (`DATABASE_URL_APP` configured, `T1.S1b` tracker).
- Current state (`STATE_SNAPSHOT.md`): dev DSN uses `postgres` user → RLS bypass at runtime (DEV intentional). Production must set `DATABASE_URL_APP=postgres://ragbot_app:...@host/...`.
- Engine refuses superuser fallback unless `RAGBOT_ALLOW_SUPERUSER_RUNTIME=ragbot:dev-escape` env set (and logs WARNING when used).

### 4.4 Cross-workspace isolation

Two tenants × two workspaces can independently set `bot_id="support"` + `channel_type="web"`. Resolution produces 4 distinct `record_bot_id` UUIDs → data tables scope by `record_bot_id` only → no leak.

Missing any key → cross-tenant/cross-workspace leak risk (caught by `uq_bots_record_tenant_workspace_bot_channel` UNIQUE constraint).

---

## 5. Sequence Diagrams (Mermaid)

### 5.1 Main User Request Flow (POST /chat -> 202 + job_id -> poll)

```mermaid
sequenceDiagram
  participant Client
  participant FastAPI as FastAPI / TenantContextMiddleware
  participant ChatRoute as chat.py
  participant UseCase as AnswerQuestionUseCase
  participant Outbox as OutboxRepository
  participant Publisher as outbox_publisher
  participant Stream as Redis Stream chat.received.v1
  participant Worker as chat_worker
  participant Graph as LangGraph (singleton)
  participant DB as PostgreSQL
  participant Job as JobRepository
  participant CB as ChatCompletedEvent

  Client->>FastAPI: POST /api/ragbot/chat<br/>{workspace_id, bot_id, channel_type,<br/>connect_id, content}<br/>+ Authorization: Bearer JWT
  FastAPI->>FastAPI: JWT verify (HS256 -> RS256)
  FastAPI->>FastAPI: 3-layer rate limit (fail-CLOSED)
  FastAPI->>FastAPI: bind request.state.record_tenant_id

  FastAPI->>ChatRoute: dispatch submit_chat()
  ChatRoute->>ChatRoute: resolve_workspace_id (fallback to str(tenant))
  ChatRoute->>UseCase: AnswerQuestionCommand
  UseCase->>DB: BotRegistryService.lookup (Redis L1 -> DB)
  alt bot not found
    DB-->>UseCase: None
    UseCase-->>ChatRoute: HTTPException 404
    ChatRoute-->>Client: 404 bot_not_found
  end
  UseCase->>Job: create job_id (status=queued)
  UseCase->>Outbox: INSERT outbox(subject=chat.received.v1, payload)
  UseCase->>DB: INSERT request_logs(status=queued)
  UseCase-->>ChatRoute: ChatAcceptedResponse{job_id, status_url}
  ChatRoute-->>Client: 202 + job_id

  loop poll
    Publisher->>DB: SELECT … FOR UPDATE SKIP LOCKED LIMIT 1
    Publisher->>Stream: XADD chat.received.v1 (per-row tx)
    Publisher->>DB: mark_processed + COMMIT (atomic)
  end

  Stream->>Worker: XREADGROUP chat-worker-grp
  Worker->>Worker: ChatReceivedPayload.parse
  Worker->>Worker: bind_request_context(trace_id, tenant, bot)
  Worker->>DB: SystemConfigService.get_many(65 keys) -- batched
  Worker->>DB: ConversationRepository.get_history (limit=max_history)
  Worker->>Graph: graph.ainvoke(initial_state)
  Graph-->>Worker: GraphState (answer, citations, tokens, cost)
  Worker->>DB: update request_logs (status=done, chunks_used, top_score, cost)
  Worker->>DB: insert request_chunk_refs (one row per used chunk)
  Worker->>DB: upsert semantic_cache (exact_hash + embedding)
  Worker->>Outbox: INSERT chat.answered.v1
  Worker->>Job: update_status(done)
  Worker->>CB: dispatch ChatCompletedEvent (hooks: TokenUsageDB, TokenUsageRedis, QuotaThreshold)

  alt mode = callback
    Stream-->>Worker: chat.answered.v1 picked up by delivery adapter
    Worker->>Client: HTTP POST to callback_url (chat answer body)
  else mode = poll
    Client->>Job: GET /api/ragbot/jobs/{job_id}
    Job-->>Client: {status: done, answer: …}
  end
```

### 5.2 Ingestion Flow (document upload -> chunk -> embed -> store)

```mermaid
sequenceDiagram
  participant Client
  participant TestChat as test_chat.py (route)
  participant UC as IngestDocumentUseCase
  participant DocRepo as DocumentRepository
  participant Outbox
  participant Stream as Redis Stream document.uploaded.v1
  participant DocWorker as document_worker
  participant DocSvc as DocumentService
  participant Parser as parser_registry
  participant Chunker as smart_chunk
  participant Enricher as contextual_chunk_enrichment (Haiku)
  participant Embedder as zembed-1 (dimensions:1280)
  participant Store as PgVectorStore
  participant DB

  Client->>TestChat: POST /test/documents/upload<br/>(multipart: file + bot_id + channel)
  TestChat->>TestChat: 4-key resolve -> record_bot_id
  TestChat->>TestChat: content_hash_required + dedup check
  TestChat->>DocRepo: INSERT document(status=queued)
  TestChat->>Outbox: enqueue document.uploaded.v1
  TestChat-->>Client: 202 + {job_id, document_id}

  Outbox->>Stream: XADD (publisher loop)
  Stream->>DocWorker: XREADGROUP document-worker-grp
  DocWorker->>DocWorker: bind_request_context
  DocWorker->>DB: update job_repo (running)

  Note over DocSvc: 7-step ingest (U1-U7)

  DocWorker->>DocSvc: ingest(content, mime, metadata)
  DocSvc->>DocSvc: U1 VALIDATE size + dedup
  DocSvc->>Parser: U2 PARSE (mime detect -> pdf/xlsx/csv/md/kreuzberg)
  Parser-->>DocSvc: extracted text
  DocSvc->>DocSvc: U3 CLEAN (NFC + injection strip)
  DocSvc->>Chunker: U4 CHUNK (strategy analyze -> smart_chunk)
  Chunker-->>DocSvc: list[Chunk]
  DocSvc->>Enricher: U5 ENRICH (parent-child + CR via Haiku)
  Enricher-->>DocSvc: enriched chunks
  DocSvc->>DocSvc: U6 VN SEGMENT (underthesea, per-bot)
  DocSvc->>Embedder: U7 EMBED batch (zembed-1)
  Embedder-->>DocSvc: list[vector 1280-dim]
  DocSvc->>Store: upsert_chunks(record_document_id, chunks, record_tenant_id)
  Store->>DB: session_with_tenant -> SET LOCAL app.tenant_id
  Store->>DB: DELETE FROM document_chunks WHERE record_document_id
  Store->>DB: INSERT … VALUES (…) batched (one tx)
  Store-->>DocSvc: chunks_count

  DocWorker->>DB: update_status(done, chunks=N)
  DocWorker->>Outbox: INSERT document.ingested.v1
  DocWorker->>DB: audit_log row
```

### 5.3 Retrieval Flow (query -> embed -> vector search -> rerank -> grade -> LLM -> response)

```mermaid
sequenceDiagram
  participant Worker as chat_worker
  participant Graph as LangGraph
  participant Cache as PgSemanticCache
  participant LLM_S as Haiku (understand)
  participant Embedder as zembed-1 (query)
  participant Vec as PgVectorStore
  participant Rerank as ZeroEntropy zerank-2
  participant Grader as CRAG
  participant LLM_A as gpt-4.1-mini (answer)
  participant OG as OutputGuardrail

  Worker->>Graph: ainvoke(state)

  Graph->>Graph: Q1 guard_input (PII / injection scan)
  Graph->>Cache: Q2 cache_check (parallel with understand)
  Cache->>Cache: exact SHA256 match
  alt hit
    Cache-->>Graph: CachedResponse{answer, citations}
    Graph->>Graph: persist (write log + outbox) -> END
  else miss -> cosine fallback
    Cache->>Vec: SELECT … ORDER BY query_embedding <=> :qv LIMIT 1
    alt sim >= 0.97
      Cache-->>Graph: CachedResponse
      Graph->>Graph: persist -> END
    else
      Cache-->>Graph: None
    end
  end

  Graph->>LLM_S: Q3 understand_query (intent + condense merged 1-call)
  LLM_S-->>Graph: {intent: factoid|multi_hop|greeting|oos|…}
  alt intent in {oos, greeting}
    Graph->>Graph: persist (refusal or pre-template) -> END
  end

  Graph->>Graph: query_complexity (L1 heuristic)
  alt complex
    Graph->>LLM_S: adaptive_decompose (L3 LLM decomposer)
    LLM_S-->>Graph: {sub_queries: [...]}
  end

  Graph->>Graph: rewrite_and_mq_parallel (HyDE + multi-query)

  Graph->>Embedder: embed query (with abbrev expand + VN segment)
  Embedder-->>Graph: vec 1280
  Graph->>Vec: Q6 retrieve hybrid_search (RRF dense + BM25)
  Vec-->>Graph: candidates[N]  (top_k=10..50)

  Graph->>Graph: Q8 filter (min_score 0.005)
  Graph->>Graph: Q9 mmr_dedup_pre (cosine 0.88)
  Graph->>Rerank: Q10 rerank API call
  Rerank-->>Graph: reranked[top_n=4..6]
  Graph->>Graph: Q11 mmr_dedup_post

  Graph->>Grader: Q12 grade (CRAG structured output)
  alt adequate
    Grader-->>Graph: route=generate
  else retry & iter<2
    Grader-->>Graph: route=rewrite_retry -> back to retrieve
  end

  Graph->>LLM_A: Q14 generate (platform_rules + bot persona, chunks, history)
  LLM_A-->>Graph: answer + citations
  Graph->>OG: Q15 guard_output (prompt-leak shingle on platform-only; grounding judge; PII filter)
  alt blocked
    OG-->>Graph: GuardrailBlocked -> answer_type=blocked, return refusal
  else
    OG-->>Graph: pass
  end

  Graph->>Graph: Q16 reflect (skip for factoid)
  Graph->>Graph: Q17 persist (semantic_cache + outbox + request_logs)
  Graph-->>Worker: final state
```

### 5.4 SSE Streaming Flow

```mermaid
sequenceDiagram
  participant Client
  participant Route as chat_stream.py
  participant Cfg as SystemConfigService
  participant BRS as BotRegistryService
  participant Graph as LangGraph (in-request)
  participant Helper as _sse_helper.stream_real_llm
  participant LLM as gpt-4.1-mini (stream=True)

  Client->>Route: POST /api/ragbot/chat/stream<br/>(same body shape as /chat)
  Route->>Route: JWT-bound record_tenant_id (middleware-lifted)
  Route->>Cfg: get_bool(streaming_response_enabled, true)
  Route->>Cfg: get_bool(streaming_enabled, true)
  alt either OFF
    Route-->>Client: 403 Streaming is disabled via system_config
  end
  Route->>BRS: lookup(4-key)
  alt not found
    Route-->>Client: 404 bot_not_found
  end

  Route->>Route: build_pipeline_config (reuse test_chat helper)
  Route-->>Client: 200 + Content-Type: text/event-stream

  Route->>Graph: build_graph(stream=True)
  Graph->>Graph: run Q1-Q13 sync (no streaming yet)
  Graph->>Helper: yield first frame status (e.g. thinking)
  Helper-->>Client: data: {event:status, step:retrieve}

  Graph->>LLM: Q14 generate (stream=True)
  loop per token chunk
    LLM-->>Graph: token delta
    Graph->>Helper: yield replace frame
    Helper-->>Client: data: {event:replace, text:...}
  end

  Graph->>Graph: Q15 guard_output (post-stream check)
  alt blocked
    Graph->>Helper: yield blocked refusal frame
  end
  Graph->>Graph: Q17 persist
  Graph->>Helper: yield done frame
  Helper-->>Client: data: {event:done}
  Route-->>Client: HTTP close
```

---

## 6. Critical Integration Points & Data Consistency

### 6.1 Outbox Pattern (Transactional Event Log)

- Pattern: DB transaction → INSERT `outbox` row → publisher polls → Redis Stream publish.
- **Exactly-once on publish side** (post Agent N, 2026-05-16 — `outbox_publisher.py:97`):
  - Publisher calls `OutboxRepository.poll_one_for_update()` → `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`.
  - Inside the same tx: `XADD` → `mark_processed` → `COMMIT`. Crash mid-publish → tx aborts → row stays pending → no duplicate.
  - Two publisher replicas safe (SKIP LOCKED skips peer-held rows).
- DLQ: `outbox.retry_count >= max_retries` → moved to `outbox_dlq` table (forensic + manual replay).
- At-least-once on consumer side (XREADGROUP/XACK).

### 6.2 Semantic Cache Invalidation

```mermaid
flowchart LR
  Admin[Admin PATCH bot config<br/>/admin/bots/id] --> Audit[audit_log row]
  Audit --> CV[corpus_version bump<br/>CorpusVersionService]
  CV --> Outbox[outbox: bot.config_updated.v1]
  Outbox --> Stream[Redis Stream]
  Stream --> Listener[ai_config_listener]
  Listener --> L1[clear ragbot:model_resolver:*]
  Listener --> L2[clear ragbot:bot:...]
  Listener --> Cache[invalidate semantic_cache<br/>WHERE record_bot_id=X AND corpus_version < bumped]
```

- Cache key includes `bot_version_hash` (`DEFAULT_BOT_CACHE_VERSION_HASH_LEN` chars) — config change auto-misses stale rows.
- `LEGACY_CORPUS_VERSION_TAG` sentinel for migrated data without explicit version.

### 6.3 Token Quota Deduct (2-stage commit)

```mermaid
sequenceDiagram
  participant Graph
  participant DBHook as TokenUsageDbHook
  participant RedisHook as TokenUsageRedisHook
  participant Quota as QuotaThresholdNotifyHook
  participant DB
  participant Redis

  Graph->>Graph: ChatCompletedEvent emitted by persist node
  Graph->>DBHook: dispatch (one of N hooks)
  DBHook->>DB: INSERT bot_token_usage_log (tx-safe, append-only)
  Graph->>RedisHook: dispatch
  RedisHook->>Redis: INCRBY ragbot:bot:tokens_used:record_bot_id :n
  RedisHook->>Redis: EXPIREAT (month-aligned)
  Graph->>Quota: dispatch
  Quota->>DB: SELECT monthly_token_cap FROM tenants
  Quota->>Redis: GET counter
  alt counter > cap * threshold (default 80%)
    Quota->>Notifier: webhook_dispatcher.send (throttled DEFAULT_TOKEN_QUOTA_NOTIFY_THROTTLE_S)
  end
```

- DB is source of truth (reconcile job rebuilds Redis counter if drift).
- Redis is hot-path (fast pre-flight gate before LLM call).

### 6.4 ApiKeyPool Rotation

- `DBBackedApiKeyPoolFactory` reads `ai_keys` table per provider on boot.
- Hot-load: `bot.config_updated.v1` event invalidates factory cache → next request reads fresh pool.
- Per-key: rotate on 401/429 response. Pool exhaust (all keys returned bad) → request fails loud with `EmbeddingError` / `LLMError`.
- Provider format resolved via `format_litellm_model(model_name, provider)` (`model_resolver.py:95`) — Strategy+DI replacement for `f"{provider.code}/{model_name}"` (which special-cased `openai`); now controlled by `ai_providers.requires_prefix` boolean (alembic `010e`).

### 6.5 Document Ingest Finalize

- Atomic chunks INSERT (single tx, `pgvector_store.py:84`): DELETE-by-doc + INSERT-batch.
- `document.ingested.v1` outbox event emitted only after chunks committed.
- Cache invalidate triggered via corpus_version bump (cascades through 6.2).
- `request_logs` ingest row with `connect_id="ingest"` sentinel so chat dashboards filter it out.

### 6.6 Streaming + Outbox

- SSE endpoint does **not** use outbox — answer streams in-request to client over open connection.
- After stream completes, `persist` node still writes `request_logs` + `semantic_cache` + `chat.answered.v1` outbox row so analytics + delivery hooks fire identically.
- Streaming idempotency replay = out of scope (acknowledged in `chat_stream.py:18`).

---

## 7. Technical Debt & Improvement Notes

(Live state from `STATE_SNAPSHOT.md` + `reports/MULTI_AGENT_20260516/00_MASTER_VERDICT.md` — current as of 2026-05-16.)

### 7.1 Code shape

| Item | File | Lines | Plan | Effort |
|---|---|---|---|---|
| God-object `query_graph.py` | `src/ragbot/orchestration/query_graph.py` | 5488 | Agent I `_chat_common.py` extract | 17h |
| SRP violation `document_service.py` | `src/ragbot/application/services/document_service.py` | 3396 | (Tier 3 defer) | — |
| `chat_worker.py` config build duplication | `src/ragbot/interfaces/workers/chat_worker.py` | 1361 | Agent I (`_chat_common`) — also extract pipeline_config | — |

### 7.2 Performance / DB (Agent F P0/P1)

| Issue | File:line | Sev | Status |
|---|---|---|---|
| Outbox exactly-once was broken pre-Agent-N | `outbox_publisher.py:54-121` | P0 | FIXED 2026-05-16 (per-row tx + SKIP LOCKED) |
| Semantic cache race condition | `semantic_cache.py:188-452` | P0 | OPEN — add FOR UPDATE on lookup |
| Model resolver N+1 cascade (+400ms P99) | `model_resolver.py:750+` | P0 | OPEN — batch `get_models_by_ids` (8h) |
| Soft-delete not filtered in vector search | `pgvector_store.py:262` | P0 | OPEN — add `d.deleted_at IS NULL` (1h) |
| Conversation history N+1 (Python trim) | conversation repo | P0 | OPEN — JOIN+LIMIT (3h) |
| 5 perf indexes added | alembic `010c` | P1 | shipped (3 remaining indexes covered by existing) |

### 7.3 RLS & Security

| Item | Status |
|---|---|
| RLS policy `tenant_isolation` `FORCE`d on 20+ tables | shipped alembic `0069` |
| `ragbot_app` non-superuser role activation | OPEN — Agent F C3 deferred (needs ops to set `DATABASE_URL_APP`) |
| `_resource_ownership.py` typo `tenant_id` → `record_tenant_id` (AUTH-1) | FIXED Wave A2 `eef9991` |
| `system_prompt_override` field removed (INJ-5) | FIXED `ce8b6f8` |
| `bot_id` + `channel_type` strict regex (INJ-6) | FIXED `ce8b6f8` |
| `.env` permissions 0644 → 0600 | FIXED ops chmod |
| JWT clock skew leeway (AUTH-7) | FIXED `ce8b6f8` |
| AUTH-5 `cache_reload` permission gating | FIXED `e9dc6c4` + alembic `010b` |
| 42 remaining (HIGH 6, MED 28, LOW 8) | OPEN — `reports/SECURITY_AUDIT_20260516/` |

### 7.4 Async LLM wire-up

- D1 `chat_async_worker` shipped (`a7579d6`) — consumes `chat.received.v1` on alternate consumer group.
- D2 `/api/ragbot/test/chat-async` POST + GET endpoints shipped (`19fb198`).
- D3 routing-compat retry shipped (`67c3adf`).
- **PENDING**: wire prod `/api/ragbot/chat` → async LLM queue (biggest perf unlock, 12h plan ready Agent D).
- Current /chat path: synchronous worker (chat_worker) — p95 21s post-fixes (legalbot 23.23s / spa 22.23s) versus T2 8s GA SLA target.

### 7.5 Test debt

- 481 unit tests PASS / 1 SKIP / 1 pre-existing baseline FAIL on changed-dirs sweep.
- 25 unit tests failing — `H` plan ready (`reports/MULTI_AGENT_20260516/agent_H_test_fix_plan.md`):
  - Category F version-ref scrub (`_legacy` references in tests)
  - `node_rewrite` restore
  - `test_state_has_speculative_slots` baseline fail
- Coverage: full test suite ~2000+ pass rate target — Coder S/T agents handling.

### 7.6 Pipeline / RAG quality

| Item | Status |
|---|---|
| Q18 CRAG over-strict on compound queries | OPEN — Agent G 30min tune (add `regulatory` intent to fallback score) |
| HALLU=0 sacred | preserved (40Q sanity, 0 fabricate) |
| Cache hit rate | ~25-30% active (alembic `0105` fixed dim mismatch) |
| HNSW idx_scan | active post alembic `0108` (was dead — 0 scans / 22MB) |
| Article-aware filter | active (`d589415`) |
| Spa Q8 retrieval | FIXED (chunks=6, top=0.939) |

### 7.7 Architecture (Strategy+DI)

| Item | Status |
|---|---|
| Reranker preflight Strategy+DI refactor | shipped `ce9e12e` |
| Vector store factory + provider format | OPEN — Agent K plan ready (5h) |
| `requires_prefix` column for provider formatting | shipped alembic `010e` |
| `guardrail_rules` DB migration (config-driven moderation) | OPEN — Agent J plan ready (6.75 days) |

### 7.8 Misc operational

- 587 orphan `guardrail_events` deleted (forensic loss accepted) — alembic `0107c` orphan reset.
- Multi-format upload (PDF / DOCX / CSV) — Kreuzberg engine seeded alembic `0097`; full integration test pending.
- 14 migrations (`0098`-`010e`) require ops to `alembic upgrade head` per user mandate (auditor did NOT run upgrade this session).
- 4× document-worker scale (1 primary + 3 secondary via `ragbot-document-worker@.service` systemd template).

---

## 8. Appendix

### 8.1 Endpoint surface (from `interfaces/http/router.py`)

| Method | Path | Module | RBAC |
|---|---|---|---|
| GET | `/health` | `health.py` | public |
| GET | `/health/models` | `health_models.py` | public (verifies AI providers) |
| GET | `/honeypot/*` | `honeypot.py` | public-by-design (trap) |
| POST | `/api/ragbot/chat` | `chat.py:40` | `chat:submit` (level >= 60) |
| POST | `/api/ragbot/chat/stream` | `chat_stream.py:80` | `chat:stream` |
| POST | `/api/ragbot/feedback` | `chat.py:96` | `chat:feedback` |
| POST | `/api/ragbot/feedback/thumbs` | `feedback.py` | `chat:feedback` |
| POST | `/api/ragbot/documents/upload` | `documents.py` | `bot:write` |
| GET | `/api/ragbot/jobs/{job_id}` | `jobs.py` | tenant-scoped |
| POST | `/api/ragbot/sync/documents` | `sync.py` | `bot:write` |
| POST | `/api/ragbot/test/chat` | `test_chat.py` | tenant-scoped (demo) |
| POST | `/api/ragbot/test/chat-async` | `chat_async.py` (G26 — D2) | tenant-scoped |
| GET | `/api/ragbot/test/chat-async/{job_id}` | `chat_async.py` | tenant-scoped |
| GET | `/api/ragbot/test/tokens/self` | `test_chat.py:95` | public dev-token endpoint |
| POST | `/api/ragbot/test/documents/upload` | `test_chat.py` | tenant-scoped |
| * | `/api/ragbot/admin/ai/*` | `admin_ai.py` | tenant_admin+ |
| * | `/api/ragbot/admin/bots/*` | `admin_bots.py` | tenant_admin+ |
| * | `/api/ragbot/admin/tenants/*` | `admin_tenants.py` | super_admin |
| * | `/api/ragbot/admin/policy/*` | `admin_policy.py` | admin |
| * | `/api/ragbot/admin/tenant-policy/*` | `admin_tenant_policy.py` | tenant_admin+ |
| * | `/api/ragbot/admin/audit/*` | `admin_audit.py` | super_admin |
| * | `/api/ragbot/admin/gdpr/*` | `admin_gdpr.py` | super_admin |
| * | `/api/ragbot/admin/metrics/*` | `admin_metrics.py` | admin |
| * | `/api/ragbot/admin/analytics/*` | `admin_analytics.py` | admin |
| * | `/api/ragbot/admin/notify-channel` | `admin_notify.py` | admin |
| POST | `/api/ragbot/admin/cache/reload` | `admin_*` | `bot:cache_reload` (>= 60, post alembic `010b`) |

### 8.2 Request body shape (4-key)

```json
{
  "workspace_id": "sales",
  "bot_id": "support-v1",
  "channel_type": "web",
  "connect_id": "user-123",
  "content": "Cho tôi xin bảng giá dịch vụ A",
  "history_limit": 4,
  "external_message_id": "msg-uuid-from-upstream",
  "mode": "poll",
  "callback_url": null
}
```

JWT bearer: `record_tenant_id` UUID claim REQUIRED. `workspace_id` optional (fallback to `str(record_tenant_id)`).

### 8.3 Common operational commands

```bash
# Reload system_config caches (super_admin token)
curl -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  http://localhost:3004/admin/cache/reload

# Run tests
.venv/bin/pytest tests/ -x --tb=short

# Run service-token mint (dev only)
curl -X GET http://localhost:3004/api/ragbot/test/tokens/self \
  -H "X-API-Key: $APP_API_TOKEN"

# Alembic upgrade to head
.venv/bin/alembic upgrade head

# Inspect outbox backlog
psql "$DATABASE_URL" -c "SELECT status, COUNT(*) FROM outbox GROUP BY status;"

# Spot-check chunk count for a bot
psql "$DATABASE_URL" -c "SELECT record_bot_id, COUNT(*) FROM document_chunks GROUP BY record_bot_id ORDER BY 2 DESC LIMIT 10;"

# Verify HNSW index activity
psql "$DATABASE_URL" -c "SELECT relname, idx_scan, idx_tup_read FROM pg_stat_user_indexes WHERE relname LIKE 'ix_document_chunks_embedding%';"

# Cost audit (replay JSONL session logs)
python scripts/cost_audit.py today
python scripts/cost_audit.py model-mix --days 7
```

### 8.4 Default thresholds & constants (excerpt from `shared/constants.py`)

| Constant | Default | Purpose |
|---|---|---|
| `SEMANTIC_CACHE_THRESHOLD` | 0.97 | Cosine cutoff for L2 cache hit |
| `DEFAULT_EF_SEARCH` / `MAX_EF_SEARCH` | 64 / 200 | HNSW search width |
| `DEFAULT_TOP_K` | 10 | Initial dense retrieve |
| `DEFAULT_RERANK_TOP_N` | 4-6 | Post-rerank keep |
| `DEFAULT_RERANK_CLIFF_GAP_RATIO` | 0.5 | Adaptive cliff detect for rerank short-circuit |
| `DEFAULT_RERANKER_MIN_SCORE` | 0.05 | Reject if below |
| `DEFAULT_GENERATION_TEMPERATURE` | 0.1 | Conservative for HALLU=0 |
| `DEFAULT_MAX_TOKENS_TOTAL` | 4096 | Per-turn budget |
| `DEFAULT_GROUNDING_CHECK_ENABLED` | True | Q15 grounding judge |
| `DEFAULT_CRAG_FALLBACK_COUNT` | 2 | Max CRAG retry |
| `DEFAULT_MULTI_QUERY_N_VARIANTS` | 3 | Multi-query expansion |
| `DEFAULT_MAX_HISTORY` | 4 | Conversation turns |
| `DEFAULT_ZEROENTROPY_EMBEDDING_DIM` | 1280 | Matryoshka chosen dim |
| `DEFAULT_STATEMENT_TIMEOUT_MS` | (env-tunable) | Per-query timeout in `session_with_tenant` |
| `WORKSPACE_SYSTEM_SLUG` | `"system"` | Tenant-level / forensic workspace marker |

---

*Document generated 2026-05-16 against commit `862ff12` (post multi-agent R3). Verify section deltas against `STATE_SNAPSHOT.md` after each merge — current state always wins. Cross-reference file:line citations against live source for re-anchor on next refactor.*
