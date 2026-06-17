# PHẦN E — CROSS-CUTTING PATTERNS

## 19. Caching Strategy (2-tier implemented; 4-tier planned)

### 19.1 Current 2-Tier Cache (Implemented)

| Tier | Content | TTL | Hit target |
|---|---|---|---|
| **Exact hash match** | Redis key = hash(query + tenant + bot_version + corpus_version) → cached response | 1h | ≥ 20% |
| **Semantic similarity** | pgvector cosine similarity ≥ 0.97 on query embeddings | 1h | ≥ 10% |

### 19.1b PLANNED 4-Tier Cache Model (not yet implemented)

| Tier | Content | TTL | Hit target |
|---|---|---|---|
| **Semantic cache** | Query có embedding similarity ≥ 0.97 | 1h | ≥ 20% |
| **Response cache** | Prompt + model + tools → response | 1h | ≥ 10% |
| **Retrieved chunks cache** | Normalized query + filters → candidates | 10 min | ≥ 30% |
| **Embedding cache** | Text + model version → embedding | 30 ngày | ≥ 70% |

Combined target: **≥ 35%** (giảm 35% cost + latency).

### 19.2 Cache Key Design Rules

**Mandatory prefix template**:
```
tenant:{tenant_id}:bot:{bot_version}:cv:{corpus_version}:...
```

Keys (CORRECTED 2026-04-28 v2 — xem `03-C-cross-axes.md §12.9`):

> **Logic**: `record_bot_id` đã 1-1 với `(tenant_id, bot_id, channel_type)` qua unique constraint `uq_bots_tenant_bot_channel`. Sau resolve, key chỉ cần `record_bot_id` — KHÔNG cần thêm `channel_type` (thừa).

- Semantic: `sc:rbid:{record_bot_id}:bv:{bv}:cv:{cv}` (hoặc giữ `t:{tid}` cho RLS defense-in-depth)
- Response: `resp:rbid:{record_bot_id}:bv:{bv}:cv:{cv}:p:{prompt_hash}`
- Retrieved: `ret:rbid:{record_bot_id}:cv:{cv}:q:{query_hash}`
- Embedding: `emb:mv:{model_version}:h:{text_hash}` (no bot/channel — text-only embedding)

Hash SHA256 để key độ dài cố định.

✅ **Code reality match (2026-04-28)**: `semantic_cache.py:159-162` filter chỉ có `record_bot_id + record_tenant_id + bot_version + corpus_version` — **đây là ĐÚNG**, không phải gap. Trước đây v1 docs ghi sai là "thiếu channel_type" — đã correct.

### 19.3 Invalidation Triggers

Không xóa manual. Bump version:
- Doc update → `corpus_version` bump → retrieved + response cache invalid.
- Bot config update → `bot_version` bump → response invalid.
- Embedding model upgrade → `embedding_model_version` bump → embedding + semantic invalid.

TTL đủ ngắn để tránh stale nếu miss bump.

### 19.4 Cross-Tenant Leak Prevention

Test bắt buộc:
- Red-team: query tenant A không bao giờ hit cache tenant B.
- Audit log: cache key include tenant_id, grep log confirm.

Phát hiện leak → incident, wipe cache, fix root cause.

### 19.5 Semantic Cache Threshold

Cao (0.95–0.98) tránh false match. Tune trên golden set:
- False positive (serve sai) là critical → thà miss còn hơn hit sai.
- False negative (miss cache) chỉ chậm + tốn token.

---

## 20. Performance

### 20.1 Latency Budget Allocation

Target p95 < 5s fresh, p95 < 2s cached.

| Stage | Budget |
|---|---|
| Input moderation | 100ms |
| Routing | 200ms |
| Query rewriting / HyDE | 400ms (cached ~50ms) |
| Hybrid retrieval | 300ms |
| Reranking | 400ms |
| Grading | 300ms |
| Generation | 2500ms |
| Citation validation | 50ms |
| Output moderation | 100ms |
| Overhead | 200ms |
| **Total** | **4550ms** |

### 20.2 Async Everything

- **Chỉ dùng**: `httpx.AsyncClient`, `asyncpg`, `redis.asyncio` (Redis Streams for bus).
- **Cấm**: `requests`, `psycopg2` sync, `time.sleep()`, `threading.Lock` trong request path.
- CPU-bound (rerank local, parse sync) → `loop.run_in_executor(ThreadPoolExecutor)`.
- Concurrent LLM calls: `asyncio.gather` + `Semaphore` chống rate limit.

### 20.3 Connection Pooling

| Pool | Size | Recycle |
|---|---|---|
| Postgres (asyncpg) | 20 per pod | 1800s |
| Redis | 50 per pod | — |
| httpx (external) | 100 max, 50 keepalive | 60s |
| Qdrant gRPC | persistent channel | keepalive 30s |

### 20.4 N+1 Detection & Fix

- **SQLAlchemy eager loading** (`selectinload`, `joinedload`).
- **DataLoader pattern**: gom request 10ms window, batch gửi.
- **Bulk upsert** Qdrant 64 chunk/batch.
- **CI check**: sqlalchemy event listener count queries per request, fail test nếu N+1 trong hot path.

### 20.5 uvloop + httptools

```
pip install uvloop httptools
uvicorn ... --loop uvloop --http httptools
```

+2-4x perf so với default asyncio loop.

### 20.6 Resource Isolation

- CPU-light async (API, WS): many small replicas.
- CPU-heavy (ingest, chunking): medium replicas với CPU limit.
- GPU-heavy (reranker, local LLM): node pool riêng, taint, cold start cao.
- IO-heavy (DB, vector): không shared pool, SSD NVMe.

---

## 21. Scalability & Deployment Topology

### 21.1 Service Decomposition

| Service | Role | State |
|---|---|---|
| `api` | HTTP entry, auth, rate limit | stateless |
| `ws_gateway` | WebSocket + event bridge | stateless, sticky session |
| `webhook_adapter` | Bot platform ↔ internal event | stateless |
| `ingest_worker` | Consume `document.uploaded` | stateless |
| `rag_worker` | Consume `chat.received` | stateless |
| `feedback_worker` | Consume feedback, update metrics | stateless |
| `outbox_worker` | Outbox → Redis Streams | singleton (HA via leader election) |
| `reranker_server` | GPU, expose rerank API | GPU |
| `embedding_server` | GPU, expose embed API | GPU |
| `vllm_fallback` | Local LLM fallback | GPU, optional |
| `llm_proxy` | LiteLLM unify + cost tracking | stateless |
| `scheduler` | Cron jobs (reindex, aggregate) | singleton |

Stateful: Postgres, Qdrant, Redis Stack (includes Streams for message bus). Langfuse planned.

### 21.2 Autoscaling Signals

- **API gateway**: RPS / CPU.
- **Worker**: **queue depth** (pending messages), không phải CPU.
- **GPU service**: queue depth + GPU utilization.
- **DB**: không autoscale compute, scale read replica.

### 21.3 GPU Node Pool

- Tách node pool taint.
- Pods có tolerance + nodeSelector.
- Cold start cao → min replicas ≥ 1, **không scale to zero**.
- Batch requests qua Triton/TEI dynamic batching.

### 21.4 Blue-Green & Canary

- **Blue-Green** cho infra cứng: 2 env song song, switch router.
- **Canary** cho logic: 5% → 25% → 50% → 100%, metric-driven.
- **Feature flag** cho user-facing: flip không redeploy.

---

## 22. Cost Control

### 22.1 Cost Drivers

- **LLM input tokens**: lớn nhất với context dài.
- **LLM output tokens**: rẻ hơn input nhưng cộng dồn.
- **Embedding**: rẻ nhưng bulk ingestion tốn.
- **Reranking**: rẻ nếu self-host GPU, đắt nếu SaaS per request.
- **OCR**: đắt cho PDF image.

### 22.2 Model Cascade

- **Router, Rewriter, Grader**: LLM nhỏ (cheap, fast).
- **Generator**: LLM trung bình default, escalate khi grade thấp hoặc intent phức tạp.
- **Fallback**: self-host model khi provider down.

Quyết định dựa trên classifier đầu vào, không hardcoded.

### 22.3 Budget Enforcement per Tenant

- Monthly token budget + daily rate limit.
- Soft warn 80%, hard block 100%.
- Override cho emergency cần admin approve.

### 22.4 Cache Hit Ratio as Cost Lever

Mỗi % cache hit = tiết kiệm LLM + embedding proportional. Cache hit đo hiệu quả cost.

Target ≥ 35%. Dưới → root cause (threshold sai? key không phù hợp? traffic đa dạng quá?).

### 22.5 Batch vs Realtime

- **Ingestion**: luôn batch. Không embed 1-by-1.
- **Query**: realtime, dedup trong debounce window giảm call.
- **Analytics**: batch job hourly, không realtime streaming.

---

## 23. Failure Modes & Mitigations (Top 12)

| # | Failure | Nguyên nhân | Mitigation |
|---|---|---|---|
| 1 | Indirect prompt injection qua doc | User upload doc chứa "ignore instructions" | Context sandboxing + doc scan + output check + canary |
| 2 | Cross-tenant leak qua cache | Cache key thiếu tenant_id | Key rule enforced, red-team audit |
| 3 | Silent quality drop đổi embedding | Mix vectors khác model | Version field + dual-write migration |
| 4 | Reranker timeout silent dense fallback | Circuit breaker không alert | Metric + alert, circuit breaker state visible |
| 5 | Chunk cắt giữa table/formula | Fixed chunking không structure-aware | AdapChunk + atomic preservation |
| 6 | Golden set leak vào few-shot | Không có hash check | Hash gate + team isolation |
| 7 | Header/footer lặp dominate BM25 | Không dedup ingest | Frequency analysis + stopword |
| 8 | Retrieval drift khi nhồi history | History trong query | Condense question trước retrieve |
| 9 | Citation hallucination | LLM bịa doc_id | Structured output + post-validation |
| 10 | Silent regression đổi prompt | Không có eval gate | CI RAGAS gate + canary |
| 11 | Cost blow-up do loop | Không cap iteration | Hard cap + alert |
| 12 | Stale cache sau doc update | Cache không invalidate | Version-scoped key |

**Defense-in-Depth**: mỗi failure ≥ 2 lớp phòng thủ. Không tin single check.

---

## 24. Enforcement & exactly-once patterns (W1–W6 — đã wire)

> 4 cross-cutting pattern được nối dây trong chương trình Expert Build (Wave W1–W2). Tất cả theo stance EVOLVE: machinery đã tồn tại, đây là phần "cắm dây + recalibrate", không rewrite. Mọi anchor `file:line` verify trên code thật.

### 24.1 RLS enforcement layer-3 (W1-D3)

Tenant isolation cần **cả 3 layer** sống thì RLS mới enforce thật (`infrastructure/db/session.py:6-26`): (1) `ENABLE/FORCE ROW LEVEL SECURITY` + policy đọc GUC (alembic 0069/0141/0187); (2) login role **NOBYPASSRLS non-owner** `ragbot_app`; (3) mỗi transaction `SET LOCAL`. Layer-3 trước đây chỉ wire ở callsite `session_with_tenant`; bare-session repo không issue `SET LOCAL` → policy chết.

- **Hook**: `attach_rls_session_hook` (`session.py:188`) đăng ký `after_begin` listener (`_after_begin` `:141`) — khi `tenant_id_ctx` có giá trị thì emit `SET LOCAL app.tenant_id = '<uuid>'` (`:129-138`) + `SET LOCAL app.workspace_id = '<slug>'` khi `workspace_id_ctx` set (`:158-162`). UUID validate trước nội suy (SET LOCAL không nhận bind param); slug validate qua `^[a-zA-Z0-9-]{1,64}$` (`:84`).
- **Wire**: `create_rls_session_factory(engine=...)` (`session.py:213`) gói `create_session_factory` + attach hook; composition root route qua đây tại `bootstrap.py:171-173` → **mọi** repo session (kể cả bare `_new_session`) có layer-3.
- **No-op an toàn**: dưới superuser DSN hook là behavioural no-op (policy bị bypass bất kể); contextvar unbound (ops/migration/background) → return sớm (`:152-154`). Bật enforce ngay khi ops trỏ `DATABASE_URL_APP` sang `ragbot_app` — code không đổi.
- **Workspace GUC backward-compat**: `app.workspace_id` chưa SET → policy clause `COALESCE(current_setting('app.workspace_id', true),'')=''` short-circuit TRUE → tenant-only semantics (`session.py:76-79`).
- **Leak-test role-guard non-vacuous**: integration test phải `SELECT rolbypassrls, rolsuper` = cả hai FALSE, nếu không `pytest.fail` — chốt chống "test xanh vô nghĩa trên superuser" (ADR-W1-D3 §2.4).

### 24.2 Exactly-once transactional inbox (W1-D8b)

Pattern **process-then-mark** (`infrastructure/events/redis_streams_bus.py:361-433`) thay anti-pattern mark-before-handler (dedup `SET NX` trước handler → handler raise → redeliver → dedup-skip + XACK = **message DROPPED**).

- **Store**: bảng `event_inbox` PK `(subscriber_id, msg_id)` (alembic `0198`). `subscriber_id = subject:group` — 1 message, N subscriber độc lập.
- **Order**: Redis `SET NX` giữ làm **fast-path hint** (`:392`) nhưng **mất quyền XACK** — hint-hit vẫn phải check inbox DB (`:400`), chỉ inbox-row-exists mới XACK-and-skip (`:407`). Handler chạy → `INSERT INTO event_inbox ... ON CONFLICT DO NOTHING` (`:49-52`) **trong CÙNG transaction** của handler side-effects (hook `_make_inbox_tx` `:209`, conflict → `InboxDuplicateError` rollback). XACK **chỉ sau commit** (`:431-433`) → crash giữa commit và XACK = redeliver → inbox-hit → skip.
- **Handler contract backward-compat**: handler khai báo keyword-only `inbox_tx` (`:64`) thì own mark cùng tx; handler 1-arg cũ → bus tự mở tx wrap mark `_mark_processed` (`:179`), phải idempotent cho side-effect ngoài DB.
- **DLQ thật**: poison message (`times_delivered > DEFAULT_BUS_DLQ_MAX_DELIVERIES`) XADD sang `{stream}:dlq` (`_dead_letter` `:598`) **trước** XACK (XADD-then-XACK — blip để lại PEL, retry pass sau) → admin replay được, không log-and-drop.

### 24.3 Per-tenant ingest fairness (W2-D8)

Noisy-neighbour ở tầng consumer-concurrency: global `Semaphore(5)` cho TẤT CẢ tenant → A flood 100 doc nuốt cả 5 slot, B chờ sau A.

- **Nesting OUTER per-tenant / INNER global** (`redis_streams_bus.py:378-379`): `async with tenant_sem, sem:` — per-tenant semaphore (`_tenant_semaphore` `:133`, `Semaphore(DEFAULT_BUS_CONCURRENCY_PER_TENANT)`) acquire **NGOÀI** global `sem`. Tại sao thứ tự này chống starvation: một tenant noisy bị block trên **cap riêng của nó** sẽ KHÔNG cầm slot global trong lúc chờ → tenant khác luôn với tới ≥ `(global − per_tenant)` slot còn lại. Nếu đảo (global ngoài) thì task A block-trên-tenant-cap vẫn giữ global slot = đói B.
- **Bounded state**: dict keyed `record_tenant_id` lift từ payload (`_fairness_key` `:112`), lazy; quá `DEFAULT_BUS_TENANT_SEM_MAX` tenant → share 1 overflow semaphore (`:144-148`) nên dict không leak. Concurrency là transient → restart reset là đúng, không persist.
- **Trực giao với D8b**: fairness quyết *bao nhiêu message của tenant nào song song*; exactly-once quyết *message có double/drop không*. Per-tenant wrap bao NGOÀI khối dedup-INSERT-XACK của D8b.

### 24.4 API-key encryption at rest (W1-KEY)

Provider key trong `api_keys.value_plain` plaintext (× superuser DSN × nightly `pg_dump` = key lộ qua backup). AES-GCM machinery `EnvSecretsAdapter` đã có sẵn — chỉ nối dây.

- **Envelope**: AES-256-GCM `base64(nonce[12] || ciphertext+tag)`, KEK từ env `RAGBOT_CONFIG_KEK`, fail-loud RuntimeError khi KEK thiếu (`infrastructure/security/env_secrets.py`).
- **Dual-read resolver** (`application/services/provider_key_resolver.py:112`): `SELECT value_encrypted, value_plain` → ưu tiên `value_encrypted` qua `secrets.resolve` (`:88,:140`); `value_plain` fallback + structlog `api_key_plaintext_read` (`:144`) đếm tiến độ backfill về 0 trước kill-date.
- **Redis lưu ciphertext**: cache setex ciphertext, decrypt sau cache-hit → plaintext biến mất khỏi Redis at-rest; cache-hit decrypt fail (entry cũ) coi như miss, đọc lại DB.
- **Backfill 2-step (no-psql-hotfix)**: alembic `0196` encrypt + fingerprint vào `metadata_json` (giữ plain để rollback); `0197` NULL-out `value_plain` (downgrade decrypt ngược) — áp sau soak `api_key_plaintext_read`=0. Verify gate: `count(value_plain IS NOT NULL)` = 0.

---
