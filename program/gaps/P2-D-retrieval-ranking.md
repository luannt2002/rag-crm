# P2-D — RETRIEVAL & RANKING AUDIT (Phase 2 · gaps + verdicts)

> Auditor P2-D (was P1-D). STANCE = **EVOLVE** (engine swap chỉ qua ADR). READ-ONLY src/alembic/tests.
> Anchor branch `fix-260604-action-slotmachine-dead-key`, HEAD `7dd1f84`. Input: `program/context/P1-D-retrieval-ranking.md`, `program/P1-SYNTHESIS.md §4 (Q5/Q16/Q17)`, charter 6 axes.
> Mọi claim = `file:line` / commit / psql / link. **SỰ THẬT** (đo được) vs **GIẢ THUYẾT** (gắn nhãn).
> EXPLAIN ANALYZE chạy thật trên `ragbot_v2_dev` (psql superuser DSN, 2026-06-10) — kết quả §4.

---

## 1. Bảng nhãn component (✅ tốt · 🕰 lỗi-thời-vs-2026 · ↔️ plan-vs-live · 🐛 bug)

| # | Component | Nhãn | Evidence | Verdict 1 dòng |
|---|---|---|---|---|
| 1 | **Cliff filter** (distribution-aware gap-cut) | ✅ | `query_graph.py:792-860` (`_cliff_detect_filter`); const `_01:115/133/141`; saga `cfb7717`→`a339806`→`8336f83` | Thiết kế **đúng-bài**: cắt theo hình-dạng phân phối từng query → miễn nhiễm 2 lần swap reranker mà static-floor đã fail. PRAISE. |
| 2 | **Retrieval safety-net** (union top-N retrieval-order khi reranker disagree, score-stamped) | ✅ | `query_graph.py:5163-5199`; origin `ccc9f57` forensic 2026-06-05 (zerank-2 dìm BM25-rank#1 → rank-8) | "Vết sẹo production" tiền không mua được. Stamp = `min(surviving rerank score)` để sống qua CRAG floor 0.3 — chi tiết đúng. Ít hệ prod có. PRAISE. |
| 3 | **ZE circuit-breaker + fail-soft** | ✅ | P1-D A4; `RetrievalError`→`mode="rerank_fallback"` giữ retrieval order + webhook `:4900-4926`; CB per reranker (P1-SYNTHESIS §2) | Aux dependency KHÔNG giết pipeline chính (graceful degrade đúng claude-mem pattern). PRAISE. |
| 4 | **Hybrid weighted-RRF trong 1 SQL** (FULL OUTER JOIN dense+sparse) | ✅ | `pgvector_store.py:484-501`; rrf_k=60 `_00:174`, w 0.5/0.5 `_02:98-99` | Cấu trúc CTE chuẩn, embedding propagate `float4[]` cho MMR cosine thật (không re-embed). PRAISE cấu trúc. (Tham số → 🕰 mục 2). |
| 5 | **CRAG-lite score-mode-aware fallback** | ✅ | `query_graph.py:5576-5615`; relative gate cho mode=bypass (RRF ~0.01) vs absolute floor cho mode=rerank | Fix scale-mismatch refuse-oan — đủ thành phần paper + sửa đúng lỗi calibrate scale. PRAISE. |
| 6 | **HNSW filter-then-exact pushdown** (bot filter local-CTE) | ✅ | `pgvector_store.py:_doc_filter_sql:181-223`; alembic `0108`; **EXPLAIN §4** | Bot filter áp **PRE** scan (btree `ix_chunks_bot_doc`), vector sort EXACT. Đúng best-practice "narrow then exact-score" (pgvector 2026). PRAISE — nhưng xem 🐛 caveat scale. |
| 7 | **RRF k=60 fixed (3 key riêng)** | 🕰 | `rag_rrf_k`/`lexical_rrf_k`/`rrf_k` đều =60, 3 chỗ khác nhau; chưa sweep | Chuẩn 2026: k=60 vẫn là **baseline đúng**, nhưng learned/weighted fusion tốt hơn KHI có tuning data. Xem §2. |
| 8 | **Branch weights 0.5/0.5 fixed** (per-intent blend gated OFF) | 🕰 | `_02:98-99`; `adaptive_rerank_weight_enabled` default-gated `query_graph:3984-4001` | Fixed 0.5/0.5 = "no-prior" mặc định; tuned-per-corpus tốt hơn khi có nhãn. Xem §2. |
| 9 | **Cliff heuristic floor 0.05 / gap 0.35 hardcoded-default** | 🕰 | `_01:115/133`; drift 2 lần (`03cab24`/`d834c3b`/`2258581`/`8336f83`) | Cross-encoder scores **well-calibrated** (2026 source) → percentile/learned floor khả thi; cliff vẫn an toàn hơn static. Xem §2. |
| 10 | **zembed-1 1280-dim matryoshka** | 🕰 | alembic `0085`; swap `b9e7761` | Single-vector 1280-dim; 2026 frontier multilingual = Qwen3-Embed-8B / Llama-Embed-Nemotron. VN: VN-MTEB cảnh báo APE-models (bge-m3) yếu. Xem §2. |
| 11 | **underthesea VN tokenizer** | 🕰 | `shared/vi_tokenizer.py:29-77` | underthesea = ổn cho compound segment; chưa benchmark vs newer VN tokenizer. **GIẢ THUYẾT** (low priority — sparse-side, không phải bottleneck đo được). |
| 12 | **HNSW m=32 / ef_construction=200 / ef_search=64 global** | 🕰 + 🐛-caveat | `0085`; `_00:172-173`; **EXPLAIN §4: idx_scan=0 lifetime** | ef_search **KHÔNG config-driven** (param-default, 0 caller override — grep §4.2). Corpus 560 rows → HNSW chưa từng chạy. Xem §2 + 🐛-scale §4. |
| 13 | **Tie-break / determinism** | 🐛 (re-judged) | `6547fb6`→**REVERT `2f5ed41`**; verdict `7dd1f84` (legal 87→73-75) | Tie-break-by-arbitrary-UUID phá dense-corpus legal. Re-judgment + experiment → §3. |
| 14 | **plan 260604-bm25-vietnamese-aware** | ↔️ | DRAFT `plans/260604-bm25-vietnamese-aware/plan.md:4`; L1 đã live `4d750d2`; symbol-phrase live `f6eeb42`; **L2/L3 chưa ship** (`pgvector_store.py:412-415` vẫn AND-mode) | Plan-vs-live: vài mitigation đã rời rạc live, OR/quorum fallback chưa. Xem §5 Q-mở. |
| 15 | **True BM25 (ts_rank_cd ≈ BM25)** | 🕰 | P1-D LACKS#1; `ts_rank_cd flags=5`; pg_textsearch/VectorChord research-only (`docs/master/12-L:6-16`) | Thiếu IDF-saturation/k1/b; VectorChord-BM25 2.4-6.5× ES. Engine-swap candidate → cần ADR + A/B (§5). |
| 16 | **`rerank_input_pool` chưa tách `rerank_top_n`** | 🐛-gap | plan `260605` Phase 1 dang dở; P1-D LACKS#4 | Two-stage chuẩn = reranker thấy 30-50, trả 5-10. Cần trace 1 request aggregation (§5 Q-mở). |

**Đếm**: ✅ **6** · 🕰 **6** (mục 7-12, +15) thực ra 7 nếu tách 15 · 🐛 **2** (mục 13 tie-break, mục 16 input-pool gap; mục 12 có 🐛-caveat scale) · ↔️ **1** (mục 14). Tổng nhãn-chính: ✅6 / 🕰7 / 🐛2 / ↔️1.

---

## 2. 🕰 — chuẩn 2026 là gì + nguồn (≤4 web search, prefer code-evidence)

### 2a. RRF k=60 fixed vs weighted/learned fusion (mục 7-8)
- **Chuẩn 2026**: RRF k=60 vẫn là **baseline đúng cho zero-shot/ensemble** (rank-only → giải score-incompatibility). NHƯNG: "*learned, normalized score-combination strategies should be preferred when modest tuning data are available… RRF prone to performance non-smoothness under domain shift*". Weighted-RRF (Elastic/OpenSearch) cho fine-grained control khi cần.
- **Verdict EVOLVE**: GIỮ k=60 làm default. Ragbot ĐÃ có `adaptive_rerank_weight_enabled` per-intent blend (`query_graph:3984`) = đúng hướng "weighted khi có prior" — chỉ **gated OFF, chưa sweep**. Hành động Phase 3: A/B k∈{20,60,120} + weight grid trên 91Q graded TRƯỚC khi đổi default. Domain shift legal-corpus chính là case "non-smoothness" mà nguồn cảnh báo → có thể giải thích flip §3.
- Nguồn: [Elastic weighted-RRF](https://www.elastic.co/search-labs/blog/weighted-reciprocal-rank-fusion-rrf), [OpenSearch RRF](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/), [Digital Applied Hybrid 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026).

### 2b. Cliff floor 0.05/gap 0.35 vs learned per-corpus threshold (mục 9)
- **Chuẩn 2026**: "*Cross-encoder scores are often better calibrated across query types… choosing a score at which to drop documents is significantly more reliable… binary classification losses train calibrated probabilities useful for a fixed threshold*". → Per-corpus learned/percentile floor LÀ khả thi cho cross-encoder (zerank-2).
- **Verdict EVOLVE**: Cliff (gap-relative) vẫn **an toàn hơn** static-floor qua provider swap (Ragbot đã trả giá 2 lần drift — bằng chứng `feedback_threshold_drift_post_migration`). Đề xuất: per-bot score-histogram → **percentile floor** thay constant tay (P1-D LACKS#3); WE-4 đã có tooling đo (`33644d2`/`befd2a4`). KHÔNG bỏ cliff, mà **bổ sung** floor calibrated per-bot. A/B gate trước default.
- Nguồn: [Elastic semantic reranker](https://www.elastic.co/search-labs/blog/elastic-semantic-reranker-part-1), [Cross-encoder reranking](https://mbrenndoerfer.com/writing/reranking-cross-encoders-information-retrieval).

### 2c. zembed-1 1280-dim còn cạnh tranh? (mục 10)
- **Chuẩn 2026 (MTEB Mar-2026)**: multilingual top = **Qwen3-Embedding-8B (70.6)** + **Llama-Embed-Nemotron-8B** (#1 250+ langs, open-weight). **VN-MTEB (ACL findings-eacl.86)**: RoPE-based (e5-Mistral-7B, e5-Qwen2-7B) > APE-based (gte-multilingual-base, **bge-m3**, m-e5-large) trên tiếng Việt.
- **Verdict EVOLVE**: zembed-1 (zerank ecosystem) chưa có trên VN-MTEB → **GIẢ THUYẾT** cạnh tranh chưa đo. Hành động: benchmark zembed-1 vs Qwen3-Embed-8B (hoặc API voyage-3) **trên corpus thật từng bot** (recall@K + coverage 91Q) trước ADR swap. Lưu ý cost: re-embed = ALTER+wipe (alembic 0085 pattern) + đổi HNSW dim. Đây là decision **D14-D17 AdapChunk engine** trong charter — swap qua ADR.
- Nguồn: [Embedding leaderboard Mar-2026](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/), [VN-MTEB](https://aclanthology.org/2026.findings-eacl.86/), [Milvus 2026 guide](https://milvus.io/blog/choose-embedding-model-rag-2026.md).

### 2d. HNSW params vs corpus size (mục 12)
- **Chuẩn 2026**: "*For filtered search, increasing M/ef_construction yields diminishing returns under filtering… prefer lighter graphs, allocate tuning budget to search-time params (ef_search)*". Và: "*retrieve a small candidate set [structured filter], then run exact vector scoring over those rows… works well when another retrieval step narrowed the search space*".
- **Verdict EVOLVE**: Ragbot pattern (bot filter → exact sort, §4) = **CHÍNH XÁC best-practice 2026 cho small/filtered corpora**. m=32 hơi nặng so khuyến nghị filtered (m=16), nhưng index chưa từng dùng (idx_scan=0) nên vô hại hiện tại. **Cần policy theo row-count**: ngưỡng nào planner switch sang HNSW → khi đó ef_search=64 phải config-driven (hiện hardcoded). GIẢ THUYẾT: corpus chưa đủ lớn nên chưa đau.
- Nguồn: [ParadeDB pgvector tuning](https://www.paradedb.com/learn/postgresql/tuning-pgvector), [pgvector HNSW PG18 2026](https://nerdleveltech.com/pgvector-hnsw-postgres-18-production-tuning-tutorial), [Filtered ANN arXiv 2602.11443](https://arxiv.org/pdf/2602.11443).

---

## 3. 🐛 Tie-break — RE-JUDGMENT + experiment design (Phase 3)

### Bằng chứng đã có (P1-D + git)
- `6547fb6` thêm tie-break vào 5 điểm ordering; `2f5ed41` **REVERT** cùng ngày. A/B 3-run: legal `thong-tu` baseline **87/91 → 73-75/91** (regress -13pp), HALLU=0 cả 2. Verdict `7dd1f84`: deterministic-by-arbitrary-id **chọn chunk tệ hơn**; variance thật = **LLM temp-0 upstream** (multi-query rewrite / gen / judge).

### Re-judgment (3 lựa chọn của charter)
**Câu trả lời = (c) CẢ HAI, theo thứ tự ưu tiên** — nhưng key tie-break phải **content-aware**, KHÔNG arbitrary id:

- **(a) Stable tie-break by BETTER key** — KHÔNG dùng id/UUID (đã chứng minh phá legal). Đề xuất key bậc thang: `rerank_score DESC → bm25_rank ASC → chunk_index ASC → record_document_id`. Lý do: với corpus điều-luật near-identical (cosine tied), `chunk_index` (thứ tự xuất hiện trong văn bản) là **proxy ngữ nghĩa** cho "điều đứng trước" — ổn định VÀ không ngẫu nhiên. Arbitrary UUID = random permutation → phá cluster đúng. Đây là điểm P1-D gọi LACKS#8 "chưa thử".
- **(b) Fix temp-0 coverage upstream** — variance gốc ở 3 LLM call temp-0 (multi-query/gen/judge). temp-0 KHÔNG deterministic trên hạ tầng (sampling + batching + provider non-determinism). Multi-query fanout sinh variant khác → retrieve set khác → flip. Đây là **nguồn variance lớn hơn** SQL order (bằng chứng: revert SQL tie-break vẫn còn flip per `7dd1f84`).
- **Kết hợp**: SQL tie-break content-aware **giảm** flip ở tầng retrieve; fix upstream variance **giảm** flip ở tầng query-expansion. Cần đo riêng từng tầng mới biết tầng nào đóng góp bao nhiêu — hiện KHÔNG có `gold_chunk_in_set` per-stage (P1-D LACKS#10).

### Experiment design (Phase 3 — đề xuất, KHÔNG thực thi ở Phase 2)
1. **Instrument per-stage stability**: thêm flag eval-only `gold_chunk_in_set` tại retrieve/rerank/cliff vào `request_steps` (Q8 synthesis). Chạy 91Q × 5-run, đo **flip-rate per stage** → attribute variance đúng tầng (SQL-order vs MQ-expansion vs gen).
2. **Arm A** (content-aware tie-break): thay arbitrary-id bằng `rerank_score→bm25_rank→chunk_index`. Đo 91Q × 5-run: coverage + flip-rate. Gate: **giữ ≥85/91 VÀ flip-rate↓**. So `2f5ed41` baseline.
3. **Arm B** (freeze upstream variance): cache multi-query variants per (bot,query-hash) trong 1 eval-run + seed gen. Đo flip-rate còn lại = SQL-order-only contribution.
4. **Quyết định**: nếu Arm-A giữ coverage + diệt flip → ship (đúng EVOLVE). Nếu Arm-A vẫn regress legal → confirm "variance là upstream", accept (chấp nhận flip nhỏ vì HALLU=0 quan trọng hơn determinism). **No-guess**: KHÔNG đổi default trước khi có số.

> **GIẢ THUYẾT cần đo**: legal-corpus là "domain shift" mà RRF non-smoothness (§2a) cảnh báo — tie-break arbitrary-id có thể đã đẩy fused-rank qua cliff-gap khác → đổi cluster. Cần per-flip `chunk_id` diff (synthesis Q17) mới chứng minh — chưa có evidence, đánh dấu rõ.

---

## 4. EXPLAIN ANALYZE — HNSW filter PRE/POST verdict (đo thật)

### 4.1 Setup
- DSN: `DATABASE_URL` (`postgresql+asyncpg://` → psql `postgres@ragbot_v2_dev`, superuser). Corpus đo: **560 chunks total, all embedded**; bot lớn nhất `5f2e12a8…` = **131 chunks** (`Rows Removed by Filter: 429`).
- Query shape lấy từ `pgvector_store.py:485-491` (dense CTE): `WHERE record_bot_id = :bot AND embedding IS NOT NULL ORDER BY embedding <=> CAST(:emb AS vector) LIMIT 20`. Embedding thật từ DB.

### 4.2 Plan (mặc định, ef_search=64)
```
Limit → WindowAgg → Sort → Seq Scan on document_chunks (rows=131 loops=1)
  Filter: ((embedding IS NOT NULL) AND (record_bot_id = '5f2e12a8…'::uuid))
  Rows Removed by Filter: 429
Planning Time: 2.203 ms · Execution Time: 3.703 ms
```
**VERDICT: bot filter áp PRE (Seq Scan + Filter), vector ordering là EXACT sort — KHÔNG dùng HNSW.** Index `ix_chunks_embedding_hnsw` có **`idx_scan = 0` lifetime** (pg_stat_user_indexes, đo trước+sau).

### 4.3 Force `enable_seqscan=off` (ép planner thử index)
```
Bitmap Index Scan on ix_chunks_bot_doc (rows=131)  → Filter: (embedding IS NOT NULL) → Sort
```
→ Planner chọn **btree bot-filter index PRE**, rồi exact-sort — **vẫn KHÔNG đụng HNSW**, kể cả khi seqscan bị tắt.

### 4.4 Unfiltered (toàn bảng, không bot filter)
```
Seq Scan on document_chunks (rows=560) · Execution Time: 8.223 ms · HNSW idx_scan vẫn = 0
```
→ Ở 560 rows, **exact scan (8ms) rẻ hơn HNSW traversal** → planner không bao giờ chọn HNSW.

### 4.5 Kết luận pre/post (settle câu hỏi Q5)
- **HIỆN TẠI (corpus nhỏ)**: bot filter **PRE-scan**, vector sort **EXACT** → **recall = 100%, KHÔNG có HNSW recall-cliff**. Đây CHÍNH LÀ pattern best-practice 2026 cho filtered/small corpora (§2d). ✅ KHÔNG có bug.
- **🐛-caveat SCALE (GIẢ THUYẾT, cần thực nghiệm Phase 3)**: khi 1 bot vượt ngưỡng row-count mà planner switch sang HNSW, câu hỏi pre/post-filter **mới** trở nên load-bearing. pgvector áp filter **POST** HNSW-scan (post-filter recall-cliff cho small-corpus-bot trong bảng multi-tenant lớn) TRỪ KHI dùng iterative-scan (pgvector ≥0.8). Hiện `_doc_filter_sql` đặt filter local-CTE (alembic 0108) để pushdown — nhưng pushdown vào HNSW operator chỉ hiệu lực khi planner THỰC SỰ chọn HNSW. **CHƯA reproduce được vì corpus quá nhỏ.** Cần: nạp 1 corpus ≥50k chunks/bot rồi đo recall@K HNSW-vs-exact + pre/post-filter plan (synthesis Q25/Q10 HNSW policy).
- **ef_search**: `DEFAULT_EF_SEARCH=64` (`_00:173`), **KHÔNG config-driven** — grep: 0 caller trong `orchestration/`/`application/` truyền `ef_search=` (chỉ param-default trong `pgvector_store.py:259/326`). 🕰: khi corpus lớn → ef_search per-corpus phải đẩy lên `system_config` (zero-hardcode rule + diminishing-M-prefer-ef-search 2026).

---

## 5. Trả lời Q5 / Q16 / Q17 (synthesis §4)

### Q5 — HNSW filter record_bot_id pre/post vector scan?
**PRE** (đo §4): Seq Scan + Filter on `(embedding IS NOT NULL AND record_bot_id=…)`, exact sort. HNSW `idx_scan=0` lifetime. **Recall-cliff cho small-corpus bot = KHÔNG xảy ra hiện tại** (exact, 100% recall). Cliff chỉ thành rủi ro KHI corpus đủ lớn để planner chọn HNSW → khi đó post-filter recall-cliff là rủi ro kinh điển; alembic 0108 + iterative-scan là mitigation, **cần đo lại Phase 3 trên corpus lớn** (GIẢ THUYẾT chưa reproduce).

### Q16 — Per-intent context cap ở đâu? safety-net có overflow nó không?
**Vị trí** (P1-D không định vị được — nay chốt): `query_graph.py`
- `_ctx_cap` resolve per-intent `:6169-6179` (`_cap_by_intent[intent]`), emit metadata `:6298`.
- `adaptive_context_max_n` prune `:6079-6095` (default OFF, `DEFAULT_ADAPTIVE_CONTEXT_ENABLED`).
- `compress_chunks` (prompt_compression) `:6040-6072`.
- `neighbor_token_budget` `:5753-5781`.

**Safety-net KHÔNG bị overflow**: tại `:6086-6088` adaptive-prune giữ tường minh `[c for c in graded[_ac_n:] if c.get("_safety_injected")]` → safety-injected chunk (`:5189`) **luôn survive** cap. Đây là thiết kế đúng: "*strong retrieval is never turned into an answer gap*" (comment `:6076-6078`). ✅ Đã đóng đúng lỗ Q16.

### Q17 — Vì sao deterministic-by-id làm legal *tệ hơn* (không neutral)?
**Cơ chế (đo §3 + verdict `7dd1f84`)**: corpus điều-luật near-identical → cosine **tied** ở nhiều chunk. Tie-break-by-arbitrary-UUID = chọn ngẫu-nhiên-nhưng-cố-định 1 permutation → có thể là permutation **TỆ** (đẩy chunk chứa đáp án xuống dưới cliff-gap hoặc ra ngoài top_k). Random-mỗi-run (pre-tie-break) ít nhất có **xác suất** chọn đúng ở một số run → average tốt hơn 1 permutation xấu cố định. Cộng RRF "non-smoothness under domain shift" (§2a): id-order đổi fused-rank → đổi cliff cluster. **CHƯA có per-flip chunk_id diff** (synthesis Q17 yêu cầu) → đây là cơ chế GIẢ THUYẾT, cần experiment §3 Arm-A (content-aware key) để chứng minh + sửa.

---

## 6. "ĐÃ CHUẨN — đừng đụng" (EVOLVE: giữ nguyên, đập = lỗi nặng)

1. **Cliff filter** (`:792-860`) — distribution-aware, survive 2 provider swap. Đừng quay lại static-floor.
2. **Retrieval safety-net** (`:5163-5199`) — vết-sẹo-production forensic thật; stamp-score chi tiết đúng. Đừng bỏ; chỉ tune N qua A/B.
3. **Safety-injected retention qua context-cap** (`:6086-6088`) — đừng prune mất.
4. **CRAG score-mode-aware fallback** (`:5576-5615`) — relative-vs-absolute gate đúng; đừng đổi về single-floor.
5. **Hybrid weighted-RRF CTE + embedding propagate `float4[]`** (`:484-501`) — cấu trúc chuẩn, MMR cosine thật. Tune **tham số** (k, weight) qua A/B; đừng đập cấu trúc.
6. **Bot-filter PRE + exact-sort** (đo §4) — best-practice 2026 cho small/filtered corpus. Đừng ép HNSW khi corpus nhỏ.
7. **circuit-breaker + fail-soft reranker** (`:4900-4926`) — graceful degrade đúng. Đừng đổi sang fail-loud.
8. **VN sparse stack** (underthesea symmetric ingest/query + filler-strip `4d750d2` + structural-prefilter graceful-degrade) — đừng gỡ; chỉ thêm L2 OR-fallback qua A/B per-bot.
9. **Smart-skip CRAG** (`:5226-5273`) — tiết kiệm LLM call khi top-score cao. T2 win, đừng bỏ.

---

## Open questions chuyển Phase 3 (decision-grade)
- True-BM25 engine swap (VectorChord/pg_textsearch) qua LexicalRetrievalPort — A/B 91Q trước ADR (mục 15; Q1 synthesis-D).
- Reranker per-bot percentile floor (auto-calibration) thay constant — chặn drift lần 3 (§2b; Q3 synthesis-D).
- `rerank_input_pool` ≠ `rerank_top_n` (mục 16; plan 260605 Phase 1; trace 1 request aggregation thật).
- Tie-break content-aware experiment (§3) — gate ≥85/91 + flip↓.
- HNSW policy theo corpus-size + ef_search config-driven (§4.5; Q25 synthesis).
- Per-stage `gold_chunk_in_set` instrumentation (P1-D LACKS#10) — tiền đề cho mọi attribution trên.

---
*P2-D · Phase 2 gaps · 2026-06-10 · EXPLAIN ANALYZE chạy thật · evidence-first · STANCE=EVOLVE.*
