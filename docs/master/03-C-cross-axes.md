# PHẦN C — 3 TRỤC NGANG

## 12. Security & Multi-Tenancy

### 12.1 Threat Model

1. Cross-tenant data leak (DB, vector, cache, log, trace).
2. Prompt injection (direct từ user, indirect qua doc).
3. Jailbreak (bypass moderation).
4. PII leakage (output chứa PII không đáng có).
5. Data exfiltration (trích xuất corpus qua smart query).
6. DoS / cost blow-up (query tốn token cực lớn).
7. Credential leak (secrets trong code/log).

### 12.2 Tenant Isolation 5 Layer

1. **Database row-level security** (Postgres RLS policy với tenant_id session variable).
2. **Repository layer** enforce tenant filter — throw nếu thiếu.
3. **Vector store** payload filter tenant_id, có index.
4. **Cache keys** prefix `tenant:{id}` mandatory.
5. **Log/trace** bind tenant_id vào context, redact nếu cross-tenant.

**Red-team test** bắt buộc: gửi request JWT tenant A, payload chứa doc_id tenant B → phải block.

### 12.3 Prompt Injection Defense (Layered)

**5 lớp độc lập**:
1. **Input classifier** (Llama Guard 3 / Lakera) — detect injection phổ biến.
2. **Doc scan at ingestion** — detect pattern "ignore previous instructions", flag/reject.
3. **Context sandboxing** — wrap XML, system prompt "data not instruction".
4. **Output check** — dấu hiệu tuân theo injection (revealing system, off-topic) → block.
5. **Canary tokens** — chèn token bí mật vào system prompt, fail nếu leak trong output.

### 12.4 PII Redaction

Tools chuyên dụng (Presidio) redact PII:
- Trước khi log.
- Trước khi cache.
- Trước khi trace.
- Trước khi gửi provider không trust.

Reversible tokenization nếu cần restore — map token ↔ PII trong vault, TTL ngắn.

### 12.5 Output Moderation

Tách 2 policy:
- **Input** fail → HTTP 400 (user biết vi phạm).
- **Output** fail → HTTP 500 generic (ẩn LLM đã generate xấu).

Phân biệt quan trọng vì lộ logic moderation = tiết lộ cách bypass.

### 12.6 Secrets Management

- Không bao giờ trong code, .env commit, config file.
- Vault/Secrets Manager, inject runtime qua env hoặc sidecar.
- Rotation: API keys rotate 90 ngày.
- CI scan: gitleaks, trufflehog mandatory.
- **Automation**: cron rotate weekly, giữ old key 24h để in-flight requests hoàn tất.

### 12.7 Rate Limiting & Token Budget

2 layer:
- **Rate limit HTTP**: per (tenant, user, route) — chống flood.
- **Token budget**: per tenant per month. Middleware check trước LLM call. Soft warn 80%, hard block 100%.

Token budget không chỉ cost — còn chống DoS và runaway loop.

### 12.8 Auth

- **Service-to-service**: OAuth2 client credentials.
- **User**: JWT RS256 với short expiry + refresh.
- **Bot platform**: HMAC signature verify webhook.
- **Authorization**: RBAC + tenant scope.

---

## 13. Observability

### 13.1 Three Pillars

- **Trace**: mỗi request = 1 trace; mỗi stage = 1 span; parent-child rõ.
- **Metric**: RED (Rate, Errors, Duration) + RAG-specific.
- **Log**: structured JSON với context propagation.

Trace ID xuyên suốt từ API gateway qua worker, LLM call, DB query.

### 13.2 LLM-Specific Tracing

Khác system trace:
- Input prompt (redact PII).
- Output (full).
- Model, temperature, max_tokens.
- Token counts (prompt, completion).
- Cost USD.
- Latency per LLM call.
- Tool calls với args + results.

Tool: **structlog** (current) cho application logging; **OpenTelemetry** cho system trace; merge qua trace ID. Langfuse planned for LLM-specific observability.

### 13.3 RAG-Specific Metrics

- `retrieval_recall_at_k` (shadow eval).
- `rerank_latency_seconds` histogram.
- `grade_score_distribution`.
- `iteration_count_histogram` (detect loop).
- `citation_validation_fail_total`.
- `cache_hit_ratio` per layer.
- `tokens_per_request` histogram.
- `cost_usd_per_request` histogram.
- `strategy_selection_override_total` (AdapChunk cross-check).
- `reranker_circuit_state` (0=closed, 1=open, 2=half).
- `canary_leak_total` (critical).

### 13.4 Structured Logging Standard

JSON với fields chuẩn:
- `timestamp`, `level`, `service`, `version`.
- `trace_id`, `span_id`.
- `tenant_id`, `bot_id`, `conversation_id`, `user_id`.
- `event`, `message`.
- Custom fields per event.

**Không** log prompt/response full ở INFO (storage + PII). Dùng DEBUG level hoặc dedicated trace store (Langfuse planned).

### 13.5 Cost Attribution

- `cost_usd` per request, aggregate lên conversation, bot, tenant.
- Breakdown: LLM, embedding, rerank, OCR.
- Dashboard: top tenant/bot theo cost, anomaly detection.

### 13.6 Silent Degradation Detection

Degradation thường âm thầm:
- Reranker timeout → fallback dense, latency giảm nhưng quality giảm.
- Cache hit tăng giả (cache poisoning).
- LLM provider đổi model behind the scenes.

Detect:
- Shadow eval metric trend.
- Alert khi metric drop > 2σ so baseline 7 ngày.
- Canary tests định kỳ (mỗi giờ): bộ query biết answer, so sánh.

### 13.7 Alerting Rules (mandatory)

- p99 latency > SLA × 2.
- Error rate > 1%.
- Cache hit ratio < baseline - 10%.
- Shadow eval faithfulness drop > 3%.
- Budget usage > 80% any tenant.
- Token usage spike > 3σ.
- Tool call failure rate > 5% any tool.
- **Cost per request > 3× baseline 7d** (anomaly detection).
- **Canary token leak detected** (critical).

---

## 14. Lifecycle & Event-Driven

### 14.1 Why Event-Driven

1. **LLM latency không ổn**: 2–30s, hold HTTP worker tốn.
2. **Debounce**: gộp nhiều message user thành 1 call.
3. **Multi-channel delivery**: Zalo, Telegram, Web — không phải tất cả sync.
4. **Resumability**: worker crash giữa chừng resume.
5. **Backpressure**: burst traffic không sập.

### 14.2 Event Types & Contracts

Event là **contract ổn định**:
- Version (`chat.received.v1`).
- Schema (Pydantic/Avro/Protobuf).
- Idempotency key (dedup).
- Trace context (parent span ID).
- Metadata (tenant, bot, timestamp, source).

Events chính:
- `document.uploaded` / `document.ingested` / `document.failed` / `document.archived`.
- `chat.received` / `chat.answered` / `chat.failed`.
- `corpus.version_changed`.
- `bot.config_updated`.
- `feedback.given`.
- `quota.exceeded`.

### 14.3 Outbox Pattern (Exactly-Once)

Vấn đề: commit DB → publish event. Fail publish sau commit → inconsistent.

**Transactional outbox**:
1. Cùng transaction: INSERT business data + INSERT outbox row.
2. Commit.
3. Background poller đọc outbox → publish → mark processed.
4. Publish fail → retry.
5. Consumer idempotent (check key) → handle duplicate an toàn.

Kết hợp: at-least-once publish + idempotent consumer = **exactly-once effective**.

### 14.4 Idempotency & Deduplication

Consumer idempotent:
- Lưu `processed_idempotency_keys` (Redis TTL 24h).
- Nhận event, check key → nếu đã xử lý, skip (trả success).

Key = `sha256(source|external_message_id)` (bot platform msg ID).

### 14.5 Dead Letter Queue

Event fail sau N retry → DLQ:
- Subject riêng, consumer riêng.
- Alert khi có message.
- Tool manual replay sau fix bug.

Không vứt event — DLQ là last resort.

### 14.6 Delivery Channels

- **Webhook** (B2B): HTTP POST với HMAC, exponential backoff retry.
- **WebSocket** (web widget): bi-directional, streaming.
- **SSE** (fallback khi WS bị proxy chặn).
- **Bot platform push** (Zalo/Telegram/Messenger): adapter dịch sang platform API.
- **Mobile push** (FCM/APNS): khi user không active.

Gateway subscribe `chat.answered` → route theo channel của conversation.

### 14.7 Corpus Versioning & Cache Invalidation

Thay đổi corpus → bump `corpus_version`.

Cache key prefix include `corpus_version` → tự động invalidate khi bump.

Không xóa cache manual — đơn giản và atomic.

### 14.8 Reindex & Migration Playbook

Standard flow:
1. **Plan**: đo baseline metric, prepare rollback.
2. **Parallel build**: namespace/collection mới, re-embed background.
3. **Shadow validation**: traffic thật, so sánh recall/precision 2 namespace.
4. **Canary**: 5% → 25% → 50% → 100%.
5. **Decommission**: giữ namespace cũ ≥ 72h, xóa sau.
6. **Document**: ADR ghi lý do, metric before/after.

### 14.9 Saga Compensation

Khi downstream fail sau khi upstream commit:
```
ChatReceived → AnswerGenerated (persist OK) → DeliveryFailed (webhook down)
                                            → Compensate: mark "delivery_failed",
                                              publish ChatDeliveryFailed,
                                              alert, retry later
```

Compensation không xóa answer — đánh dấu trạng thái + alert để retry thủ công hoặc tự động.

---

### 12.9 Multi-tenant 3-KEY identity flow (added 2026-04-28 v3 — REQUIRED)

**Rule TUYỆT ĐỐI** — 2 levels, 3 keys REQUIRED:

#### Level 1 — EXTERNAL 3 keys (tenant gửi vào API, CẢ 3 BẮT BUỘC):

| Key | Type | Required | Định nghĩa |
|---|---|---|---|
| `tenant_id` | **int** | ✅ REQUIRED (NOT NULL DB, REQUIRED schema) | Tenant ID upstream. Phải khớp JWT/header (validate ở middleware). |
| `bot_id` | string slug | ✅ REQUIRED | Tên bot tenant định nghĩa, vd `"customer-bot"`. KHÔNG prefix `record_`. **Có thể trùng giữa các tenant** (slug do tenant tự đặt, không qua review). |
| `channel_type` | opaque string | ✅ REQUIRED | Kênh giao tiếp. Project RAG-agnostic — KHÔNG decode/branch theo giá trị. Examples `"web"`, `"zalo"`, `"messenger"` đều là string thường. **Có thể trùng giữa các tenant**. |

**Tại sao PHẢI 3 keys**:
- 2 tenant khác nhau hoàn toàn có thể tự đặt `bot_id="support"` + `channel_type="web"` TRÙNG NHAU.
- Nếu resolve chỉ `(bot_id, channel_type)` → **cross-tenant data leak**: tenant A query → trúng bot tenant B.
- QA/QC red-team test sẽ catch ngay → big issue prod blocker.

**Tại sao tenant_id PHẢI NOT NULL**:
- Postgres unique constraint với cột NULL = **cho phép duplicate** (NULL ≠ NULL trong UNIQUE comparison).
- Vậy `uq_bots_tenant_bot_channel(tenant_id, bot_id, channel_type)` chỉ enforce khi cả 3 NOT NULL.
- Schema hiện tại [models.py:101](src/ragbot/infrastructure/db/models.py#L101) đang `tenant_id: Mapped[int | None] ... nullable=True` — **VI PHẠM rule**, cần migration đổi NOT NULL.

#### Level 2 — INTERNAL (sau khi resolve):

| Key | Type | Định nghĩa |
|---|---|---|
| `record_bot_id` | UUID | Primary key của row trong `bots` table. Đã 1-1 với `(tenant_id, bot_id, channel_type)` qua unique constraint `uq_bots_tenant_bot_channel`. |

**Schema enforce 1-1 mapping**: `bots` table có unique constraint `(tenant_id, bot_id, channel_type)` ([models.py:85-88](src/ragbot/infrastructure/db/models.py#L85-L88)). Vậy:
> **`record_bot_id` ĐÃ uniquely identify `(tenant_id, bot_id, channel_type)`. Sau khi resolve, KHÔNG cần 3 keys cho internal queries — `record_bot_id` ONLY là đủ.**

#### Resolve flow CHUẨN (3 keys REQUIRED không null):

```
HTTP request body: { tenant_id: int (req), bot_id: str (req), channel_type: str (req) }
    ↓ Pydantic validate — reject 422 nếu thiếu/null/sai type
    ↓ Middleware: JWT/header tenant_id PHẢI khớp body tenant_id — reject 403 nếu mismatch
BotRegistryService.lookup(tenant_id: int, bot_id: str, channel_type: str)  # all 3 required
    ↓ Redis key: ragbot:bot:{tenant_id}:{bot_id}:{channel_type} → record_bot_id JSON
    ↓ DB fallback: find_by_bot_channel(tenant_id, bot_id, channel_type)
        SQL: WHERE tenant_id = :tid AND bot_id = :bid AND channel_type = :ch
        AND deleted_at IS NULL
record_bot_id (UUID) → 1-1 với 3-key external
```

Sau resolve: **MỌI query nội bộ CHỈ dùng `record_bot_id`**.

#### Layer-by-layer key usage (CHUẨN):

| Layer | Key dùng | Lý do |
|---|---|---|
| **HTTP request schema** | 3 fields REQUIRED Pydantic | Reject sớm nếu thiếu |
| **External resolve** (lookup `bots`) | `(tenant_id, bot_id, channel_type)` REQUIRED | PHẢI 3 keys vì trùng cross-tenant |
| **Redis registry cache key** | `ragbot:bot:{tenant_id}:{bot_id}:{channel_type}` | Tenant scope đầu tiên trong key |
| **DB unique constraint bots** | `uq_bots_tenant_bot_channel(tenant_id, bot_id, channel_type)` NOT NULL | 3 cột NOT NULL ở DB level |
| **Pgvector filter** | `record_bot_id` ONLY | Đã resolve rồi |
| **Document upsert dedup** | `(record_bot_id, source_url)` | record_bot_id đã đủ unique |
| **Conversation history** | `(record_bot_id, connect_id)` | record_bot_id đã đủ |
| **Semantic cache** | `(record_bot_id, record_tenant_id, bot_version, corpus_version)` | KHÔNG cần channel_type — đúng hiện tại |
| **DB composite index data tables** | `record_bot_id` ONLY | `(record_bot_id, channel_type)` THỪA |

#### Brutal-audit gaps RE-SCOPED (2026-04-28 v3 — sau identity expansion):

- **Gap B.1 ❌ INVALID**: semantic_cache.py:159-162 filter `record_bot_id + record_tenant_id` — đúng, không cần thêm channel_type.
- **Gap B.2 ✅ VALID**: schema `chat_schema.py:20` yêu cầu `bot_id: UUID` — leak internal UUID. **Đổi thành 3-key string + int**.
- **Gap B.3 ✅ VALID**: `routes/test_chat/` package hit DB direct — dùng registry lookup.
- **Gap B.4 RE-SCOPED**: BỎ composite index `(record_bot_id, channel_type)` thừa, dùng `record_bot_id` ONLY.
- **Gap B.5 NEW**: BỎ filter THỪA `AND channel_type = :ch` ở 4 chỗ ([document_service/](src/ragbot/application/services/document_service/) package; [pgvector_store.py:87-88](src/ragbot/infrastructure/vector/pgvector_store.py#L87-L88)).
- **Gap B.6 NEW**: `documents.channel_type` denormalized THỪA — drop column.
- **Gap B.7 NEW PRIORITY P0** (CROSS-TENANT IDENTITY COLLISION):
  - [models.py:101](src/ragbot/infrastructure/db/models.py#L101) `tenant_id: Mapped[int | None] ... nullable=True` → **đổi `nullable=False`**.
  - [sync.py:92-98](src/ragbot/interfaces/http/routes/sync.py#L92-L98) query 2-key, có `req.tenant_id` nhưng KHÔNG dùng → **add `AND tenant_id = :tid`** vào WHERE.
  - [bot_registry_service.py:91-96](src/ragbot/application/services/bot_registry_service.py#L91-L96) signature `tenant_id: int | None = None` → **đổi `tenant_id: int`** required.
  - [bot_repository.py:109-130](src/ragbot/infrastructure/repositories/bot_repository.py#L109-L130) keyword-only optional `tenant_id` → **positional required**.
  - HTTP schemas: 3 fields PHẢI required Pydantic.
  - Migration mới: `tenant_id NOT NULL` + verify unique constraint enforce.
  - Plan: [plans/260429-3KEY-identity/plan.md](../../plans/260429-3KEY-identity/plan.md) — Sprint 9 PRIORITY TOP P0.

Xem [SPRINT9_AUDIT_VERDICT §B](../../reports/SPRINT9_AUDIT_VERDICT.md) cho full breakdown.

---

### 12.10 Workspace entity — slug → first-class row (W2-D2, added 2026-06-10)

> Stance EVOLVE: slug-in-identity ĐÚNG (4-key tuple KHÔNG đổi). Thêm bảng `workspaces` BÊN CẠNH slug để mang được lifecycle + RBAC/quota — KHÔNG thay identity.

**Vấn đề slug-only**: `bots.workspace_id VARCHAR(64)` (alembic `0062`) đủ cho identity/data-scoping nhưng KHÔNG mang được boundary RBAC/quota — không có bảng entity, không lifecycle (create/soft-delete/offboard).

- **Schema additive** (alembic `0199`): `CREATE TABLE workspaces (id UUID PK, record_tenant_id UUID FK, slug VARCHAR(64), name, created_at, deleted_at)` + `UNIQUE(record_tenant_id, slug)`. Backfill `INSERT … SELECT DISTINCT record_tenant_id, workspace_id FROM bots ON CONFLICT DO NOTHING` → mỗi tenant ≥1 ws "default" trùng `str(record_tenant_id)` (đúng tinh thần null→default ws). Repo: `WorkspaceRepository.lookup/list_for_tenant/ensure` (`infrastructure/repositories/workspace_repository.py:30,:49,:65`) — `ensure` get-or-create idempotent (concurrent create → re-read winner).
- **4-key tuple KHÔNG đổi**: entity là tham chiếu THÊM, liên kết qua `(record_tenant_id, workspace_id slug)`. **KHÔNG** thêm `record_workspace_id` UUID vào tuple (sẽ vỡ unique constraint + Redis key + leak-test D3) — `bots.workspace_id` slug giữ nguyên là cột identity.
- **RBAC global-per-tenant** (W2 chốt, `workspace_members` defer Wave 6): tenant admin (level 80) quyền đều trên MỌI ws của mình; `role_definitions.scope` dead-column được reconcile. D2 đóng **data-boundary** workspace (qua RLS `app.workspace_id` của D3, xem `05-E §24.1`), KHÔNG đóng **authz-boundary** workspace (defer). Lý do: psql `count(DISTINCT workspace_id)=1` per tenant → chưa ai dùng multi-ws, build FGA hierarchy = premature (sacred #0).
- **Quota cascade tenant→workspace→bot**: `IngestQuotaService` wire vào 2 upload route thật (`enforce_ingest_quota` `interfaces/http/_ingest_quota_guard.py:28`, gọi tại `documents.py:167` + `documents_stream_upload.py:251`) — đóng IQ-1 orphan (trước chỉ chạy demo route). `0=unlimited` giữ; ws-tier degrade về tenant-tier khi ws_limit chưa set (backward-compat).
- **RLS scope tenant**: `workspaces` ENABLE+FORCE, policy `USING (record_tenant_id = current_setting('app.tenant_id', true)::uuid)` — không workspace predicate trên chính bảng (owner cần list mọi ws của mình); data tables (bots/documents) mới là chỗ ws-isolation áp dụng (D3 cover).

---
