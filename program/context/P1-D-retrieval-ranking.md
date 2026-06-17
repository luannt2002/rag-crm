# P1-D — RETRIEVAL & RANKING (hybrid search · cross-encoder · RRF · MMR · CRAG)

> Phase 1 READ-ONLY report. Anchor branch `fix-260604-action-slotmachine-dead-key`, HEAD `7dd1f84` (2026-06-10).
> Mọi finding có evidence `file:line` hoặc commit-hash. Nhãn: **SỰ THẬT** (đo được) vs **GIẢ THUYẾT** (gắn rõ).

---

## (a) Domain map — retrieve → rerank → mmr_dedup → neighbor_expand → grade(CRAG)

Tất cả node là closure trong `build_graph()` (`src/ragbot/orchestration/query_graph.py:1139`, file 8087 dòng).
Wiring: `add_edge("rerank","mmr_dedup")` :8002 · `("mmr_dedup","neighbor_expand")` :8006 · `("neighbor_expand","grade")` :8007 · `("rewrite_retry","retrieve")` :8013.

### A1. `retrieve` — query_graph.py:3162 (≈1,630 dòng, node lớn nhất pipeline)

Sub-stages theo thứ tự, kèm flag per-bot (`_pcfg` = pipeline_config, resolve chain plan_limits > system_config > constants — `shared/bot_limits.py`):

| Sub-stage | Vị trí | Gate flag |
|---|---|---|
| Stats-index routing (B3 self-query, price-range SQL) + race mode | :3166–3260 | `stats_index_repo` DI + `range_query_min_confidence`, `stats_index_race_enabled`; structural-ref guard skip (`structural_ref_fallback_pattern`) |
| Metadata-aware retrieval (JSONB filter) | trong retrieve | `metadata_extraction_enabled`, `metadata_aware_retrieval_enabled`, `metadata_fallback_relax_enabled`, `metadata_layer3_llm_enabled` |
| VN preprocessing (abbrev, diacritic restore, generic vocab, entity grounding) | trong retrieve | `vietnamese_preprocessing_enabled`, `diacritic_restoration_enabled`, `generic_vocab_enabled`, `entity_grounding_enabled`, `bot_custom_vocabulary` |
| Multi-query fanout → parallel hybrid_search → **RRF merge** | :4060–4299 (`mq_rrf_merge_chunks` = `application/services/multi_query_expansion.py:557`, gọi :4291 với `rag_rrf_k`) | `multi_query_enabled`, `multi_query_n_variants`, `multi_query_timeout_s`, `multi_query_enabled_by_intent` |
| MQ relax-retry (RRF re-merge) | :4354–4355 | tự động khi merge nghèo |
| Per-intent RRF weight blend (Phase-C C5) | :3984–4001 | `adaptive_rerank_weight_enabled` → `_resolve_intent_weights` đổ `bm25_weight`/`vector_weight` |
| Lexical/BM25 branch (Strategy+DI, RRF-fuse với vector list) | :4594–4660 | Null Object default OFF (`infrastructure/retrieval/lexical_registry.py`, `pg_bm25_retrieval.py`); `lexical_top_k`, `lexical_rrf_k`=60, `cr_enhanced_enabled` (widen tsvector sang `chunk_context`) |
| Permission pre-filter | :4660+ | `permission_filtering_enabled`, `permission_default_public` |
| Parent-child expansion (small-to-big) | `expand_parent_chunks` :433 | `parent_child_enabled` |
| Autocut (gap-ratio cut trên RRF scores, legacy — cliff ở rerank superseded) | :4727–4738, `_autocut` :781 | `autocut_enabled` (default **False**), `autocut_min_gap_ratio`=0.3 |
| Retrieve-fallback (retry hybrid với original query) | `retry_hybrid_with_original` :393–432 | `retrieve_fallback_enabled` |
| Per-intent top_k slice | `retrieve_top_k_by_intent` (constants `_16_…:84` — factoid 15 / comparison 25 / multi_hop 30 / aggregation 40) | resolve chain |

Route sau retrieve: `_retrieve_route` :7623 — 0 chunks → **early-exit thẳng `generate`** (Stream D RAGO Pareto, tiết kiệm 3-4 LLM call/turn); `graph_rag_mode` = disabled/adaptive/always → `graph_retrieve_node` :7609 (GraphRAG KG, fail-empty, default `"disabled"`).

### A2. Hybrid search engine — `infrastructure/vector/pgvector_store.py`

- `search` (dense-only) :253; **`hybrid_search`** :317–534. **SỰ THẬT** cấu trúc SQL :465–501: CTE `dense` (HNSW cosine `<=>`, ROW_NUMBER) + CTE `sparse` (`websearch_to_tsquery('simple', …)` + `ts_rank_cd(…, flags=5)`) + `FULL OUTER JOIN` → **weighted RRF**: `(:vec_w/(:rrf_k+rank_d)) + (:bm25_w/(:rrf_k+rank_s))`, miss penalty `rrf_miss`. Defaults: `DEFAULT_RRF_K=60` (Cormack canonical, `constants/_00:174`), weights 0.5/0.5 (`_02:98-99`), `DEFAULT_TOP_K=20` (`_00:28`).
- VN tokenization symmetric: ingest index `content_segmented` (compound nối `_` qua underthesea — `shared/vi_tokenizer.py:77 segment_vi_compounds`; warmup-in-lock race fix :29–76); query side mirror :369. Sparse-only filler strip `strip_vn_filler_tokens` :375 (`shared/text_utils.py:18`) + diacritic-strip variant :376–377. NFC normalize :351 (`shared/text_normalization.py:26`).
- Sparse predicate mở rộng opt-in: ILIKE substring fallback (`bm25_substring_fallback_enabled`, seq-scan warning :409–421); **symbol-phrase branch** `phraseto_tsquery` cho code token vd `range(5)` (:423–438, `_extract_symbol_phrase` :43).
- VN structural pre-filter (Chương/Mục/Phần/Điều N → LIKE clause dense-branch, graceful degrade re-query khi 0 rows) :440–522.
- `_doc_filter_sql` :181–223: bot filter đặt **local trong CTE** để planner push vào HNSW operator — pre-alembic-0108 filter nằm ở outer subquery → `ix_chunks_embedding_hnsw idx_scan = 0` (evidence trong docstring :191–203 + `alembic/versions/20260516_0108_chunks_record_bot_id.py`).
- `ef_search` set per-session `SET hnsw.ef_search` :287/:364, clamp 1..`MAX_EF_SEARCH=1000`; `DEFAULT_EF_SEARCH=64` (hạ từ 100, M3.6-F2 — `constants/_00:172-173`).
- RLS-ready: `session_with_tenant` thread `record_tenant_id` → `SET LOCAL app.tenant_id` :361–363 (enforcement còn pending — plan 260610 ISSUE 1).

### A3. HNSW config (SỰ THẬT, alembic)

- Index gốc: `alembic/versions/20260416_0013_pgvector_chunks.py:64-66` — `hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`.
- Rebuild khi đổi dim: `20260512_0085_zeroentropy_embed_2560dim.py:48,155-157` — `_NEW_DIM=1280` vì **zembed-1 native 2560 vượt pgvector HNSW 2000-dim limit** → matryoshka truncate 1280; index recreate `m=32, ef_construction=200`.
- `pg_trgm` extension: `20260520_010l_chunk_context.py:75` (phục vụ chunk_context).

### A4. `rerank` — query_graph.py:4795–5201

1. Per-intent `top_n` (`rerank_top_n_by_intent`, default map `_16_…:63`: factoid 7 / comparison+multi_hop 12 / aggregation 20; global `DEFAULT_RERANK_TOP_N=7`) :4798–4812.
2. Per-bot reranker resolver (`reranker_resolver.resolve_for_bot`) override singleton :4815–4827; binding thật override flag `reranker_enabled` :4834–4839.
3. Skip gates: intent whitelist (`rerank_intent_whitelist`) :4841–4856; per-intent skip-set với **size-safety** (chỉ skip khi pool ≤ top_n, T2.S7) :4858–4875.
4. Bypass taxonomy mode: `empty_input | intent_skip_set | intent_skip | disabled | no_reranker | null_reranker | rerank` :4877–4892. Fail-soft: `RetrievalError` → `mode="rerank_fallback"` giữ retrieval order + webhook notify :4900–4926.
5. Filter strategy dispatch `"threshold" | "cliff"` :4983–5039. **Cliff** = `_cliff_detect_filter` :792–860: floor (`DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR=0.05`) → gap-cut (`GAP_RATIO=0.35`) → `min_keep=3` (`constants/_01:115-141`) + empty-context safety keep-top-1 :823–831; per-intent **skip cliff** cho aggregation/comparison/multi_hop (`DEFAULT_RERANK_CLIFF_SKIP_INTENTS`, `_01:185-191` — đo được: "thong-tu multi_hop → only 1 chunk survived" khi cliff cắt). Prometheus `cliff_drop_total` :5024–5032.
6. Static threshold refuse-gate `_rerank_threshold_gate` :863–908 — **mặc định SKIP khi strategy=cliff** (Wave J2 double-gate fix :5074–5118; opt-in lại qua `rerank_threshold_gate_after_cliff_enabled`, default OFF).
7. **Retrieval safety-net** :5163–5199 — union top-N pre-rerank candidates (RRF/BM25 order) bị reranker under-rank, stamp score = min surviving rerank score để sống qua CRAG floor 0.3; default **ON**, `rerank_retrieval_safety_n=2` (`_01:148`), audit event `rerank_retrieval_safety_net`, chunks đánh dấu `_safety_injected` (được context-cap giữ lại :6074–6088).
8. Propagate `rerank_score_mode` :5201 để CRAG calibrate scale (cross-encoder 0..1 vs RRF ~0.01).

Reranker infra (Port+Registry+Null, `infrastructure/reranker/registry.py:47-62`): `jina`/`jina_ai`, `litellm`, `null`, `viranker_local` (**STUB** — `__init__` raise NotImplementedError), `voyage`, `zeroentropy`. Fail-soft unknown→Null :74-98. Production hiện tại: **ZeroEntropy zerank-2** (seed `ccc9f57`; docs/master/12-L:63).

### A5. `mmr_dedup` — query_graph.py:5678–5724

`mmr_filter` (`shared/mmr.py:72`) trên reranked_chunks; `DEFAULT_MMR_LAMBDA=0.7`, `DEFAULT_MMR_SIMILARITY_THRESHOLD=0.88` (`_14:221-223`); per-intent threshold override (`mmr_similarity_threshold_by_intent` — Bug #10 260525: CSV row-shape chunks bị dedup oan ở aggregation) :5682–5697. Embedding propagate end-to-end từ hybrid_search (pgvector_store :463–468 cast `float4[]`) để MMR tính **cosine thật, không re-embed**; `strip_embedding=True` sau filter.

### A6. `neighbor_expand` — query_graph.py:5725–5784

M2: expand ±N `chunk_index` neighbours cùng document, per-bot opt-in `neighbor_expand_enabled` (default **OFF**, node trả `{}` no-op); knobs `neighbor_window_size` / `neighbor_token_budget` (M22 cap) / `neighbor_max_concurrency`; impl `orchestration/nodes/neighbor_expand.py`.

### A7. `grade` (CRAG-lite) — query_graph.py:5203–5677

- Iteration cap `max_total_graph_iterations` :5209–5217.
- **Smart-skip**: `crag_skip_retry_above_score` — top rerank score đủ cao → bỏ grade-LLM call + retry loop (S1 Pipeline-Opt) :5226–5273.
- Batch structured-output grading (1 LLM call, XML chunks), gated `structured_output_enabled`+`grade_use_structured_output`+`grade_use_batch`; wall-clock cap `grade_timeout_s` (đo: p50 0ms qua skip, p95 2.56s khi invoke) :5285–5330; per-chunk fallback path.
- Adequacy: `_is_retrieval_adequate` :730–741 (`min_relevant_count` AND `min_relevant_fraction`).
- Compound-intent leniency `_remap_grade_for_intent` :744–778 (irrelevant→ambiguous cho comparison/multi_hop/aggregation; G10b Issue 10) + lenient route skip retry :5630+.
- **Score-mode-aware fallback gate** :5576–5615: all-irrelevant + graded rỗng → mode `rerank` so floor tuyệt đối (`crag_min_fallback_score=0.3`, per-intent map `_10:94`, factoid hạ 0.35→0.25 theo LOAD_TEST Q18 Điều 45); mode bypass (RRF ~0.01) → **relative gate** `top × DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO` (fix scale-mismatch refuse oan).
- Retry path: `rewrite_retry` :5786 → edge về `retrieve` :8013, cap `max_grade_retries`.

---

## (b) Git evolution — WHY (commit-hash + reason)

### Reranker provider history (4 swaps)

| Khi | Commit | Sự kiện | Lý do (commit msg / memory record) |
|---|---|---|---|
| 2026-04-28 | `52f8241` | Sprint-12A Reranker Strategy port+registry (LiteLLM, Cohere rerank-v3.5 default) | khởi tạo Strategy pattern. **SỰ THẬT lịch sử**: `COHERE_API_KEY` rỗng → NullReranker **silent** suốt giai đoạn đầu (memory `project_reranker_disabled`) — bài học silent-fallback ban |
| 2026-04-29 | `51b8e41` | JinaReranker code-only, chưa activate | tách ship khỏi flip |
| 2026-05-06 | `c5a6398` | Unblock Jina v3 rerank+embed production (V14) | binding/key fix |
| 2026-05-11 | `57c6b95` | ZeroEntropy strategy (Port+Registry, default OFF) | thêm provider = 1 file, không sửa orchestrator |
| 2026-05-12 | `b9e7761` | **Jina→ZeroEntropy swap** (zembed-1 1280 matryoshka + zerank-2) | legalbot 30Q PASS 65%→90% (+25pp); trade-off cost +54%, p95 +4.7s |
| 2026-05-13 | `2d7be61` | Voyage rerank-2 adapter | thêm hosted multilingual option |
| 2026-06-08 | `ccc9f57` | seed dev jina→ZeroEntropy đồng bộ | nano-drift cleanup |

### Cliff filter vs static gate (threshold-drift saga)

1. `cfb7717` (2026-05-07 16:16) — **adaptive cliff-detect ra đời**, Pattern B từ research `RERANK_THRESHOLD_BEST_PRACTICE_2026`, per-bot opt-in, default vẫn `threshold`.
2. `a339806` (2026-05-07 23:16) — **flip default threshold→cliff** + `min_score` 0.4→0.15: floor 0.4 là "low relevance" boundary của **Cohere**; Jina v3 phân phối khác → 0.4 thành precision trap drop sạch chunk query ngắn; threshold strategy trả `[]` → refuse silent không audit context; cliff `force_min_keep=True` diệt silent-failure surface. (= memory `feedback_threshold_drift_post_migration`: đổi provider PHẢI recalibrate constants gắn distribution cũ.)
3. `03cab24` floor 0.05→0.15, rồi `d834c3b`/`2258581` (S2 Phase-1) **recalibrate ngược 0.15→0.05** sau swap ZeroEntropy — drift lần 2, cùng pattern.
4. `8336f83` (Wave A WA-5, 2026-05-19) min-score 0.15→0.30 + post-rerank refuse gate; `33644d2`/`befd2a4` A/B framework + đo REAL distribution trước khi chỉnh (đúng no-guess).
5. `1c9fca4` (Wave J2, 2026-05-20) — static gate chạy đè cliff = **double-gate** vứt luôn safety chunk → 27% false refuse (load-test 15Q, top_score 0.29–0.43) → skip gate khi strategy=cliff (:5074–5118).
6. `0dff82f` — empty-context safety keep-top-1 trong cliff.
7. `32b3b62` — cliff **bypass multi-fact intents** (`DEFAULT_RERANK_CLIFF_SKIP_INTENTS`): gap-cut giữ đúng 1 cluster điểm cao → bóp chết aggregation/multi_hop cần nhiều chunk.

**Tại sao cliff thay static gate (tổng hợp)**: static floor là hằng số gắn vào distribution của 1 model cụ thể — 2 lần swap reranker đều làm floor sai lệch nghiêm trọng (0.4 Cohere-era drop sạch dưới Jina; 0.15 Jina-era sai dưới ZE). Cliff cắt theo **hình dạng phân phối từng query** (gap tương đối), không theo hằng số tuyệt đối → miễn nhiễm provider swap; cộng `force_min_keep` chuyển refuse từ "empty context silent" sang "sysprompt đọc 1 chunk low-confidence rồi tự refuse" (đúng sacred rule 10 — app không tự inject refuse).

### Retrieval safety-net origin (legal exact-clause miss)

`ccc9f57` (2026-06-08): forensic 2026-06-05 — **zerank-2 dìm chunk legal chứa đáp án exact (BM25 rank #1) xuống rerank rank-8**, vượt `top_n` + cliff → hard miss. Fix: union top-N retrieval-ordered candidates trở lại (bounded, chỉ khi reranker disagree với retrieval), stamp score = min surviving rerank score — raw RRF ~0.01 sẽ bị `crag_min_fallback_score=0.3` loại, defeat chính safety-net. Default ON N=2 (code :5163–5199). Cùng commit gỡ `math_lockdown` answer-override + FAIR-RAG gap-retry inject (sacred rule 10 compliance).

### BM25 Vietnamese-aware

- `4d750d2` (2026-05-27) Triple RAG retrieve fix: (1) **filler-word bug** — `websearch_to_tsquery('simple','Chương 3 nói gì')` AND-of-4-tokens → 0 hits ('Chương 3' → 66 hits); fix `strip_vn_filler_tokens` 19 fillers, sparse-branch only, list override qua system_config; (3) **structural prefilter** — zembed-1 zero-shot không hiểu structural identifier (đo cosine trực tiếp: top-20 không có chunk Chương 3 nào; zerank-2 chấm 0.85 khi được ĐƯA input → bottleneck ở retrieve, không phải rerank — đúng tầng).
- `f6eeb42` — symbol-phrase branch (`range(5)` case th-03).
- `2eac539`/`a2d54aa` — VN segmentation parallel + underthesea race fix (`vi_tokenizer.py:29-76`).

### Determinism episode (2026-06-10 — bài học mới nhất)

- `6547fb6` (00:24): graded 3-run thấy thong-tu **6/8 flip** — corpus điều luật near-identical → tied scores, mọi ORDER BY thiếu secondary key → top-K khác mỗi run. Thêm tie-break vào 5 điểm ordering (dense/sparse ROW_NUMBER + LIMIT + final rrf ORDER BY, cliff sort, MQ RRF merge sort).
- `2f5ed41` (01:20): **REVERT**. A/B 3-run: baseline 87/91 → tie-break **73–75/91 (regress, legal-specific)** → revert về 85/91, HALLU=0. `7dd1f84` verdict: tie-break-by-arbitrary-id phá dense-corpus legal retrieval; **nguồn variance thật = LLM temp-0 upstream (multi-query/gen/judge), không phải SQL ordering**.

### Khác

- Alembic `0085`: dim 2560→1280 matryoshka (pgvector HNSW **2000-dim limit**), HNSW rebuild m=32/ef_construction=200 — lần tuning index duy nhất.
- Alembic `0108` + `_doc_filter_sql`: HNSW pushdown fix (idx_scan 0 → active).
- `fccf817` Phase 4 CRAG relax; `4aed6c1` route 6 cliff/schema keys qua `resolve_bot_limit` (per-bot không redeploy).
- Memory `feedback_resolver_must_fallback_system_config`: rerank_resolver từng chỉ JOIN `bot_model_bindings` (skip system_config fallback) → NullReranker silent, bot `thong-tu-09-2020` 0 chunks; fix `_lookup_platform_default()`; kèm naming drift `'rerank'` vs `'reranker'` purpose (`e256e40`).

---

## (c) Plans — done / doing / not-done (retrieval scope)

| Plan | Status (evidence) |
|---|---|
| `plans/260604-bm25-vietnamese-aware/plan.md` | **DRAFT chưa ship nguyên trạng** ("⏳ DRAFT — waiting user approval" :4). L1 stopword-strip đã có trước từ `4d750d2`; **L2 AND-then-OR fallback + L3 graceful degrade CHƯA ship** — sparse vẫn AND-mode thuần (pgvector_store :412-415). Case chứng minh: Pin Daniel (hoa-02, 0 chunks), Lý Thái Tổ (lsu-04, top_score 0.066), range(5) (th-03 — đã vá riêng bằng symbol-phrase `f6eeb42`) |
| `plans/260605-rag-hardquery-rootcause-fix/plan.md` | **PARTIAL**: Phase 0 throughput 503 ✅ (semaphore+bulkhead, "0×503 verified" trong plan); Phase 2 CRAG scale-mismatch ✅ shipped — `rerank_score_mode` relative gate (`ccc9f57`, :5590-5612); Phase 1 per-intent maps ✅ wired (`retrieve_top_k_by_intent`/`rerank_top_n_by_intent`); Phase 1 tách **`rerank_input_pool` ≠ `rerank_top_n`** ❌ chưa tồn tại; Phase 3 wire `math_lockdown` ❌ **REJECTED đúng** — vi phạm sacred rule 10, `ccc9f57` xoá override |
| `plans/260610-ga-hardening/plan.md` | **DOING**: ISSUE 1 RLS P0 (ngoài scope P1-D nhưng chạm `session_with_tenant` của hybrid_search); retrieval-determinism portion **tried + reverted** (`6547fb6`→`2f5ed41`), root cause re-attributed sang LLM variance |
| `plans/260609-query-graph-split/` | **NOT DONE** — query_graph.py vẫn 8087 dòng, retrieve node ≈1.6k dòng monolith |
| Wave A/CT-4/WE-4 threshold A/B + histogram tooling | DONE (`33644d2`, `befd2a4`, `8336f83`) |
| GraphRAG (`graph_retrieve_node` :7609) | code DONE, default `graph_rag_mode="disabled"` — production OFF |
| `neighbor_expand` M2 | code DONE, default OFF per-bot |
| ViRanker local | **STUB** (registry.py:38-45 raise NotImplementedError); 12-L:63: NDCG@3 0.6815 MMARCO-VI — candidate VN-heavy |
| pg_textsearch / VectorChord-BM25 / ParadeDB (true BM25) | research-only (docs/master/12-L:6-16); P15-1 deferred (memory `project_p15_progress`); production vẫn `ts_rank_cd` = BM25-approx |

---

## (d) vs SOTA IR 2026 — HAS / LACKS (objective)

**HAS**
1. Hybrid dense+sparse trong 1 SQL với weighted-RRF (FULL OUTER JOIN), rrf_k=60 canonical, per-intent weight blend gated (`pgvector_store.py:465-501`, query_graph :3984).
2. Two-stage retrieve→cross-encoder, provider qua Port+Registry+Null (5 hosted + 1 local stub), per-bot resolver + 3-tier fallback (binding → system_config → Null).
3. Adaptive score filter (cliff = distribution-aware gap cut) thay static threshold — survive 2 lần provider swap mà static floor đã fail; per-intent skip cho multi-fact.
4. **Retrieval safety-net** (union top retrieval-order khi reranker disagree, score-stamped) — guard chống reranker under-rank mà ít hệ production có; nguồn gốc forensic thật 2026-06-05.
5. CRAG-lite đầy đủ thành phần paper (evaluator → relevant/ambiguous/irrelevant → rewrite-retry) + batch 1-call + timeout + smart-skip + **score-mode-aware fallback** + compound leniency.
6. MMR diversity với cosine THẬT (embedding propagate từ SQL `float4[]`, không re-embed), per-intent threshold.
7. Vietnamese-aware sparse stack: underthesea compound segmentation symmetric ingest/query, filler strip, diacritic dual-variant, structural LIKE prefilter + graceful degrade, NFC normalize, symbol-phrase code-token.
8. HNSW ops đúng bài: filter pushdown trong CTE (alembic 0108), ef_search per-session clamp, rebuild theo dim m=32/ef_construction=200, matryoshka 1280 dưới 2000-dim limit.
9. Per-step observability: `request_steps` metadata (n_in/n_kept/strategy/gap/top_score/mode), audit events, Prometheus `cliff_drop_total`.
10. Multi-query fanout + RRF merge + relax retry; GraphRAG / neighbor-expand / parent-child sẵn code (gated OFF).

**LACKS**
1. **True BM25**: `ts_rank_cd` thiếu IDF saturation/k1/b/length-norm — pg_textsearch/VectorChord-BM25 đúng công thức + 2.4–6.5× ES (12-L:12-16); P15-1 deferred.
2. **Sparse AND-mode brittleness**: websearch AND-of-N là default duy nhất; L2 OR/quorum fallback (plan 260604) chưa ship — recall sparse lệ thuộc filler-list thủ công.
3. **Reranker calibration tự động**: 2 lần threshold-drift đều fix tay bằng constant; chưa có per-bot score-histogram → percentile floor dù WE-4 đã có tooling đo.
4. **`rerank_input_pool` chưa tách khỏi `rerank_top_n`** (two-stage chuẩn: reranker thấy 30–50, trả 5–10) — plan 260605 Phase 1 dang dở.
5. **HNSW tuning-by-corpus-size**: m/ef_construction/ef_search cố định toàn cục mọi tenant; chưa có policy theo row-count hoặc recall@K đo vs exact scan (**GIẢ THUYẾT**: corpus hiện nhỏ nên chưa đau).
6. **RRF k chưa tune theo evidence**: k=60 mặc định ở 3 key khác nhau (`rag_rrf_k`/`lexical_rrf_k`/`rrf_k`) — chưa sweep, chưa thống nhất.
7. **Reranker model currency process**: zerank-2 từ 2026-05-12; không có benchmark định kỳ vs ViRanker (VN NDCG 0.6815)/Voyage/BGE trên corpus thật từng bot.
8. **Determinism trên dense corpora**: tied-score flip là real (6/8 thong-tu); revert đúng nhưng vấn đề gốc còn — tie-break **content-aware** (chunk_index/BM25-rank secondary key thay arbitrary id) chưa thử.
9. **Late-interaction / multi-vector (ColBERT-style) absent** — single-vector zembed-1; lưu ý 12-L:175 RAG-Fusion FAILS post-rerank (-3% Hit@10) = evidence ngược cần cân nhắc khi thêm fusion layer mới.
10. **Per-stage recall attribution**: Coverage chỉ đo end-to-end (GRADED 85–87/91); không có `gold_chunk_in_set` per-stage (retrieve/rerank/cliff/grade) trong request_steps → mỗi miss phải forensic tay.

---

## (e) 10 open questions cho Phase 2

1. **True-BM25 engine swap** (pg_textsearch / VectorChord) qua LexicalRetrievalPort có lift coverage trên các case chứng minh (Pin Daniel / Lý Thái Tổ) không — A/B trên 91Q graded trước khi viết ADR?
2. Sparse **AND→OR/quorum fallback** (plan 260604 L2) — ship hay drop? Risk đã ghi "OR fallback bring back noise → 4 perfect bot tụt"; gate per-bot hay per-query-token-count?
3. **Reranker auto-calibration**: per-bot score-histogram → percentile floor thay constant tay — drift lần 3 (khi swap model tiếp) ai chặn? WE-4 histogram data có đủ làm baseline chưa?
4. **Safety-net N=2 đủ chưa**: zerank-2 dìm tới rank-8 — N=2 không cover nếu 2 slot đầu trùng kept-set. Tỉ lệ `rerank_retrieval_safety_net.added>0` thực tế trong request_steps là bao nhiêu?
5. **Tie-break content-aware** (secondary key = chunk_index / BM25 rank, không phải arbitrary id) có giữ 87/91 mà vẫn diệt flip không — hay chấp nhận variance vì nguồn thật là LLM temp-0?
6. Tách **`rerank_input_pool` vs `rerank_top_n`**: per-intent retrieve_top_k=40 (aggregation) có thực sự tới reranker không, hay bị slice sớm ở fuse points? Cần trace 1 request aggregation thật.
7. **RRF k sweep** (k ∈ {20, 60, 120}) trên graded set + quyết định gộp/giữ 3 config key RRF riêng biệt?
8. **Per-stage recall instrumentation**: thêm `gold_chunk_in_set` flag tại retrieve/rerank/cliff/grade vào request_steps (eval mode only) để attribution miss đúng tầng — chi phí schema/perf chấp nhận được không?
9. **GraphRAG vs neighbor_expand vs parent-child** — cả 3 gated OFF; bật cái nào trước cho corpus legal (Điều N cross-reference), evidence gate nào justify (coverage lift vs token cost)?
10. **HNSW policy theo corpus size**: ngưỡng row-count nào trigger ef_search > 64 / index params per-tenant? Cần đo recall@K HNSW-vs-exact trên corpus lớn nhất hiện có trước khi đặt policy.

---

*P1-D · Phase 1 read-only · 2026-06-10 · evidence-first, không đoán.*
