# RAGBOT MASTER — Architecture Reference Index

> **Purpose**: TOC pointing to detailed docs. For current state see [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md). For sacred rules see [`CLAUDE.md`](CLAUDE.md).
> **Detail sub-files**: [`docs/master/01-A`](docs/master/01-A-foundation-architecture.md) through [`15-O`](docs/master/15-O-anti-hallu-tuning.md).

---

## 1. Project state

> **Latest state**: see [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md) (always-updated). **2026-06-10 — Expert Build Phase 4: Wave 1-6 shipped** (17 feature/fix commit, anchor `648c452`, alembic head **0200**). W1 STOP-THE-BLEED closed 3 P0: RLS layer-3 wired (bootstrap hook + `workspace_id` GUC + non-vacuous leak-test), API-key encryption (AES-GCM dual-read + backfill), transactional-inbox exactly-once (`event_inbox`, process-then-mark + real DLQ); + DI-parity (`graph_assembly.py`), sacred#10 effective-prompt endpoint, BotLifecycleService purge+reaper. W2 workspaces ENTITY (slug→row, alembic 0199) + ingest-quota wired + per-tenant fairness. W3 chunking carve-outs (VN-legal-prose, proposition-connector) + Block-feed S1 plumbing. W4 temp-0 router choke-point + grounding-degraded counter + math_lockdown cleanup. W5/W6 config-governance + feedback-loop wire. **unit 5907 pass · graded 85/91 · HALLU=0 · 0 regression** (verified LIVE post-migrate+cache-clear+restart). Program memory: [`program/EXPERT-STATE-REPORT.md`](program/EXPERT-STATE-REPORT.md) + [`program/EXPERT-PLAN.md`](program/EXPERT-PLAN.md) + 10 ADR. *(Prior 2026-05-19 Wave A-G state → `STATE_SNAPSHOT_HISTORY.md`.)*
> **2026-06-16 — God-file refactor + CRM analytics** (behavior-preserving, alembic head now **0220**). 5 god-files split into packages (all modules ≤1.2k): `shared/chunking.py`→`shared/chunking/` (`__init__.smart_chunk` + `strategies.py` + `analyze.py` + `csv_chunker.py` + `blocks.py` + `vn_structural.py`); `application/services/document_service.py`→`document_service/` (`ingest_core.py::_IngestMixin.ingest()` orchestrator + `ingest_stages*.py` U1–U7 phases via `_IngestCtx` dataclass + `ingest_helpers.py` + `text_processing.py`); `application/services/model_resolver.py`→`model_resolver/` (`__init__` + `_helpers.py`); `interfaces/http/routes/test_chat.py`→`routes/test_chat/` (URL prefix `/api/ragbot/test/` UNCHANGED); `interfaces/workers/chat_worker.py`→`workers/chat_worker/`; CRAG grade filters extracted to `orchestration/retrieval_filter.py`. NEW CRM analytics read-layer (`crm_analytics_service.py` + `routes/crm.py` over `request_logs`+`request_steps`+`monitoring_log`, dashboard `static/crm.html`, alembic 0219 `token_budgets`) + durable `monitoring_log` (alembic 0217) + booking-precedence sysprompt rule (0218) + EN multi-query language_pack parity (0220).
> **Score axis**: detail per axis in [`docs/master/03-C-cross-axes.md`](docs/master/03-C-cross-axes.md).

---

## 2. What ragbot DOES

A multi-tenant multi-industry multi-language RAG chatbot platform.

- **Drop-in corpus per bot** — upload PDF / Excel / Sheets / Markdown → parse + chunk + embed → grounded answers
- **Multi-tenant + multi-workspace** — 1 tenant tách N workspace (slug pass-through, platform không quản lifecycle)
- **Vietnamese-first** — `underthesea` segmentation; Jina v3 multilingual; sysprompt VN
- **Domain-neutral** — 0 brand / industry literal in tracked source
- **Multi-channel** — web / Zalo / LINE / Viber / Telegram via channel adapter port
- **App-mindset** — bot owner's `system_prompt` = single source of truth. Application NEVER injects template text NEVER overrides LLM answer
- **Provider-agnostic** — LLM / embedder / reranker swap qua `ai_providers` + `ai_models` + `bot_model_bindings` DB tables

---

## 3. Hard contracts

> Full sacred rules: [`CLAUDE.md`](CLAUDE.md) — sections "IDENTITY RULE 4-KEY", "Zero-hardcode", "Strategy + DI", "Domain-neutral", "App-mindset", "Broad-except policy".
> Anti-HALLU tuning: [`docs/master/15-O-anti-hallu-tuning.md`](docs/master/15-O-anti-hallu-tuning.md).

- **Singleton compiled graph + state-lifted per-request params** (TASK-10, 2026-05-09): `build_graph()` invoked once via `get_graph()` async-locked singleton. Per-request fields (`step_tracker`, `bot_system_prompt`, `kg_service`, `session_factory`) flow through `GraphState`, not closure — multi-tenant cache-safe.

---

## 4. Stack

- **Runtime**: Python 3.12+ (type-strict, async-first)
- **HTTP**: FastAPI + Uvicorn (4 workers) — REST + SSE streaming
- **Orchestration**: LangGraph — 24-step query pipeline
- **Vector**: pgvector (HNSW 1024-dim) + tsvector BM25 + RRF fusion
- **Tenant isolation**: app-layer repo scoping + PostgreSQL Row-Level Security (alembic 0069 — `tenant_isolation` policy `FORCE`d on 20 tables; effective only when app connects as non-superuser, see `T1.S1b`)
- **AI bindings**: per-bot via `bot_model_bindings(purpose)` — embedding (ZeroEntropy `zembed-1` 1280-dim matryoshka) / rerank (ZeroEntropy `zerank-2` cross-encoder) / llm_answer (`gpt-4.1-mini` — admin mandate, NO Haiku for answer) / llm_enrich (`claude-haiku-4-5` ingest-only)
- **Cache**: Redis L1 + pgvector L2 @ 0.97 + Anthropic prompt-cache
- **Events**: Redis Streams + dedup ledger (NOT NATS)
- **Observability**: structlog JSON + Prometheus + `request_steps` (NOT Langfuse)
- **Auth**: JWT bearer + 7-tier RBAC; **Deploy**: Docker Compose + systemd
- **Tests**: pytest with structured-output assertions

---

## 5. 32+ step adaptive pipeline (Phase A-D shipped 2026-05-12)

7 ingest steps (U1–U7) + 25-32 query steps (Q1–Q32 adaptive). Adaptive Query Router L1 heuristic + L3 LLM decomposer gates expensive nodes (CRAG retry, HyDE, multi-query fanout) per intent. Detail: [`RAGBOT_STEP_PIPELINE.md`](RAGBOT_STEP_PIPELINE.md).
Code: [`src/ragbot/orchestration/query_graph.py`](src/ragbot/orchestration/query_graph.py) + [`src/ragbot/application/services/document_service/`](src/ragbot/application/services/document_service/) (package — `ingest_core.py::_IngestMixin.ingest()` + `ingest_stages*.py`).
State: [`src/ragbot/orchestration/state.py`](src/ragbot/orchestration/state.py). Step→code mapping: [`docs/master/11-K-pipeline-code-mapping.md`](docs/master/11-K-pipeline-code-mapping.md).

**Worker scale**: 4 document-worker instances active (1 primary + 3 secondary via `ragbot-document-worker@.service` template); Redis Streams XREADGROUP fan-out. Haiku enrichment concurrency 20 (Anthropic Tier 1 safe); skip enrich for doc <50K chars.

---

## 6. Architecture layout (Hexagonal / DDD)

```
src/ragbot/
├── domain/              # Entities, value objects
├── application/
│   ├── ports/           # 15+ Protocol/ABC interfaces
│   ├── services/        # Use cases (no I/O; depends on Ports)
│   └── dto/             # Pydantic schemas
├── infrastructure/
│   ├── llm/             # dynamic_litellm_router (prompt-cache + failover)
│   ├── embedding/       # litellm + openai + null + registry
│   ├── reranker/        # jina + litellm + viranker + null + registry
│   ├── vector/          # pgvector_store (HNSW + BM25 + RRF)
│   ├── cache/           # redis L1 + pg semantic L2
│   ├── graph/           # graph_retriever (GraphRAG)
│   ├── parsers/         # pdf / excel / sheets / md + registry
│   ├── tokenizer/       # vi (underthesea) / en + registry
│   ├── guardrails/      # input + output
│   ├── pii/             # vn_regex_pii_redactor
│   ├── repositories/    # bot / document / tenant / ...
│   ├── delivery/        # callback channel adapter
│   ├── notify/          # webhook_dispatcher (error alert)
│   └── events/          # redis_streams_bus
├── orchestration/       # query_graph + state
├── interfaces/
│   ├── http/            # routes / middlewares / schemas
│   └── workers/         # chat / document / outbox
└── shared/              # constants, errors, types, rbac, api_key_pool, ...
```

Detail: [`docs/master/01-A-foundation-architecture.md`](docs/master/01-A-foundation-architecture.md).

---

## 7. Configuration sources (priority — top wins)

| Source | Scope |
|---|---|
| `bot_model_bindings` | per-bot × per-purpose (authoritative) |
| `bots.plan_limits` JSONB + `bots.{system_prompt, oos_answer_template}` | per-bot |
| `tenants.{rate_limit_per_min, monthly_token_cap, allowed_origins}` | per-tenant |
| `system_config` (DB + Redis cache) | global thresholds, TTLs |
| [`shared/constants.py`](src/ragbot/shared/constants.py) | compile-time fallback |
| `.env` | secrets + pool sizing |

Force-reload after editing `system_config`: `POST /admin/cache/reload` with super-admin token.

---

## 8. T1 / T2 / T3 capabilities

T1 Smartness / T2 Cost+Perf / T3 Architecture invariants — current scores & capability matrix in [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md). Architecture detail per axis in [`docs/master/03-C-cross-axes.md`](docs/master/03-C-cross-axes.md).

---

## 9. Workspace concept (V10 pass-through · + W2-D2 entity 2026-06-10)

**Triết lý**: Platform là core, **KHÔNG quản lý workspace lifecycle**. Tenant truyền sao platform lưu vậy. Validate format slug only — không CRUD, không 404. **`bots.workspace_id` slug vẫn là identity canonical (4-key tuple KHÔNG đổi).**

**W2-D2 (alembic 0199)**: thêm `workspaces` ENTITY BÊN CẠNH slug — `(id, record_tenant_id FK, slug, name, deleted_at)` UNIQUE(tenant,slug), RLS scope `record_tenant_id`, backfill từ distinct `bots.workspace_id`. Entity là tham chiếu THÊM cho RBAC/quota/lifecycle (`WorkspaceRepository.lookup/list/ensure`); KHÔNG thêm `record_workspace_id` vào tuple. RBAC vẫn global-per-tenant (workspace_members defer). Detail: `program/decisions/ADR-W2-D2-workspace-entity.md`.

**4-key bot identity** (sacred — full rule trong [`CLAUDE.md`](CLAUDE.md) "IDENTITY RULE"):
- **Body 2-key**: `(bot_id, channel_type)` REQUIRED + `workspace_id` OPTIONAL slug
- **JWT bearer**: `record_tenant_id: UUID` REQUIRED claim
- **Internal resolve**: `(record_tenant_id, workspace_id, bot_id, channel_type)` → `record_bot_id` UUID
- **Slug rules**: `^[a-zA-Z0-9-]+$`, 1-64 chars; missing → fallback `str(record_tenant_id)`; invalid → 422
- **Cross-workspace isolation**: ngầm qua `record_bot_id` UUID — 2 workspace cùng `bot_id` resolve khác UUID, data tự scope

Detail + edge cases: [`docs/master/03-C-cross-axes.md`](docs/master/03-C-cross-axes.md).

---

## 10. Security posture

- **Auth**: JWT bearer versioned + revoke on `api_tokens.version` bump
- **RBAC**: 7-tier numeric (`super_admin=100 / admin=80 / tenant_admin=60 / editor=40 / viewer=20 / guest=0`); 35+ routes gated
- **CORS**: per-tenant strict whitelist
- **Rate limit**: sliding window per-token + per-tenant + IP-DDoS pre-auth
- **Anti-abuse**: UA filter + honeypot + 4xx-ratio guard + soft-throttle
- **PII**: `vn_regex_pii_redactor` (input + output)
- **Tenant isolation**: 4-key DB unique + JWT lift + repository filter + PostgreSQL Row-Level Security `FORCE`d. **W1-D3 (2026-06-10)**: layer-3 wired — `attach_rls_session_hook` attached in bootstrap (`create_rls_session_factory`), per-transaction `SET LOCAL app.tenant_id` + **`app.workspace_id` GUC** (`workspace_id_ctx`, bound at all transports), non-vacuous leak-test (`tests/integration/test_rls_leak_2tenant.py` asserts connection role ≠ bypassrls). **Activation chờ ops**: flip `DATABASE_URL_APP` → `ragbot_app` NOBYPASSRLS role (hook là no-op an toàn dưới superuser cho tới khi flip — `program/waves/W1-OPS-CHECKLIST.md`).
- **Secrets**: `.env` gitignored + `PROVIDER_API_KEYS_JSON` provider-agnostic. **W1-KEY (alembic 0196/0197)**: provider API keys **encrypted at rest** (`api_keys.value_encrypted` AES-GCM via `RAGBOT_CONFIG_KEK`; dual-read encrypted-first, `value_plain` NULLed; Redis cache stores ciphertext).

Detail: [`docs/master/05-E-cross-cutting-patterns.md`](docs/master/05-E-cross-cutting-patterns.md).

---

## 11. Test coverage & roadmap

- Tests + coverage: [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md) "Test" section.
- Roadmap + history: [`docs/master/13-M-roadmap-history.md`](docs/master/13-M-roadmap-history.md). Next-action candidates trong [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md).

---

## 12. Quick reference

### Endpoints
- `POST /api/ragbot/chat` — chat (4-key body + JWT)
- `POST /api/ragbot/chat/stream` — SSE streaming
- `POST /api/ragbot/feedback` — thumbs up/down
- `POST /api/ragbot/sync/documents` — ingest from upstream
- `GET  /admin/notify-channel` · `PATCH /admin/notify-channel`
- `GET  /admin/tenants` (RBAC 60+)
- `GET  /health` · `GET /health/models` (admin)

### Body shape (4-key)
```json
{ "workspace_id": "sales", "bot_id": "support-v1",
  "channel_type": "web", "connect_id": "user-123", "question": "..." }
```
JWT bearer: `record_tenant_id` UUID claim REQUIRED.

### Common ops
```bash
# Reload system_config caches
curl -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  http://localhost:3004/admin/cache/reload

# Run tests
.venv/bin/pytest tests/ -x --tb=short

# Smoke health
curl -sf http://localhost:3004/health | jq
```

---

## 13. Anti-HALLU tuning

9-layer default config (temperature, grounding, chunk quality, self-correction, retrieve, generation, chunking, cache, sysprompt). Detail + per-bot override matrix: [`docs/master/15-O-anti-hallu-tuning.md`](docs/master/15-O-anti-hallu-tuning.md).

---

## 14. Threshold / strategy default — 4-source flow

Đổi default reranker / cliff / grounding / cache phải sync **4 nơi**, nếu không config drift gây silent fail (đã sai 2026-05-07: lower constant nhưng không UPSERT live system_config → fallback constant không kích hoạt → bot tiếp tục im lặng).

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. constants.py  DEFAULT_*                                       │
│    └─ source of truth code; thêm comment WHY recalibrate         │
│                                                                  │
│ 2. bot_limits.py  PLAN_LIMIT_SCHEMA[key]["default"]              │
│    └─ MUST reference constant (no hardcode literal —             │
│       vi phạm CLAUDE.md zero-hardcode rule)                      │
│       Validate per-bot override range (min/max)                  │
│                                                                  │
│ 3. init_system_config.py  seed value                             │
│    └─ Fresh deployment loader                                    │
│                                                                  │
│ 4. alembic/versions/<N>_<name>.py  UPSERT system_config          │
│    └─ Existing deployment: row đã tồn tại từ trước → constant   │
│       fallback không bao giờ kích hoạt → cần migration UPSERT    │
│       (precedent: alembic 0067 recalibrate_reranker_defaults)    │
└──────────────────────────────────────────────────────────────────┘
                ↓
┌──────────────────────────────────────────────────────────────────┐
│ chat_worker/ + test_chat/ pipeline_config builder               │
│    "key": resolve_bot_limit(bot_cfg, "key", system_default=...)  │
│    └─ MUST go through resolve_bot_limit so per-bot tuning works  │
│       Source-grep TDD test: tests/unit/test_pipeline_config_     │
│       per_bot_resolve.py                                         │
└──────────────────────────────────────────────────────────────────┘
                ↓
            runtime _pcfg(state, key, ...)
```

**Resolve chain runtime** (cao → thấp):

1. `bots.threshold_overrides[key]` (per-bot JSONB; bot owner explicit tune)
2. Dedicated `bots.<column>` (e.g. max_documents)
3. `bots.plan_limits[key]` (per-bot JSONB; plan-tier override)
4. `system_config.<key>` row (live UPSERT; deployment-wide)
5. `PLAN_LIMIT_SCHEMA[key]["default"]` (validation default)
6. `constants.DEFAULT_<KEY>` (final fallback when above empty)

**Numeric semantic**: `resolve_bot_limit` uses `max(bot_val, system_default)` — bot can RAISE above system floor (be stricter) but cannot LOWER below it. To loosen the floor for a tenant the system_config value must drop. Intentional safety semantic.

**Verify command sau khi đổi**:

```bash
psql "$DATABASE_URL" -c "SELECT key, value, value_type FROM system_config WHERE key = '<key>';"
.venv/bin/python -m pytest tests/unit/test_pipeline_config_per_bot_resolve.py
```

## 15. Reference docs

- [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md) — current state, always-updated (READ FIRST in new session)
- [`CLAUDE.md`](CLAUDE.md) — sacred rules cho Claude Code agents
- [`RAGBOT_STEP_PIPELINE.md`](RAGBOT_STEP_PIPELINE.md) — 24-step skill+audit reference
- [`README.md`](README.md) — quick start
- [`docs/master/01-A` → `15-O`](docs/master/) — chapter chi tiết per axis
- [`docs/BOT_OWNER_TOOLKIT.md`](docs/BOT_OWNER_TOOLKIT.md) — bot owner self-serve guide
- [`reports/`](reports/) — load-test verdicts + audit reports
- [`plans/`](plans/) — implementation plans
