# Phân tích trade-off TẤT CẢ flag on/off — Ragbot

> **Nguồn**: grep `src/ragbot/shared/constants/*.py` — **83 flag `_ENABLED` (41 ON / 42 OFF) + 4 action-flag (observe/block)**. Default đọc từ **constant** (không tin comment — nhiều comment stale).
> **Câu hỏi cốt lõi**: flag on/off có "expert" không? → **Flag CHỈ hợp lệ cho trade-off THẬT + per-tenant. Debt = migration-flag kẹt-OFF + default không revisit.**

---

## ⚠️ CORRECTION rule#0 (comment lừa cả tôi + phân tích ngoài)
| Flag | Comment ghi | Constant THẬT |
|---|---|---|
| `PIPELINE_PARALLEL_REWRITE_MQ_ENABLED` | "default OFF" | **True** (đã bật) |
| `PIPELINE_PARALLEL_CACHE_UNDERSTAND_ENABLED` | "default OFF" | **True** (đã bật) |
→ **Parallelism ĐÃ BẬT.** Kết luận trước ("bật parallel để nhanh") **SAI** — chúng đã ON. Chỉ **async-grounding còn OFF**.

---

## 1. Phân loại flag theo LOẠI trade-off

### 🟢 A. PERF/PARALLELISM — trade-off **latency vs endpoint-load** (đa số ĐÃ ON, đúng)
| Flag | Default | Trade-off |
|---|---|---|
| PIPELINE_PARALLEL_CACHE_UNDERSTAND | **ON** | cache∥understand — nhanh, nhưng cache-hit thì phí understand. OK vì load-test bypass_cache |
| PIPELINE_PARALLEL_REWRITE_MQ | **ON** | rewrite∥multi_query (disjoint output) — nhưng code ghi "**503 under concurrency**" trên endpoint nghẽn |
| PIPELINE_PARALLEL_OUTPUT_GUARDS | **ON** | guards chạy song song |
| PIPELINE_PRE_RETRIEVAL_PARALLEL | **ON** | classifier + resolver song song |
| PIPELINE_MULTI_QUERY_EMBED_BATCH | **ON** | batch embed |
| STATS_INDEX_RACE | OFF | stats∥vector race — OFF vì risk overload endpoint |
| SPECULATIVE_RETRIEVE / STREAMING / MULTI_QUERY_SPECULATIVE | OFF | speculative — thêm LLM-call, risk 503 |
→ **Đánh giá**: parallel ĐÃ bật hợp lý. Cái OFF (speculative/race) = **tránh overload endpoint chậm** — default đúng cho endpoint này.

### 🔴 B. SAFETY GATES — trade-off **HALLU-safety vs false-block/latency** (conservative, đúng)
| Flag/action | Default | Trade-off |
|---|---|---|
| GROUNDING_CHECK | ON (sync) | check bịa — nhưng **BẮT user chờ 8-30s** |
| **GROUNDING_CHECK_ASYNC** | **OFF** | 🎯 ship-then-check — nhanh, HALLU-window nhỏ. **NÊN BẬT** (endpoint chậm → latency-win lớn) |
| GROUNDING_CONFIRMED_ACTION | observe | block answer khi ungrounded — observe = đo FP trước |
| NUMERIC_FIDELITY_ACTION | observe/**block xe** | chặn số bịa — xe đã block (FP 0/84 đo) |
| BRAND_SCOPE_ACTION | observe/**block xe** | chặn false brand-denial — xe đã block |
| CLAIM_FIDELITY_ACTION | observe | chặn scope-over-extension phi-số — observe đo FP |
| EMPTY_ANSWER_GUARD (per-bot) | ON xe+spa | blank→template |
→ **Đánh giá**: gate mặc định observe/off = **an toàn (đo FP trước khi block)** — đúng kỷ luật. **Ngoại lệ đáng đổi: async-grounding nên ON.**

### 🟡 C. RETRIEVAL FEATURES — trade-off **coverage/quality vs LLM-cost** (mix, hợp lý)
| Flag | Default | |
|---|---|---|
| RERANKER / MULTI_QUERY / DECOMPOSER / CROSS_DOC_RECONCILE / STATS_CODE_LOOKUP / STATS_PRICE_OF_ENTITY / STATS_SUPERLATIVE / LATE_CHUNKING_SLIDING / LITM_REORDER / STRUCTURED_REF_EXTRACTION | **ON** | core retrieval quality — giữ 93% đúng |
| HYDE / NEIGHBOR_EXPAND / ADAPTIVE_CONTEXT / MULTI_VECTOR / AUTO_MERGE_RETRIEVAL / ADAPTIVE_RERANK_WEIGHT / RETRIEVAL_MULTISTAGE / RERANK_THRESHOLD_GATE_AFTER_CLIFF | OFF | tính năng nâng cao chưa chứng minh lift / thêm cost |
→ **Đánh giá**: core ON, advanced OFF = **đúng** (chưa đo lift thì OFF).

### ⚙️ D. INGEST FEATURES — trade-off **ingest-quality vs cost/complexity**
| Flag | Default | |
|---|---|---|
| ADAPCHUNK_BLOCK_PIPELINE | ON | (nhưng registry-path emit flat-text → gap đã ghi report) |
| LATE_CHUNKING_SLIDING / ENRICH_ROW_GATE / CONTENT_TYPE_DISPATCH / CR_PROMPT_CACHE | ON | |
| CONTEXTUAL_RETRIEVAL / EMBEDDING_SEMANTIC_CHUNK / DIFF_REINGEST / FORMULA_IMAGE_ATOMIC_PROTECT / CR_ENHANCED | OFF | O(n²) token-storm / chưa cần (thay bằng late_chunking) |
→ contextual_retrieval OFF = **đúng** (superseded by late_chunking, tiết kiệm LLM).

### 🧪 E. EXPERIMENTAL/ADVANCED — mostly OFF (đúng — chưa chứng minh)
| Flag | Default |
|---|---|
| REFLECTION / SELF_RAG / CAG_MODE / CASCADE_ROUTING / SPECULATIVE_* / HYDE / MULTI_VECTOR / NEIGHBOR_EXPAND / ENTITY_GROUNDING / RECAP_PII / XML_WRAP | **OFF** |
→ Tính năng nghiên cứu, OFF-by-default = **đúng** (bật khi đo được lift).

### 🔧 F. INFRA/SAFETY — deployment
| Flag/knob | Default | |
|---|---|---|
| `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` (env) | ON | 🔴 = RLS INERT (gap bảo mật #1) — nên OFF sau khi flip DATABASE_URL_APP |
| WARMUP / GENERIC_VOCAB / ROBUST_JSON_PARSER / PROMPT_COMPRESSION / HEURISTIC_INTENT | ON | ổn định |
| SECURITY_HEADERS_HSTS / RETRY_MAX_ATTEMPTS=3 | OFF/3 | HSTS off (dev); retry 3 → nên 2 |

---

## 2. VERDICT: flag on/off có expert không?

**Có 3 loại flag, đánh giá riêng:**

| Loại | Có nên flag? | Ở dự án này |
|---|---|---|
| **Trade-off THẬT** (phụ thuộc deployment: endpoint-capacity, HALLU-tolerance, cache-rate) | ✅ CÓ — nhưng default theo DATA | parallel (endpoint-load), grounding (HALLU), gates (FP) — **hợp lý** |
| **Per-tenant behavior** | ✅ CÓ | numeric/brand/claim action per-bot — **đúng** |
| **Migration** (ship→đo→bật→**XÓA**) | ⚠️ TẠM — phải xóa sau | ← **debt: nhiều flag chứng minh rồi vẫn còn (parallel), comment stale** |

### 🎯 Sự thật (anh đúng phần nào):
1. **83 flag = config surface LỚN** → rủi ro "quên revisit / comment stale lừa người đọc" là **THẬT** (chính em bị lừa "parallel OFF" trong khi nó ON).
2. NHƯNG **không phải on/off bừa** — đa số là **trade-off có chủ đích + default an toàn** (measure-first, HALLU=0 sacred). Cái OFF (speculative/experimental) = **đúng** cho endpoint chậm + chưa đo lift.
3. **Debt THẬT** = (a) **comment stale** (ghi OFF nhưng constant ON) → phải sync, (b) **migration flag chứng minh rồi chưa XÓA** → giảm surface, (c) **default chưa revisit theo endpoint hiện tại** (async-grounding nên ON).

### ✅ Hành động expert (không "bỏ flag", mà "kỷ luật flag"):
1. **Bật `GROUNDING_CHECK_ASYNC_ENABLED`** (endpoint chậm → cắt 8-30s, HALLU-window nhỏ + vẫn log). — default cần revisit.
2. **retry 3→2** (giảm tail-latency).
3. **Sync/xóa comment stale** ("default OFF" → "default ON") ở parallel flags.
4. **Giữ nguyên** experimental OFF + gates observe (đúng, đừng bật bừa).
5. **RLS**: flip DATABASE_URL_APP → tắt superuser-escape (gap bảo mật, cần credential owner).

**Nguyên tắc**: flag = trade-off có chủ đích + default DATA-DRIVEN + migration phải hoàn thành vòng (flip→xóa). KHÔNG phải "code chuẩn thì khỏi flag" — vì trade-off thật (endpoint-capacity, HALLU-tolerance) BẮT BUỘC có knob.

*Mọi default dẫn từ constant `_04..._26`. Comment stale được flag riêng. 83 flag enumerated `grep DEFAULT_*_ENABLED`.*
