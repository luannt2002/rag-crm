# Luồng dự án Ragbot — overview

Tài liệu này mô tả **luồng dữ liệu** đi qua hệ thống Ragbot từ lúc bot
owner upload tài liệu đến lúc user chat trên UI nhận câu trả lời. Mục
đích: developer mới onboarding hiểu nhanh; ops + bot owner hình dung
được data đi đâu, bug nằm ở step nào.

Tài liệu này KHÔNG kể chi tiết kỹ thuật từng module — đó là việc của
`RAGBOT_MASTER.md` (table of contents 16 sub-files trong `docs/master/`).

---

## 1. Sơ đồ tổng — 2 luồng

```
┌──────────────────────────────────────────────────────────────────────┐
│                          INGEST FLOW                                  │
│   (bot owner upload tài liệu vào bot)                                 │
└──────────────────────────────────────────────────────────────────────┘

  Bot owner          HTTP API           Worker             pgvector DB
  ─────────          ────────           ──────             ───────────
     │                  │                  │                    │
     │ POST /documents/ │                  │                    │
     │   ingest         │                  │                    │
     ├─────────────────▶│                  │                    │
     │                  │ enqueue          │                    │
     │                  │  Redis stream    │                    │
     │                  ├─────────────────▶│                    │
     │  202 + job_id    │                  │ fetch URL          │
     │◀─────────────────┤                  │ parse              │
     │                  │                  │ chunk              │
     │                  │                  │ embed              │
     │                  │                  │ upsert chunks      │
     │                  │                  ├───────────────────▶│
     │                  │                  │                    │


┌──────────────────────────────────────────────────────────────────────┐
│                          QUERY FLOW                                   │
│   (user chat trên UI bot, hỏi câu hỏi)                                │
└──────────────────────────────────────────────────────────────────────┘

  User             HTTP API          Pipeline             pgvector DB
  ────             ────────          ────────             ───────────
   │                  │                  │                     │
   │ POST /chat       │                  │                     │
   │  + message       │                  │                     │
   ├─────────────────▶│                  │                     │
   │                  │ LangGraph        │                     │
   │                  │ 32-step pipeline │                     │
   │                  ├─────────────────▶│                     │
   │                  │                  │ understand          │
   │                  │                  │ retrieve            │
   │                  │                  │ ────────────────────▶
   │                  │                  │ rerank              │
   │                  │                  │ grade               │
   │                  │                  │ generate (LLM call) │
   │                  │                  │ guard_output        │
   │  answer + cite   │                  │                     │
   │◀─────────────────┤◀─────────────────┤                     │
```

---

## 2. INGEST FLOW chi tiết — 1 doc đi từ URL → chunks trong DB

Khi anh upload 1 file Google Sheets / Excel / PDF / DOCX qua admin UI,
nó đi qua **5 step**:

### Step 1 — HTTP endpoint nhận

- `POST /api/ragbot/documents/ingest` (route `interfaces/http/routes/documents.py`)
- Body: `{bot_id, channel_type, workspace_id?, source_url, document_name, mime_type?}`
- Auth: JWT bearer (tenant claim) + RBAC permission `document.ingest`
- 4-key identity: `record_tenant_id` từ JWT, `bot_id + channel_type + workspace_id?` từ body

Endpoint:
1. Validate format `workspace_id` slug
2. Resolve `record_bot_id` qua `BotRegistryService.lookup()`
3. Create `Document` row trong DB (state=`PENDING`)
4. Enqueue `DocumentUploaded` event vào **outbox** table
5. Trả 202 + `job_id` ngay (async, không block)

### Step 2 — Outbox publisher

- Service `ragbot-outbox.service` poll outbox table mỗi vài giây
- Convert row → Redis Stream `ragbot.events.document.uploaded.v1`
- Đảm bảo at-least-once delivery (idempotent)

### Step 3 — Document worker xử lý

- Service `ragbot-document-worker.service` consume Redis Stream
- File: `src/ragbot/interfaces/workers/document_worker.py`
- Flow worker:

```
   1. Pickup event từ Redis
   2. Check raw_content cache (nếu Action 1 đã fetch trước)
   3. Nếu chưa có content:
        a. Call OCR/Parser
        b. Extract text
   4. Delegate sang DocumentService.ingest():
        a. _route_through_parser (registry parser)
        b. Chunking: smart_chunk(text)
        c. Embedding: LiteLLM batch embed
        d. Bulk insert document_chunks
   5. Mark Document.state = INDEXED
```

> ⚠️ **Bug #4 nằm ở Step 3** — worker hiện gọi **OCR trước parser
> registry**. Doc kiểu `text/csv` (Google Sheets URL) đáng lý route
> qua `GoogleSheetsParser` nhưng bị Kreuzberg OCR đè lên. Phase C
> sẽ fix.

### Step 4 — Chunking (`smart_chunk`)

File: `src/ragbot/shared/chunking.py`

Hệ thống chọn 1 trong 5 strategy auto-detect:

| Strategy | Khi nào dùng |
|---|---|
| `table_csv` | Doc dạng CSV / bảng (header + rows) — em vừa fix mixed-content detect ở Phase A |
| `hdt` | Doc legal/admin có Chương/Mục/Điều/Phần |
| `semantic` | Doc text dài không có heading |
| `hybrid` | Doc mixed heading + prose |
| `recursive` | Default fallback |
| `proposition` | Doc dense academic |

> ⚠️ **Bug #5 (đã fix Phase A)**: `_is_csv_format` reject mixed doc
> (intro + table + footer) → strategy=recursive → Phase 1 chunking
> header+footer KHÔNG kick in. Phase A fix bằng criterion 2 "dominant
> table run".

### Step 5 — Embedding + Upsert

- LiteLLM batch embed chunks → vector 1280-dim (ZeroEntropy zembed-1)
- INSERT bulk vào `document_chunks` với metadata (chunk_index, content_hash, embedding, parent_chunk_id, chunk_type)
- HNSW index update tự động (Postgres pgvector)

**Output cuối**: N rows trong `document_chunks` table, scoped theo
`(record_tenant_id, record_bot_id, record_document_id)`.

---

## 3. QUERY FLOW chi tiết — câu hỏi user → câu trả lời bot

Khi user gõ "1tr499 có mấy dịch vụ" trên UI, request đi qua **32 step**
trong LangGraph adaptive pipeline. Em chia thành 6 phase logic:

### Phase A: Input + Cache check (step 1-3)

```
guard_input → cache_check → router_select_model
```

- `guard_input`: PII scrub, rate-limit, source validator
- `cache_check`: Redis exact-hash cache → nếu hit trả ngay (0 LLM call)
- `router_select_model`: chọn LLM cho `understand_query` step

### Phase B: Understand query (step 4-6)

```
understand_query → hash_lookup_cache → semantic_cache_check
```

- `understand_query`: LLM rewrite câu hỏi standalone + classify **intent**
  - intent ∈ {factoid, comparison, multi_hop, aggregation, greeting, feedback, chitchat, out_of_scope, vu_vo}
- `hash_lookup_cache`: hash exact của câu hỏi đã rewrite
- `semantic_cache_check`: pgvector cosine cache (≥ 0.97)

> ⚠️ **Phase 2 đã fix (commit `046485a`)**: prompt understand thêm 8
> few-shot example. Trước fix: 9/9 query "có mấy" classify thành
> `factoid`. Sau fix: 5/5 query "có mấy" classify thành `aggregation`
> (verified live LLM probe 8/8 PASS).

### Phase C: Query complexity + Decompose (step 7-8)

```
query_complexity → adaptive_decompose
```

- Đo complexity (số commas + conjunctions + numbers + question marks)
- Multi-hop / aggregation query → decompose thành sub-queries

### Phase D: Retrieve + Rerank funnel (step 9-13)

```
retrieve (multi-query fanout) → rrf_fuse → rerank → filter_min_score → mmr_dedup
```

- `retrieve`: vector + BM25 hybrid với multi-query 3 variants
- `rrf_fuse`: Reciprocal Rank Fusion merge 3 branch
- `rerank`: cross-encoder ZE `zerank-2` rank top-N
- `filter_min_score`: cliff-strategy drop low-score chunks
- `mmr_dedup`: MMR diversity dedup

> ⚠️ **Phase 3 đã fix (commit `4289687`)**: per-intent `rerank_top_n`
> mới — aggregation = 20 (vs default 7). Phase 3 đã ship + DB seeded
> qua alembic 010x.

### Phase E: Grade + Generate (step 14-17)

```
grade → generate → prompt_compression → litm_order → prompt_build
```

- `grade`: CRAG grader (yes/no relevance per chunk)
- `generate`: main LLM call (gpt-4.1-mini hoặc Innocom `gemma-4-e2b-it`)
- `prompt_compression`: token budget enforce
- `litm_order`: Lost-in-the-Middle re-order chunks
- `prompt_build`: assemble final prompt + cap context_chars

> ⚠️ **Phase 3 đã fix (commit `4289687`)**: per-intent
> `generate_context_chars_cap` — aggregation = 5500 (vs default 2900).
> Cho phép LLM thấy nhiều rows hơn khi đếm.

### Phase F: Citations + Guardrail (step 18-22)

```
citations_extract → guard_output → grounding_check → persist
```

- `citations_extract`: extract chunk IDs LLM đã cite
- `guard_output`: PII redact output, intent-gated grounding eligibility
- `grounding_check`: HALLU verifier (judge câu trả lời có grounded vào chunks không)
- `persist`: save request_log + request_steps + audit_log

---

## 4. 4 bug pre-existing đang fix trong plan `260525-4BUG`

```
┌─────────────────────────────────────────────────────────────────┐
│  INGEST FLOW                                                     │
│  ───────────                                                     │
│                                                                  │
│  HTTP /ingest → Outbox → Worker                                  │
│                            │                                     │
│                            ▼                                     │
│                          [OCR call] ◀─── Bug #4: OCR đè parser  │
│                            │                  registry. CSV/    │
│                            ▼                  Sheets fail.      │
│                       DocumentService.ingest                     │
│                            │                                     │
│                            ▼                                     │
│                          smart_chunk                             │
│                            │                                     │
│                            ▼                                     │
│                       _is_csv_format ◀─── Bug #5: reject mixed  │
│                            │                  doc. Phase A      │
│                            ▼                  đã fix (3a29456). │
│                       table_csv strategy                         │
│                            │                                     │
│                            ▼                                     │
│                       _chunk_table_csv_with_context (Phase 1)    │
│                            │                                     │
│                            ▼                                     │
│                       Bulk INSERT chunks                         │
│                                                                  │
│                                                                  │
│  RECHUNK FLOW (re-ingest existing doc)                           │
│  ─────────────                                                   │
│                                                                  │
│  HTTP /documents/rechunk                                         │
│       │                                                          │
│       ▼                                                          │
│    RechunkDocumentUseCase                                        │
│       │                                                          │
│       ▼                                                          │
│    get_by_source_url ◀─── Bug #1: match nhầm khi >1 doc cùng URL│
│       │                       Bug #2: thiếu path by-document-id │
│       ▼                                                          │
│    Wipe chunks + enqueue DocumentUploaded                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│  QUERY FLOW                                                      │
│  ──────────                                                      │
│                                                                  │
│  HTTP /chat → LangGraph 32-step                                  │
│       │                                                          │
│       ▼                                                          │
│    understand_query                                              │
│       │                                                          │
│       ▼                                                          │
│    [pipeline_config resolver] ◀─── Bug #6: per-bot plan_limits  │
│       │                                  không được honor.       │
│       ▼                                  System_config GLOBAL    │
│    retrieve + rerank                     thắng bot override.     │
│       │                                                          │
│       ▼                                                          │
│    generate → answer                                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Module organization — code đâu

| Layer | Folder | Mục đích |
|---|---|---|
| Interfaces | `src/ragbot/interfaces/http/` | REST routes + middleware (JWT, RBAC, rate limit, tenant context) |
| Interfaces | `src/ragbot/interfaces/workers/` | Background workers (document/chat/outbox) |
| Application | `src/ragbot/application/services/` | Business orchestration (DocumentService, BotRegistryService, ...) |
| Application | `src/ragbot/application/use_cases/` | Single-purpose use cases (RechunkDocumentUseCase, ...) |
| Application | `src/ragbot/application/ports/` | Port interfaces (DocumentRepositoryPort, VectorStorePort, LLMPort, ...) |
| Domain | `src/ragbot/domain/entities/` | Domain entities (Document, Conversation, Bot) |
| Domain | `src/ragbot/domain/value_objects/` | TenantScope, BotId, ChunkId |
| Domain | `src/ragbot/domain/events/` | DomainEvent classes (DocumentUploaded, DocumentIngested) |
| Infrastructure | `src/ragbot/infrastructure/db/` | SQLAlchemy models + UoW + session |
| Infrastructure | `src/ragbot/infrastructure/repositories/` | Repository implementations |
| Infrastructure | `src/ragbot/infrastructure/parser/` | Document parsers (PDF/DOCX/CSV/Sheets/MD) |
| Infrastructure | `src/ragbot/infrastructure/ocr/` | OCR engines (Kreuzberg) |
| Infrastructure | `src/ragbot/infrastructure/reranker/` | Reranker providers (ZE/Cohere/Null) |
| Infrastructure | `src/ragbot/infrastructure/embedding/` | Embedder providers (LiteLLM/Voyage/BKai) |
| Infrastructure | `src/ragbot/infrastructure/vector_store/` | pgvector store impl |
| Infrastructure | `src/ragbot/infrastructure/llm/` | LiteLLM router + LM Studio adapter |
| Orchestration | `src/ragbot/orchestration/` | LangGraph pipeline (32-step adaptive) |
| Shared | `src/ragbot/shared/` | Constants, types, chunking, utilities |
| Bootstrap | `src/ragbot/bootstrap.py` | DI container, factory wiring |

---

## 6. Database tables — luồng quan trọng

| Table | Mục đích |
|---|---|
| `tenants` | Multi-tenancy root |
| `bots` | Per-tenant bot config (4-key identity) |
| `bot_model_bindings` | Bot → AI model mapping (grading/grounding/llm_primary/rerank) |
| `ai_providers` / `ai_models` | LLM/embedder/reranker registry |
| `documents` | Doc metadata (source_url, mime_type, state, raw_content cache) |
| `document_chunks` | Chunks (content, embedding, chunk_type, parent_chunk_id) |
| `request_logs` | Per-request audit (duration, tokens, cost, citations) |
| `request_steps` | Per-step telemetry (32 step × metadata_json) |
| `audit_log` | Forensic admin trail (RBAC + critical mutations) |
| `outbox` | Reliable event delivery (poll → Redis) |
| `jobs` | Background job state |
| `system_config` | Global config knobs (Redis-cached, JSONB) |
| `language_packs` | Prompt templates per (code, prompt_key) |
| `semantic_cache` | pgvector cosine cache cho query |
| `bot_model_bindings.plan_limits` | Per-bot JSONB override system_config |

---

## 7. Đã ship tuần này (2026-05-21 → 25)

```
3a29456  Phase A (Bug #5) — _is_csv_format mixed-content     ┐
a8c953c  Bug #3 RechunkDocumentUseCase + script ops           │
4fcee4a  Phase 5 — alembic 010y flag chunking ON              │ Plan
4289687  Phase 3 — per-intent rerank_top_n + alembic 010x     │ 260521-
717adce  Whitelist 9 missing keys + pin test                  │ CHUNK-
adcf5d8  Detector fix + per-intent constants prep             │ AGGREGATION-
046485a  Phase 2 — alembic 010w few-shot understand           │ UNIVERSAL
740a955  Phase 1 — chunking header/footer + 12 test           ┘

7cd1411  Secret scrub 12 file (DB password literal)           ┐
030e832  Pin test secret regression                            │ 3-FIX-
1f22fc2  Alembic 010s/t/u/v DB state + provider rename         ┘ CLEANUP
```

---

## 8. Còn lại trong plan `260525-4BUG`

| Phase | Bug | Effort | Status |
|---|---|---|---|
| A | #5 `_is_csv_format` mixed-content | 2h | ✅ DONE `3a29456` |
| B | #6 resolver chain per-bot plan_limits | 2h | ⏳ Next |
| C | #4 worker parser routing | 3h | ⏳ Pending |
| D | #1+#2 rechunk-by-id + ambiguity guard | 3h | ⏳ Pending |
| E | E2E load test 3 scenario | 1h | ⏳ Pending |

**Load test scenario** (Phase E):
1. "1tr499 có mấy dịch vụ" → expect bot trả "4 dịch vụ" (Mặt CSD + Râu CSD + Mặt triệt + Râu triệt)
2. Kịch bản xin số điện thoại (CSKH bot Dr.Medispa flow)
3. Kịch bản tư vấn dịch vụ (multi-turn conversation)

Acceptance: HALLU=0 sacred + bot trả đúng theo flow + cite chunks chính xác.
