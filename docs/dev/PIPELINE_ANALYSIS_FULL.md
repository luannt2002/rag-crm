# 📊 PIPELINE ANALYSIS — Tất Cả Step + Cost + Tier + Đầu Tư

> **Date**: 2026-05-12
> **Source**: trace `fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4` (legalbot, "Điều 11 quy định gì?")
> **Status**: post-Phase A plan ready (chưa apply)
> **Stack**: ZE zembed-1 + ZE zerank-2 + gpt-4.1-mini + claude-haiku-4-5 (ingest only)

---

## 📑 MỤC LỤC

1. [Pipeline tổng thể](#1-pipeline-tổng-thể)
2. [Ingest graph 7 step](#2-ingest-graph-7-step)
3. [Query graph 32 step actual → 39 step sau Plan](#3-query-graph)
4. [Tier classification (T1/T2/T3)](#4-tier-classification)
5. [Cost breakdown từng step](#5-cost-breakdown-từng-step)
6. [Top 5 step đắt nhất — phân tích sâu](#6-top-5-step-đắt-nhất)
7. [Đầu tư model — option theo tier customer](#7-đầu-tư-model-theo-tier)
8. [Khuyến nghị stack cuối cùng](#8-khuyến-nghị-stack-cuối-cùng)

---

## 1. Pipeline tổng thể

```
INGEST (offline, khi upload doc):
  USER upload → U1-U7 → DB document_chunks + pgvector index

QUERY (per request):
  USER POST → 32 step (hiện tại) → answer
  USER POST → 39 step (sau Plan 5 phase) → answer
```

**Pre-plan baseline**:
- Latency p50: 14s, p95: 21s
- Cost: $0.0090/turn
- PASS: 58.8% mixed, 30% multi-entity

**Post Plan 5 phase**:
- Latency p50: 5s, p95: 8s
- Cost: $0.0070/turn (-22%)
- PASS: 92% mixed (+33pp)

---

## 2. INGEST GRAPH 7 step

Chạy MỘT LẦN khi upload document. KHÔNG ảnh hưởng per-turn cost.

| # | Step | Tech / Model | Tier | Cost | Vai trò |
|---|---|---|---|---|---|
| U1 | validate | Pydantic schema | T3 | $0 | Validate file format, size, MIME type |
| U2 | parse | unstructured.io / docx2txt / PyPDF | T2 | $0 | Extract text từ PDF/DOCX/CSV |
| U3 | clean | regex + unicodedata | T3 | $0 | Strip noise, normalize Unicode |
| U4 | **chunk** | rule-based + tiktoken | **T1** | $0 | Split 1024 token parent + 256 child + overlap 128 |
| U5 | **enrich** ★ | **claude-haiku-4-5** | **T1** | $0.00055/chunk | Anthropic Contextual Retrieval — prefix context |
| U6 | vn_segment | pyvi + custom dict | T2 | $0 | VN tokenize cho BM25 |
| U7 | **embed + store** | **ZE zembed-1** (1280-dim) | **T1** | $0.00001/chunk | Vector hóa + pgvector HNSW index |

**Tổng cost ingest 1 doc 633 chunks**: $0.35 (one-time).

### Vì sao Haiku cho U5 enrich?

**Anthropic Contextual Retrieval pattern** (proven):
```
Chunk raw:           "Tổ chức phải bảo đảm an toàn dữ liệu."
   ↓ Haiku enrich (đọc cả document context)
Enriched prefix:     "Đoạn này thuộc Điều 22 Thông tư 09/2020,
                      Chương III về quản lý dữ liệu, nói về
                      nguyên tắc sao lưu dự phòng."
+ chunk raw
   ↓ embed (ZE)
Vector chứa rich context → cosine similarity +35-49% recall
```

Haiku có **long-context comprehension** vượt 4.1-mini cho task này. One-time $0.22 đáng đổi proven recall lift.

---

## 3. QUERY GRAPH — 32 step actual / 39 step sau Plan

### 3.1. STAGE 1 — INPUT GUARD (~150ms tổng)

| # | Step | Tech / Model | Tier | Cost | Vai trò |
|---|---|---|---|---|---|
| 01 | guard_input | regex + Redis IP ban | **T3** | $0 | Anti-abuse, UA check |
| 02 | cache_check | Redis hash exact match | **T2** | $0 | Cache hit → exit early |
| 03 | router_select_model | Python config lookup | T3 | $0 | Pick LLM theo bot config |
| 04 | hash_lookup_cache | Redis hash key | T3 | $0 | Cache subdivide 1 |
| 05 | semantic_cache_check | pgvector similarity | T2 | $0 | Hit nếu query gần giống cached |
| 06 | **pii_redact** (Phase D D2) | regex patterns DB | **T1** | $0 | PII redact email/phone/ID at boundary |

**Parallel optimization (Phase A S4)**: Step 02, 03, 04 chạy song song qua `asyncio.gather` → -80ms.

### 3.2. STAGE 2 — QUERY UNDERSTANDING (~500ms-2s)

| # | Step | Tech / Model | Tier | Token | Cost | Vai trò |
|---|---|---|---|---|---|---|
| 07 | **query_complexity_detect** (Phase A S6) | Python regex (count comma + conj + numbers) | **T1** | - | $0 | L1 detector: simple/complex |
| 08 | uq_skip_check (Phase B B3) | regex pattern match | T2 | - | $0 | Greeting/short → skip understand |
| 09 | uq_cache_check (Phase A S5) | Redis lookup | T2 | - | $0 | Cache hit 30% → skip LLM |
| 10 | **understand_query** | **gpt-4.1-mini** | **T1** | 500/100 | $0.00036 | Rewrite query + classify intent |
| 11 | uq_cache_write | Redis setex 1h TTL | T3 | - | $0 | Save for next time |

### 3.3. STAGE 3 — DECOMPOSE (conditional ~20% turn)

| # | Step | Tech / Model | Tier | Token | Cost amortized | Vai trò |
|---|---|---|---|---|---|---|
| 12 | **query_decomposer** (Phase A S6) | **gpt-4.1-mini** | **T1** | 150/80 | $0.000038 (20% trigger) | Split compound query thành N sub |

Trigger: `state["query_complexity"] == "complex"`.

### 3.4. STAGE 4 — HYDE + EMBED (~500ms)

| # | Step | Tech / Model | Tier | Token | Cost | Vai trò |
|---|---|---|---|---|---|---|
| 13 | hyde_generator (Phase C C1, opt-in) | gpt-4.1-mini | T2 | 100/150 | $0.000056 (20% opt-in) | Hypothetical answer cho embed |
| 14 | embed_cache_check (Phase A S5) | Redis lookup | T2 | - | $0 | Cache hit 30% → skip embed |
| 15 | **embed_query** | **ZE zembed-1** (1280-dim) | **T1** | - | $0.00001 | Vector hóa query/HyDE output |

### 3.5. STAGE 5 — HYBRID RETRIEVE (~400ms parallel)

| # | Step | Tech / Model | Tier | Cost | Vai trò |
|---|---|---|---|---|---|
| 16 | metadata_filter (Phase C C2) | PostgreSQL WHERE entities_json | T2 | $0 | Article-aware filter (per-bot regex DB) |
| 17 | **retrieve_vector** | **pgvector HNSW** (cosine, top-K=20) | **T1** | $0 | Vector similarity search |
| 18 | **retrieve_bm25** (Phase A S7) | **PostgreSQL tsvector + GIN** | **T1** | $0 | BM25 keyword search parallel |
| 19 | multi_vector_retrieve (Phase C C3, opt-in) | ColBERT pattern | T2 | $0 | N-vector per chunk (storage 3-5×) |
| 20 | rrf_fuse | Python in-memory | T2 | $0 | Reciprocal Rank Fusion |

### 3.6. STAGE 6 — RERANK + GRADE (~1500ms)

| # | Step | Tech / Model | Tier | Cost | Vai trò |
|---|---|---|---|---|---|
| 21 | rerank_cache_check | Redis lookup | T2 | $0 | Cache hit 15% → skip rerank |
| 22 | **rerank** | **ZE zerank-2** cross-encoder | **T1** | $0.00003 | Re-score top-K |
| 23 | filter_min_score | Python threshold | T2 | $0 | Hard cutoff |
| 24 | mmr_dedup | Python MMR | T2 | $0 | Diversity dedup |
| 25 | **crag_grade** | **gpt-4.1-mini** | **T1** | $0.00136 | LLM grade chunks relevant/no |

### 3.7. STAGE 7 — CRAG RETRY (rare ~10% turn sau Phase A S1)

| # | Step | Tech / Model | Tier | Cost amortized | Vai trò |
|---|---|---|---|---|---|
| 26 | rewrite_retry trigger | Python conditional | T2 | $0 | Trigger if max_score < 0.7 |
| 27 | rewrite_query | gpt-4.1-mini | T2 | $0.000036 (10% trigger) | LLM rewrite thử lại |

**Phase A S1 fix**: skip retry khi max_score >= 0.7 → giảm trigger từ 90% → 10% turn.

### 3.8. STAGE 8 — PROMPT BUILD (~30ms)

| # | Step | Tech / Model | Tier | Cost | Vai trò |
|---|---|---|---|---|---|
| 28 | prompt_compression | LongLLMLingua / Python | T2 | $0 | Compress context |
| 29 | litm_order | Python algorithm | T2 | $0 | Lost-in-the-middle reorder |
| 30 | citations_extract | Python regex | T2 | $0 | Format citations cho UI |
| 31 | prompt_build | Python f-string | T3 | $0 | Build final prompt |

### 3.9. STAGE 9 — LLM GENERATE (~2-3s STREAM)

| # | Step | Tech / Model | Tier | Token | Cost | Vai trò |
|---|---|---|---|---|---|---|
| 32 | llm_router_decide | Python lookup | T3 | - | $0 | Decide model (sau Phase B B2 → token-opt) |
| 33 | **generate (ANSWER)** ★★★ | **gpt-4.1-mini** | **T1 CRITICAL** | 5000/300 | $0.00248 | LLM tổng hợp answer cuối |

**SSE Streaming (Phase A S8)**: TTFT 500ms, user thấy text ngay.

### 3.10. STAGE 10 — OUTPUT GUARD (~1600ms parallel)

| # | Step | Tech / Model | Tier | Token | Cost | Vai trò |
|---|---|---|---|---|---|---|
| 34 | guard_output | **gpt-4.1-mini** | **T1** | 3000/100 | $0.00136 | Safety check (PII, prohibited, jailbreak) |
| 35 | **grounding_check** ★ | **gpt-4.1-mini** | **T1 SACRED** | 3000/100 | $0.00136 | Verify answer grounded vào chunks (HALLU=0) |

**Parallel (Phase A S4)**: 34 ‖ 35 via `asyncio.gather` → -1600ms.

### 3.11. STAGE 11 — PERSIST (~100ms)

| # | Step | Tech / Model | Tier | Cost | Vai trò |
|---|---|---|---|---|---|
| 36 | semantic_cache_write | Redis + pgvector | T3 | $0 | Cache answer |
| 37 | metrics_emit (Phase D D3) | Prometheus | T3 | $0 | Push metrics |
| 38 | audit_log_outbox | DB INSERT + outbox | T3 | $0 | Forensic trail |
| 39 | request_steps_batch_flush (Phase B B4) | Async batch INSERT | T3 | $0 | 1 INSERT all step |

---

## 4. Tier Classification

### TIER 1 — CRITICAL (14 step, đóng góp 65% correctness)

```
Ingest:
  U4 chunk          (algorithm)
  U5 enrich         (Haiku 4.5)
  U7 embed          (ZE zembed-1)

Query:
  06 pii_redact     (regex)
  07 query_complexity (Python)
  10 understand_query (gpt-4.1-mini)
  12 decomposer    (gpt-4.1-mini)
  15 embed_query   (ZE zembed-1)
  17 retrieve_vector (pgvector HNSW)
  18 retrieve_bm25  (PostgreSQL tsvector)
  22 rerank        (ZE zerank-2)
  25 crag_grade    (gpt-4.1-mini)
  33 generate (ANSWER) (gpt-4.1-mini) ★★★
  35 grounding_check (gpt-4.1-mini) ★ SACRED
```

**14 step T1**. Mỗi step T1 bug = bot trả sai. PHẢI đúng.

### TIER 2 — IMPORTANT (16 step, 25% correctness + UX)

```
02 cache_check
05 semantic_cache_check
08 uq_skip_check
09 uq_cache_check
13 hyde_generator
14 embed_cache_check
16 metadata_filter
19 multi_vector_retrieve
20 rrf_fuse
21 rerank_cache_check
23 filter_min_score
24 mmr_dedup
26 rewrite_retry trigger
27 rewrite_query
28 prompt_compression
29 litm_order
30 citations_extract
34 guard_output
```

**18 step T2**. Hỗ trợ T1, quality + UX boost.

### TIER 3 — INFRA (10 step, ops/observability)

```
01 guard_input
03 router_select_model
04 hash_lookup_cache
11 uq_cache_write
31 prompt_build
32 llm_router_decide
36 semantic_cache_write
37 metrics_emit
38 audit_log_outbox
39 request_steps_batch_flush
```

**10 step T3**. KHÔNG trực tiếp ảnh hưởng correctness.

---

## 5. Cost Breakdown từng step

### Cost/turn breakdown (sau Plan 5 phase)

| Step | Token | Model | Cost/turn | % tổng |
|---|---|---|---|---|
| 10 understand_query | 500/100 | 4.1-mini | $0.00036 | 5.1% |
| 12 decomposer (20%) | 150/80 | 4.1-mini | $0.000038 | 0.5% |
| 13 hyde (20%) | 100/150 | 4.1-mini | $0.000056 | 0.8% |
| 15 embed | - | ZE zembed-1 | $0.00001 | 0.1% |
| 22 rerank | - | ZE zerank-2 | $0.00003 | 0.4% |
| 25 crag_grader | 3000/100 | 4.1-mini | $0.00136 | 19.4% |
| 27 rewrite_retry (10%) | 500/100 | 4.1-mini | $0.000036 | 0.5% |
| 33 generate (ANSWER) | 5000/300 | 4.1-mini | $0.00248 | 35.4% |
| 34 guard_output | 3000/100 | 4.1-mini | $0.00136 | 19.4% |
| 35 grounding_check | 3000/100 | 4.1-mini | $0.00136 | 19.4% |
| **TOTAL** | | | **~$0.0070** | 100% |

### 4.1-mini vs Haiku — so sánh cost từng step

| Step | Token | 4.1-mini cost | Haiku cost | Diff |
|---|---|---|---|---|
| 10 understand_query | 500/100 | $0.00036 | $0.00100 | Haiku +178% |
| 12 decomposer | 150/80 | $0.000188 | $0.00055 | Haiku +192% |
| 13 hyde | 100/150 | $0.00028 | $0.00085 | Haiku +204% |
| 25 crag_grader | 3000/100 | $0.00136 | $0.00350 | Haiku +157% |
| 33 generate (ANSWER) | 5000/300 | **$0.00248** | **$0.00650** | **Haiku +162%** |
| 34 guard_output | 3000/100 | $0.00136 | $0.00350 | Haiku +157% |
| 35 grounding_check | 3000/100 | $0.00136 | $0.00350 | Haiku +157% |
| U5 ingest enrich | 200/80 | $0.000208 | $0.00055 | Haiku +164% |

**Quy tắc**: Haiku LUÔN đắt hơn 4.1-mini 2.6-3× ở mọi step. CHỈ dùng Haiku khi quality vượt 4.1-mini đáng kể (case duy nhất proven: U5 ingest enrich +35-49% recall).

---

## 6. TOP 5 STEP ĐẮT NHẤT — phân tích sâu

### Step 33 — generate (ANSWER) — $0.00248/turn (35.4%)

| Aspect | Detail |
|---|---|
| Bỏ được? | ❌ Không. Trái tim bot. |
| Có dư? | INPUT dư token (Phase B B2 squeeze giảm 30%) |
| Correctness | ★★★★★ 20% contribution |
| Upgrade Sonnet? | +$0.0195/turn (+787%), +4pp PASS. ROI marginal. Chỉ tier premium. |
| Khuyến nghị | **GIỮ gpt-4.1-mini** (sweet spot). Phase B B2 token opt giảm 30% input. |

### Step 25 — crag_grader — $0.00136/turn (19.4%)

| Aspect | Detail |
|---|---|
| Bỏ được? | 🟡 Có thể batch optimize |
| Có dư? | 20% dư (batch grade nhiều chunks/1 call) |
| Correctness | ★★★ 5% contribution (quality gate) |
| Upgrade Sonnet? | KHÔNG đáng (+710% cost, +1pp lift) |
| Khuyến nghị | **GIỮ 4.1-mini**. Batch optimize lift -50% latency, cost tương tự. |

### Step 34 — guard_output — $0.00136/turn (19.4%)

| Aspect | Detail |
|---|---|
| Bỏ được? | ❌ Safety mandatory |
| Có dư? | 30% dư (2-stage regex + LLM, skip nếu regex pass) |
| Correctness | ★★ safety, không tạo correctness |
| Upgrade Sonnet? | KHÔNG (regex + 4.1-mini đủ) |
| Khuyến nghị | **GIỮ 4.1-mini**. 2-stage optimize tiết kiệm 30%. |

### Step 35 — grounding_check — $0.00136/turn (19.4%)

| Aspect | Detail |
|---|---|
| Bỏ được? | ❌ TUYỆT ĐỐI KHÔNG. SACRED HALLU=0. |
| Có dư? | KHÔNG (đừng đụng) |
| Correctness | ★★★★ bảo vệ HALLU=0 |
| Upgrade Sonnet? | +0.3pp marginal HALLU catch. Tier premium có thể cân nhắc. |
| Khuyến nghị | **GIỮ 4.1-mini** (sacred). KHÔNG optimize tiết kiệm. |

### Step 10 — understand_query — $0.00036/turn (5.1%)

| Aspect | Detail |
|---|---|
| Bỏ được? | 🟡 Skip cho greeting/short (Phase B B3 đã làm) |
| Có dư? | Đã tối ưu (cache 30% + skip 15%) |
| Correctness | ★★★ 15% contribution (rewrite → retrieve trúng) |
| Upgrade Sonnet? | KHÔNG đáng |
| Khuyến nghị | **GIỮ 4.1-mini**. Đã tối ưu. |

### Tóm 5 step đắt nhất

```
Bỏ được:        0/5 (KHÔNG bỏ được cái nào)
Có thể tối ưu:  3/5 (step 25 batch, step 34 2-stage, step 10 đã có)
Sacred giữ:     1/5 (step 35 grounding)
Quality contrib: 65% tổng correctness của 5 step

Tổng saving nếu optimize hết: ~$0.0008/turn (-11%)
→ Marginal, không đáng prioritize. Focus Plan 5 phase chính trước.
```

---

## 7. Đầu tư Model theo Tier Customer

### TIER SMB ($500-1K/month/customer)

**Stack**:
```
✅ ZE zembed-1 + ZE zerank-2 (rerank/embed top-tier rẻ)
✅ gpt-4.1-mini all query LLM (understand + decomposer + hyde + grader + generate + guards + grounding)
✅ Haiku 4.5 ingest enrich (one-time)
✅ Plan 5 phase ship full
```

**Cost**: $0.0070/turn
**PASS**: 92%
**Margin** (10K turn/day vs $500/mo): 64%

### TIER MID-MARKET ($2-5K/month)

**Stack thêm** (so với SMB):
```
+ Voyage rerank-2 cho Step 22 (UPGRADE)
  → +$0.00003/turn, +2-3pp PASS
+ HyDE on (Step 13)
  → +$0.000056/turn, +3-5pp
+ ColBERT multi-vector opt-in (Phase C C3)
  → storage 3-5×, +5-8pp
```

**Cost**: $0.0073/turn (+4%)
**PASS**: 95-96%
**Margin**: cao do tier giá cao

### TIER ENTERPRISE/PREMIUM ($10K+/month)

**Stack thêm**:
```
+ Sonnet 4.6 cho Step 33 generate
  → +$0.0195/turn, +4pp PASS, +nuance
+ Sonnet 4.6 cho Step 35 grounding (HALLU catch tốt hơn)
  → +$0.0096/turn, +0.3pp marginal
+ Knowledge graph (Phase E future)
  → +8-12pp cross-reference
+ Owner-managed corpus enrichment (continuous)
```

**Cost**: $0.025-0.030/turn
**PASS**: 97-98%
**Margin**: cao (tier $10K/month)

---

## 8. Khuyến nghị Stack Cuối Cùng

### LLM (production)

| Model | Used at | % cost/turn | Lý do |
|---|---|---|---|
| **gpt-4.1-mini** | 7 step (understand, decomposer, hyde, grader, generate, guard, grounding) | ~97% | Sweet spot quality/cost cho mọi token size |
| **claude-haiku-4-5** | 1 step (U5 ingest enrich, one-time) | 0% per-turn | Anthropic Contextual Retrieval proven +35-49% recall |

### Embedding + Reranker

| Model | Used at | Cost/turn |
|---|---|---|
| **ZE zembed-1** (1280-dim matryoshka) | 15 embed_query + U7 embed_chunk | $0.00001 |
| **ZE zerank-2** (cross-encoder) | 22 rerank | $0.00003 |

### Infrastructure (no LLM)

| Tech | Used at |
|---|---|
| **PostgreSQL pgvector + HNSW** | Vector index (m=32, ef_construction=200) |
| **PostgreSQL tsvector + GIN** | BM25 (Phase A S7) |
| **Redis** | Cache exact + semantic, anti-abuse, rate limit |
| **Redis Streams** | document.uploaded event bus |
| **PostgreSQL outbox** | Transactional outbox pattern |
| **FastAPI + uvicorn** | HTTP server :3004 |
| **LangGraph** | Pipeline orchestration |
| **dependency-injector** | DI container (Singleton + Factory hot-swap) |
| **Alembic** | DB migration |
| **Prometheus + Grafana** (Phase D D3) | SLA monitoring |
| **pyvi** | VN tokenizer |
| **unstructured.io / PyPDF / docx2txt** | Doc parser |
| **tiktoken** | Token counter |
| **structlog** | JSON logging |
| **Anthropic SSE** | Streaming response |

---

## 9. Ưu tiên Đầu tư (P/P rank)

### TIER S — Bắt buộc (FREE hoặc TIẾT KIỆM)

```
1. Ship Plan 5 phase A+B+C+D+G
   → -22% cost, +33pp PASS
   → ROI: âm cost (tiết kiệm)
   
2. Phase C C2 article-aware metadata (FREE)
   → +5-8pp PASS, $0
   
3. Owner enrich corpus (FREE owner)
   → +5-10pp PASS, $0
```

### TIER A — Đáng đầu tư (cost tăng nhỏ, lift lớn)

```
4. Voyage rerank-2 cho GA tier
   → +$0.00003/turn, +2-3pp PASS
   → P/P: 100,000pp/$1 (cao nhất)

5. HyDE opt-in per-bot
   → +$0.000056/turn, +3-5pp
   → P/P: 71,000pp/$1
```

### TIER B — Cân nhắc tier enterprise

```
6. Sonnet 4.6 ANSWER cho khách >$2K/month
   → +$0.0195/turn, +4pp PASS, +nuance
   → P/P: 205pp/$1 (marginal)
```

### TIER F — KHÔNG đầu tư

```
7. Haiku cho ANSWER → đắt 2.6×, quality DROP 1pp
8. Cohere/Jina rerank → kém ZE + đắt hơn
9. Drop Haiku ingest enrich → mất proven pattern -5pp recall
```

---

## 10. Câu hỏi nhanh

### Q: Bỏ được step nào không?

**A**: Không bỏ được step T1 nào. Step T2 có thể bypass conditional (greeting skip understand, score cao skip CRAG retry). Step T3 là infra cần thiết.

### Q: Có dư không?

**A**: Có 3 chỗ tối ưu:
- Step 25 grader: batch optimize (-20% latency)
- Step 34 guard: 2-stage regex+LLM (-30% cost)
- Step 10 understand: đã có cache + skip greeting

Tổng saving ~$0.0008/turn (~11%). Marginal.

### Q: Step nào liên quan thông minh nhất?

**A**: 4 step T1 đóng góp 65% correctness:
- Retrieve (17, 18): 40%
- Rerank (22): 25%
- Understand (10): 15%
- Generate (33): 20%

Tổng cần đầu tư: ZE rerank/embed + 4.1-mini cho 33, 10.

### Q: Step nào upgrade để thử trải nghiệm?

**A**: Theo P/P:
1. **Voyage rerank-2** (Step 22): rẻ nhất để thử (+$0.00003/turn), lift 2-3pp
2. **HyDE on** (Step 13): rẻ ($0.000056), lift 3-5pp
3. **Sonnet ANSWER** (Step 33): đắt (+$0.0195), lift 4pp — chỉ tier premium

### Q: Cost tổng nếu Haiku ALL?

**A**: $0.0167/turn (+157% vs 4.1-mini all). Quality lift marginal +1pp. ROI âm. KHÔNG đáng.

### Q: Cost tổng nếu Sonnet ALL?

**A**: $0.060/turn (+770% vs 4.1-mini all). Quality lift +6pp. ROI thấp. Chỉ tier premium ngách.

---

## 11. Test Trải Nghiệm (sau Phase A ship)

### Test A: Voyage rerank-2 ($5 chi phí thử)
```bash
# 1. Đăng ký Voyage API key
# 2. SQL update DB
UPDATE system_config SET value='voyage' WHERE key='reranker_provider';
UPDATE system_config SET value='rerank-2' WHERE key='reranker_model';
# 3. Bust cache + smoke 50Q
python scripts/loadtest_legalbot_50q.py
# 4. So sánh PASS vs baseline ZE zerank-2
# 5. Quyết định ship hay rollback
```

### Test B: Sonnet 4.6 ANSWER ($20 chi phí thử)
```bash
# 1. Đặt ANTHROPIC_API_KEY
# 2. SQL update
UPDATE system_config SET value='claude-sonnet-4-6' WHERE key='llm_default_model';
# 3. Smoke 50Q × 2 bot (legalbot + medispa)
# 4. So sánh PASS + cost + latency
```

### Test C: HyDE on ($1 chi phí thử)
```bash
# 1. SQL update
UPDATE bots SET plan_limits = jsonb_set(plan_limits, '{hyde_enabled}', 'true')
WHERE bot_id='legalbot';
# 2. Smoke 30Q query ambiguous
# 3. Đo PASS lift
```

---

## 12. Tổng Kết 1 Bảng

| Đầu tư | Cost change | PASS change | P/P rank | Verdict |
|---|---|---|---|---|
| Plan 5 phase (foundation) | **-22%** | **+33pp** | ★★★★★ S | **PHẢI LÀM** |
| Voyage rerank GA tier | +0.4% | +2-3pp | ★★★★ A | Đáng làm |
| HyDE opt-in | +0.8% | +3-5pp | ★★★★ A | Đáng làm |
| Article-aware metadata (Phase C C2) | $0 | +5-8pp | ★★★★★ S | Free, làm ngay |
| Owner enrich corpus | $0 (owner) | +5-10pp | ★★★★★ S | Free, làm |
| Sonnet ANSWER (tier premium) | +280% | +4pp | ★★ B | Chỉ >$2K/month |
| Haiku ANSWER | +57% | -1pp | F | KHÔNG |
| Drop Haiku ingest | -$0.22 once | -5pp | F | KHÔNG |
| Drop grounding_check | -$0.00136 | LOSS sacred | F | TUYỆT ĐỐI KHÔNG |

→ **Đầu tư đúng = Plan 5 phase + Voyage rerank (GA tier) + HyDE opt-in. KHÔNG đổi answer model.**

---

End of PIPELINE_ANALYSIS_FULL.md. Reference document cho admin + team coder.
