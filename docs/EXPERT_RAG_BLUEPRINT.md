# Expert RAG Blueprint — synthesis of 3 mindsets + current-state verification

> Hợp nhất **AdapChunk** (adaptive chunking by structure) + **ekimetrics/adaptive-chunking**
> (metric-driven selection) + **RAG-Anything/HKUDS** (multimodal knowledge graph) thành 1
> hệ Expert RAG đa-format, multi-tenant, có log-center/CRM, đạt 5 tiêu chí. Mỗi dòng
> "code state" có evidence `file:line` từ phiên verify 2026-06-19. SỰ THẬT (verified) vs
> GAP (chưa làm / OFF) tách bạch.

---

## 1. Hợp nhất 3 mindset — đóng góp & cách phối

| Mindset | Đóng góp cốt lõi | Vai trong hệ hợp nhất |
|---|---|---|
| **AdapChunk** | 7 tầng: OCR→md · block-detect+atomic · feature-profile · **selector** · cross-check · executor (HDT/SEM/PROP/HYBRID) · narrate-embed | **Khung xương ingest/chunking.** Mỗi doc → chọn chiến lược cắt theo cấu trúc. |
| **ekimetrics** | **Metric-driven selection**: chạy N phương pháp → chấm 5 metric nội tại (size-compliance, intrachunk-cohesion, contextual-coherence, block-integrity, missing-reference) → chọn winner | **Nâng cấp Tầng-4 selector**: thay vì LLM/rule đoán, **đo thực nghiệm** rồi chọn. Khách quan, tái lập. |
| **RAG-Anything** | Multimodal **knowledge graph** (LightRAG): entity+relation xuyên modality, "belongs_to" hierarchy, **vector-graph fusion retrieval**; modality-specific (VLM image, table-trend, equation→concept) | **Tầng retrieval cấp cao**: KG bắc cầu quan hệ giữa chunk/entity → trả lời câu hỏi tổng hợp/đa-hop mà vector thuần miss. |

**Pipeline hợp nhất (target):**
```
File (pdf/word/excel/sheet/txt/html) 
  → [T1] Parse structured-markdown (Kreuzberg, đa-format)
  → [T2] Block-detect + atomic (TABLE/FORMULA/IMAGE) + context-bind
  → [T3] Feature-profile (rule-based, 10 đặc trưng)
  → [T4] Strategy-select  = rule-scorer | ekimetrics-metric | (LLM optional)
  → [T5] Cross-check (5 rule override)
  → [T6] Executor (HDT/SEM/PROP/HYBRID, structural_path, atomic-preserve)
  → [T7] Narrate-then-embed (formula/table→câu) + metadata + embed (jina)
  → store: pgvector (dense) + tsvector (sparse) + [KG entities/relations]
Query → hybrid (dense+sparse+RRF) + [graph traversal] → rerank (jina) → generate (gpt-4.1) → guardrail
Mọi external call (LLM/embed/rerank) → token_ledger (log-center) → admin dashboard
```

---

## 2. Verification — AdapChunk 7 tầng (code state, evidence)

| Tầng | Spec | Code | BẬT? | Evidence |
|---|---|---|---|---|
| **1 Parse→md** | Mistral OCR | **Kreuzberg+OutputFormat.MARKDOWN** (đa-format, 0→72 heading TT09) | ✅ **FIXED phiên này** | `infrastructure/parser/kreuzberg_markdown_parser.py` (committed 5dddfc0) |
| **2 Block-detect** | tag + atomic + context-bind | atomic TABLE/FORMULA/IMAGE/CODE; context-bind ⚠️ một phần | ✅ ON | `shared/chunking/blocks.py:184,279`; ref RAG-Anything `:129` |
| **3 Feature-extract** | 10 đặc trưng rule-based | **9/10** (`detected_language`→`bots.language`) | ✅ ON | `shared/chunking/analyze.py:202` |
| **4 Selector** | LLM call | **rule-scorer** (default) + **ekimetrics_select** (option, OFF) | ⚠️ rule (LLM thay bằng rule — cố ý rẻ/deterministic; ekimetrics có sẵn) | `analyze.py:106,401` |
| **5 Cross-check** | 5 rule override | `apply_cross_check` đủ 5 rule | ⚠️ **default OFF** | `analyze.py:527` |
| **6 Executor** | HDT/SEM/PROP/HYBRID + meta | đủ 4 + recursive + structural_path | ✅ ON | `shared/chunking/strategies.py` |
| **7 Narrate-embed** | formula/table→narrate + 7 meta | **narrate Port+Adapter** (`llm_narrate`) + 6/7 meta (thiếu `confidence_score`) | ⚠️ **narrate OFF**; contextual-enrich **ON** | `infrastructure/narrate/llm_narrate.py`; `NARRATE_THEN_EMBED_ENABLED=False` |

## 3. Verification — ekimetrics & RAG-Anything

| Chiều | Spec | Code | State |
|---|---|---|---|
| ekimetrics metric-select | 5 metric → rank → winner | `ekimetrics_select` (`analyze.py:401`) | ⚠️ có code, default OFF (rule-scorer thắng) |
| RAG-Anything KG | multimodal entity+relation, belongs_to | `infrastructure/graph/knowledge_graph.py` + `nodes/graph_retrieve.py` | ❌ **DISABLED** (`graph_rag_default_mode=disabled`, entity model rỗng) |
| RAG-Anything vector-graph fusion | dense + graph traversal | hybrid (dense+sparse+RRF) ✅ + graph node (off) | ⚠️ vector ✅, graph OFF |
| Modality: table | trend + semantic | atomic + linearize + **stats-index** (số/giá exact) | ⚠️ giữ structure, KHÔNG LLM-trend |
| Modality: image | VLM caption | (narrate có, chưa wire VLM) | ❌ **thiếu VLM image** |
| Modality: equation | LaTeX→concept | narrate llm (LaTeX→câu) | ⚠️ infra có, OFF |

## 4. Verification — đa-format (đã test phiên này, qua registry production)

| Format | Parser | Structured? | Evidence |
|---|---|---|---|
| PDF | kreuzberg_markdown | ✅ 72 heading + bảng | test pass |
| DOCX/Word | docx (python-docx) | ✅ `#`+`\|`bảng | test pass |
| XLSX/Excel | excel_openpyxl | ✅ row+header (key:value) | test pass |
| Google Sheet | google_sheets (CSV-export) | ✅ row-chunk | ⚠️ bug URL `edit?gid=` (xe-3) chưa fix |
| HTML | kreuzberg_markdown | ✅ `#`+bảng | test pass |
| CSV/TXT/MD | csv_chunker/markdown | ✅ | có |

## 5. Verification — RAG answer flow (T1 smartness)

- **Hybrid retrieval** dense(pgvector)+sparse(tsvector BM25)+RRF ✅ + multi-query + rerank(jina) + small-to-big parent/child ✅.
- **BM25 sparse 0-match structural query** ("Điều 56...") → **FIXED phiên này**: structural-OR branch (0→2 precise, không flood). `infrastructure/vector/pgvector_store.py` (uncommitted).
- **Faithfulness/HALLU=0**: sentence-level grounding judge + citation drop-invalid + sacred no-override ✅ (`local_guardrail.py:417`, `generate.py:720`).
- **Coverage**: legal 100% (curated)/spa 60-70% (aggregation) — phụ thuộc retrieval; structural-OR fix giúp structural-pointer query.

## 6. Verification — LOG-CENTER / CRM (yêu cầu cốt lõi)

**Bảng log-center (`token_ledger`)** — mọi external paid call:
| Cột | Có? | Ghi chú |
|---|---|---|
| 4-key (tenant/workspace/bot/channel) | ✅ | `record_tenant_id, workspace_id, record_bot_id, bot_id, channel_type` |
| model + provider | ✅ | |
| token input/output/total/cached | ✅ | `input_tokens, output_tokens, total_tokens, cached_tokens` |
| cost_usd | ⚠️ | LLM có; **embed/rerank = NULL** (gap) |
| start + end time | ✅ | `started_at, finished_at` (+ `duration_ms`) |
| trace_id / request_id | ✅ | |
| action (llm/embed/rerank) | ✅ | |

**Capture points**: LLM non-stream `dynamic_litellm_router.py:756`; embed `jina_embedder.py:299`; rerank `jina_reranker.py:282`.
**GAP log-center**: (a) **streaming generation** (câu trả lời chính) KHÔNG vào token_ledger — chỉ vào `model_invocations` (bảng này **thiếu bot_id/channel** → không rollup per-bot được); (b) embed/rerank cost=NULL.

**Admin dashboard / statistics** (đã có):
- `GET /admin/metrics/usage/timeseries` — per-bot/workspace/tenant/**all-tenants** (1 endpoint, param scope), **time-range** `date_from/date_to` + `group_by=hour|day|month` + breakdown model/action/provider. RBAC: tenant=60, all=100. (`admin_metrics.py:112`)
- `GET /admin/analytics/all-tenants` (level 100), `/analytics/workspace-aggregate`, `/analytics/bots/cost|latency|pass-rate`. (`admin_analytics.py`)
- Rollup `token_ledger` GROUP BY tenant→workspace→bot **chạy được** (đã query thật: spa 9003 tok_in, xe 4690, legal 2721).
**GAP dashboard**: token in/out tách bạch chỉ ở `token_ledger` (Stack A), group-by-workspace lại ở `request_logs` (Stack B) → chưa có 1 endpoint trả "per-workspace token in/out"; thiếu index `(tenant, workspace_id, started_at)`.

## 7. Verification — multi-tenant / multi-language / 5 tiêu chí

- **Hierarchy 4-key** tenant→workspace→bot→channel: **REAL + DB-enforced** (`bots` unique `uq_bots_record_tenant_workspace_bot_channel`); workspace là **entity thật** (`workspaces` table, 3 rows). ✅
- **Multi-language per-bot**: `bots.language` (vi/en), `language_packs`, SysPromptAssembler locale. ✅ (hiện chỉ có bot `vi`, EN chưa test).
- **RLS isolation**: 21 policies + role `ragbot_app`(NOBYPASSRLS) provisioned — **nhưng INERT runtime** (DSN=superuser `ragbot`, `DATABASE_URL_SYSTEM` unset). ❌ chưa enforce. Isolation hiện = app-layer `record_bot_id` filter.
- **5 tiêu chí**:
  - **Cost**: ✅ instrument tốt (token_ledger 4-key + model tiering nano/mini + cascade).
  - **Latency**: ⚠️ total `request_logs.duration_ms` ✅; TTFT/cache-hit ở JSONB (cần promote cột).
  - **Faithfulness=100%**: ✅ HALLU=0 (grounding judge + citation + sacred no-override).
  - **UX (refuse/citation)**: ⚠️ soft-OOS log thành `status=success`; SSE path drop citations.
  - **Performance/scale**: ⚠️ worker single-process in-loop; HNSW global single-table; chưa partition tenant.

---

## 8. SCORECARD + ROADMAP (ưu tiên T1>T2>T3)

### Đã fix phiên này (verified)
1. ✅ **T1 Parser** flat→Kreuzberg-markdown (đa-format, 0→72 heading). Committed `5dddfc0` + 8 test.
2. ✅ **T1 BM25** structural-OR recall (0→2 precise). 318 test pass. (uncommitted)

### Bug thật còn lại (T1/T2)
- **upload_stream.v1 orphan** (data-loss, no consumer) — code fix.
- **Google Sheet URL** `edit?gid=` parse-fail (xe-3 retry-storm) — fix `supports()`.
- **streaming-gen không vào token_ledger** + **model_invocations thiếu bot_id** — log-center incomplete.
- **embed/rerank cost=NULL** trong token_ledger.

### OFF — cần A/B trước khi bật (KHÔNG blind-flip; cost/quality trade-off)
- T5 cross-check (rule-based, low-risk → có thể bật + đo).
- T7 narrate-then-embed (LLM/chunk cost → A/B coverage).
- ekimetrics metric-select (vs rule-scorer → A/B strategy-accuracy).
- multi-vector / ColBERT.

### Cần BUILD (feature mới — cần /plan)
- **RAG-Anything KG**: bật graph_rag (entity extraction model + KG construction + graph-fusion retrieval) — gap lớn nhất vs RAG-Anything.
- **VLM image captioning** (modality image).
- **RLS enforcement cutover** (DSN→ragbot_app + DATABASE_URL_SYSTEM) — multi-tenant trust.
- **Log-center hoàn thiện**: ledger streaming-gen + add bot_id to model_invocations + embed/rerank cost + per-workspace token endpoint + index.

### Metadata gap (cheap)
- `confidence_score` vào chunk metadata (T7 spec 7.3).

---

## 9. Verdict
Code **KHÔNG phải "đống rác"** — đã có **~90% hạ tầng AdapChunk + ekimetrics-select + KG-skeleton + log-center + 4-key multi-tenant + RAGAS eval**. Khoảng cách tới "Expert RAG đủ 5 tiêu chí" = **(a) 2 bug T1 vừa fix**, (b) **enablement + A/B** các tầng OFF, (c) **build KG + VLM + RLS-enforce + log-center hoàn thiện**. Đây là **EVOLVE** (siết ốc + bật + hoàn thiện), KHÔNG phải rewrite.
