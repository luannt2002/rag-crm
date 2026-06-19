# RAG RESEARCH MASTER — Knowledge Base for Ragbot Expert Build (2026-06-18)

> 1 file gom TẤT CẢ research từ ~30 deep-research agent (web + arXiv 2024–2026), đã adversarial-verify
> các con số chính. Mục đích: kho tri thức để nâng Ragbot thành Expert RAG đạt 5 tiêu chí
> (Nhanh · Đúng/Faithfulness=100% · UX · Performance · Cost) trên nền multi-tenant + VI/EN.
>
> Cách dùng: mỗi mục có **Finding + số đo + nguồn URL + "ÁP cho mình"** (EVOLVE/NEW, map vào bug đã đo).
> Nhãn: ✅ verified (≥2 nguồn) · ⚠️ paper-only/1 nguồn · 🔴 = đánh thẳng bug của mình.
>
> Bug nền của mình (đã đo, xem reports/PROJECT_ALL_FLOWS_20260618.md §0): **BUG-1 CONFLATE giá**
> (faithfulness ❌) · **p95 ~15s** (nhanh ❌) · **grounding warn-only** (không enforce) · **RLS bypass runtime**
> · **routing regex VN hardcode** · **chunking dead-wire**.

---

## ⭐ TOÀN BỘ RESEARCH — 2 PHẦN (cập nhật 2026-06-18, ~80 agent tổng)

Research được chia 2 file để dễ đọc; **đây là PART 1**, đọc kèm **PART 2**:

- **PART 1 (file này)** — 7 trục nền: structured/self-query routing · faithfulness/atomic-NLI · chunking (Contextual-Retrieval/Late/Docling) · eval 4-trụ · latency/cost (Adaptive-RAG/cache/cascade) · Vietnamese (ViRanker/word-seg) · multi-tenant (Silo/Pool/Bridge, RLS) · + đối chiếu AdapChunk Ekimetrics. ~30 agent.
- **PART 2** → [RAG_RESEARCH_MASTER_PART2_20260618.md](RAG_RESEARCH_MASTER_PART2_20260618.md) — 9 trục sâu thêm (~50 agent): **A** GraphRAG/HippoRAG2/GFM-RAG/LightRAG/LazyGraphRAG (verdict: KHÔNG full-GraphRAG, typed-edge cho conflate) · **B** RAPTOR/parent-child · **C** embeddings (Qwen3-0.6B same-dim, halfvec, MRL, VN-MTEB) · **D** pgvector scaling (ef_construction=128/ef_search=160, iterative_scan, halfvec, per-tenant index) · **E** security (indirect-injection qua chunk, PoisonedRAG, PromptGuard-2 — guardrail mình MÙ chunk-injection) · **F** reranking (jina-v3 BEIR 61.94, cost-gate) · **G** hybrid-tuning (rrf_k=10>60, DBSF, dynamic-alpha) · **H** 🔴 table-RAG (STC MRR+66%, narrativize+metadata-filter numeric-acc 99.7% — đánh thẳng conflate) · **I** 🔴 eval-CI (RAGAS dual-gate, 4-quadrant silent-refusal/ARSP — thoát "test lòi bug fix bừa") · **J** adversarial-caveats (bác nhiều claim vendor).

**TOP adoption HỢP NHẤT (P1 cả 2 part):** 1) routing price-of-entity→stats (Phase A) + table STC per-row · 2) atomic-claim NLI + numeric-verify (faithfulness enforce) · 3) eval-CI dual-gate + ARSP · 4) pgvector tune + halfvec (p95) · 5) retrieval-layer injection scanner (security P0) · 6) cascade/async-grounding/MQ-gate (p95/cost, config-flip đã build) · 7) ViRanker/Qwen3 swap (VN). **GraphRAG: KHÔNG dùng full** (cost 350×, win-rate thổi phồng) — chỉ typed-edge nhẹ hoặc HippoRAG2 nếu cần multi-hop legal về sau.

---

## 0. TÓM TẮT — TOP 12 ĐÒN BẨY (ranked theo ROI cho bug của mình)

| # | Kỹ thuật | Đánh bug | Số đo (nguồn) | EVOLVE/NEW |
|---|---|---|---|---|
| 1 | **Structured-first / Self-Query routing** (price-of-entity → SQL) | 🔴 CONFLATE | Metadata-filter +17–31% F1; routing classifier 93.2% acc, −28% token | EVOLVE (đã có stats route) |
| 2 | **Atomic-claim + NLI verification** (chặn, không warn) | 🔴 Faithfulness | RT4CHART F1 0.776; HalluGuard-4B 84% BAcc = GPT-4o; CLATTER attribution 0.70→0.975 | NEW node |
| 3 | **Numeric/arithmetic re-verification** | 🔴 CONFLATE/Extrapolate | FinGround bắt 43% lỗi tính mà detector thường miss; PCN "proof-carrying numbers" | NEW |
| 4 | **Adaptive-RAG complexity gate** (skip/single/multi) | 🔴 p95 15s | −54% avg steps @ −4% F1; TARG −70–90% retrieval, +0.012s | EVOLVE (gate có sẵn OFF) |
| 5 | **Semantic cache xấp xỉ (LSH τ≈0.93)** | p95, Cost | Proximity −59–75% latency, 77% ít DB call, hit 93–98% | EVOLVE (cache 2-tier có sẵn) |
| 6 | **Cascade/speculative model routing** | Cost | Speculative-RAG −44–51% latency; cascade −40–60% cost | EVOLVE (cascade built, OFF) |
| 7 | **Contextual Retrieval (Anthropic)** | Coverage | −67% top-20 retrieval failure (w/ rerank), $1.02/1M tok | EVOLVE (CR có, OFF) |
| 8 | **ViRanker** (reranker VN chuyên) | 🔴 VN recall | NDCG@3 0.6815 MMARCO-VI, beat BGE/PhoRanker | NEW (swap qua registry) |
| 9 | **Late Chunking thật** (token-pool) + **Docling table** | Coverage, CONFLATE | Late +1.5–6.5% nDCG; structure-aware table khóa value↔row | EVOLVE/REWRITE parser |
| 10 | **RAGAS/RAGChecker eval CI** (Context P/R + Faithfulness + Coverage) | 🔴 "test lòi bug" | RAGAS 5M evals/mo; synthetic test-set tự sinh | NEW (CI gate) |
| 11 | **RLS filter-in-query + per-tenant cache namespace** | Performance/isolation | post-filter = leak qua ANN side-channel; namespace routing −92% search-space | EVOLVE (set DATABASE_URL_APP) |
| 12 | **Anthropic Citations API** (char-level cite) | 🔴 Faithfulness | +15% recall; Endex hallucination 10%→0% | NEW (provider feature) |

**Luận điểm trung tâm của research:** *"Retrieval quality là predictor đáng tin cậy nhất của hallucination"*
(Weaviate, HaluEval, FaithDial). Fix **tầng retrieval/routing TRƯỚC** (đúng bài học spa-07 của mình),
KHÔNG vá sysprompt. Và **không tồn tại 1 chiến lược đa năng** — phải route theo query-corpus compatibility.

---

## 1. STRUCTURED / AGENTIC ROUTING (đánh BUG-1 CONFLATE)

**Self-Query Retriever (LangChain/LlamaIndex)** — LLM tách query → (semantic string + structured metadata filter), filter chạy TRƯỚC vector. LangChain `SelfQueryRetriever` + `WeaviateTranslator`/`PGVectorTranslator`; LlamaIndex `VectorIndexAutoRetriever` + `VectorStoreInfo`. Comparators: eq/ne/gt/gte/lt/lte/in/nin/contain. ⚠️ Cảnh báo: cần GPT-4-class cho filter chính xác; +200–800ms/lookup → cần cache.
- Nguồn: https://js.langchain.com/docs/how_to/self_query/ · https://developers.llamaindex.ai/python/framework/module_guides/querying/router/
- **ÁP**: 🔴 EVOLVE — thêm `query_type`+`entity_name` vào `UnderstandOutput` schema → price-of-entity route `query_by_name_keyword` (đã verify cơ chế có sẵn). Đúng Phase A plan.

**Routing benchmarks** — ✅ RAGRouter-Bench (7,727 query): TF-IDF+SVM **93.2% acc, macro-F1 0.928, −28.1% token** — *lexical surface beat semantic embeddings 3.1 F1* cho phân loại query-type. Adaptive-RAG 3-tier (no-retrieval 8.6% / single 53.3% / multi 38.1%).
- Nguồn: https://arxiv.org/abs/2604.03455 · https://arxiv.org/abs/2403.14403 · https://arxiv.org/abs/2602.00296
- **ÁP**: EVOLVE — classifier nhẹ (TF-IDF+SVM) trước pipeline để gate độ sâu retrieve = cắt p95.

**Metadata-driven routing** — ✅ Multi-Meta-RAG: Hits@4 0.6625→0.792 (+17.2%); Metadata-embedding-prefix: Context@5 33%→63% (+30pp), retrieval-fail 10–15%→3–6%; Financial metadata-filter: F1 32.9→44.4 (+35%), context-precision 20%→44%, hallucination 18.5%→12.2%.
- Nguồn: https://arxiv.org/abs/2406.13213 · https://arxiv.org/html/2601.11863v1 · https://arxiv.org/abs/2510.24402
- **ÁP**: 🔴 EVOLVE — prepend metadata (entity_name/category) vào chunk trước embed = giảm conflate by-construction.

**A-RAG (tool-based)** — LLM tự chọn keyword_search/semantic_search/chunk_read; +7.5–39.5pp vs Naive, −49% token. **DoTA-RAG** namespace routing: −92% search-space, 5.3× latency, correctness 0.752→0.929.
- Nguồn: https://arxiv.org/abs/2602.03442 · https://arxiv.org/abs/2506.12571
- **ÁP**: NEW (về sau) — agentic retrieval cho multi-hop; namespace = per-bot/per-workspace scoping (khớp multi-tenant).

**Text-to-SQL cho catalog** (nếu sau này cần NL→SQL thật): BIRD SOTA ~75–82% (XiYan-SQL 75.63%, AskData 81.95%); schema-linking AutoLink recall 97.4%; few-shot ICL OpenSearch-SQL 72.28%. ⚠️ BIRD có ~32% annotation error (VLDB 2026) — số cao có thể fit artifact.
- Nguồn: https://arxiv.org/abs/2507.04701 · https://arxiv.org/abs/2511.17190 · https://bird-bench.github.io/
- **ÁP**: ⚠️ chưa cần — stats-index SQL deterministic của mình đã đủ cho catalog price; text-to-SQL là over-engineering ở giai đoạn này.

---

## 2. FAITHFULNESS / HALLUCINATION ENFORCEMENT (đánh grounding warn-only)

**Vấn đề nền** — ✅ *Correctness ≠ Faithfulness*: tới **57% citation là post-rationalization** (model bịa rồi gắn cite). RAGTruth: **43–44% RAG response có hallucination**; Numeric/Logic = 25.42% spans (hạng 2). GaRAGe: best model attribution F1 chỉ **58.9%**. → citation ≠ grounded; phải verify đa chiều.
- Nguồn: https://arxiv.org/abs/2412.18004 · https://arxiv.org/abs/2401.00396 · https://aclanthology.org/2025.findings-acl.875/

**Decompose-then-verify (atomic claim + NLI)** — pattern hội tụ của SOTA:
- ✅ **RT4CHART**: claim → local→global NLI (entailed/contradicted/baseless) → F1 **0.776** RAGTruth++ (+83% baseline); re-annotation tìm **1.68× nhiều hallucination** hơn label gốc.
- ✅ **CLATTER**: decompose+attribution+aggregate; attribution acc 0.70→**0.975**; *decomposition đơn lẻ chỉ +0.12, attribution mới +2.29* → phải tìm evidence, không chỉ tách câu.
- ✅ **HALT-RAG**: NLI ensemble (roberta-mnli + deberta-v3) + abstention; QA F1 **0.9786**, Summ 0.776.
- ✅ **HalluGuard-4B**: 84.0% BAcc RAGTruth = MiniCheck-7B, ngang GPT-4o (75.9%) ở nửa tham số.
- ⚠️ **Semantic Illusion**: embedding-based detector **100% FPR trên hallucination THẬT** (RLHF) dù 0% trên synthetic; NLI AUC 0.81; chỉ LLM-judge (GPT-4o-mini) đạt 7% FPR — *đừng dùng embedding-similarity để bắt hallucination*.
- Nguồn: https://arxiv.org/abs/2603.27752 · https://arxiv.org/abs/2506.05243 · https://arxiv.org/abs/2509.07475 · https://arxiv.org/abs/2510.00880 · https://arxiv.org/abs/2512.15068
- **ÁP**: 🔴 NEW node — thay grounding warn-only bằng **atomic-claim NLI**: số/giá là claim riêng → NLI vs chunk_used → contradicted ⇒ BLOCK + OOS. Đây là nâng cấp "warn→enforce" chính xác.

**Numeric-specific** — ✅ FinGround: detector thường **miss 43% lỗi tính**; FinGround-8B F1 91.4%, hallucination 4.1% (−78% vs GPT-4o+CoT). FAITH: multivariate-calc gần **0%** ở hầu hết model. RAGShield: numeric manipulation gây nhiễu embedding **1,459× nhỏ hơn** text → embedding mù với poisoning số. ⚠️ Acurai "100% elimination RAGTruth" — chỉ đúng corpus sạch 3-passage, GPT-only, không generalize.
- Nguồn: https://arxiv.org/abs/2604.23588 · https://arxiv.org/abs/2508.05201 · https://arxiv.org/abs/2604.00387 · https://arxiv.org/abs/2509.06902
- **ÁP**: 🔴 NEW — với factoid-giá: extract số trong answer → map về đúng chunk_id (không chỉ "có trong corpus") → arithmetic verify cho tổng/đếm. Đánh thẳng CONFLATE + Extrapolate.

**Trust-Align / Learning-to-Refuse** — ✅ ICLR 2025 Oral: 26/27 model cải thiện; LLaMA-3-8b +12.56 ASQA, +36.04 QAMPARI Trust-Score. **Anthropic Citations API**: +15% recall, Endex 10%→0% hallucination, char-level cite (số không có trong source ⇒ không cite được).
- Nguồn: https://arxiv.org/abs/2409.11242 · https://claude.com/blog/introducing-citations-api
- **ÁP**: NEW — bot dùng Anthropic → bật Citations API cho factoid; "learning to refuse" khi evidence không support (khớp HALLU=0 + refusal-trap của mình).

---

## 3. CHUNKING / RETRIEVAL ADVANCES (đánh chunking dead-wire + Coverage)

**Contextual Retrieval (Anthropic, 2024)** — ✅ prepend 50–100 tok context (Claude-gen) vào mỗi chunk trước embed + BM25. Top-20 retrieval failure: 5.7%→3.7% (CE) →2.9% (+CBM25) →**1.9% (−67%, +rerank)**. Cost $1.02/1M tok (prompt-cache).
- Nguồn: https://www.anthropic.com/news/contextual-retrieval
- **ÁP**: EVOLVE — CR có trong code (OFF). Bật cho bot có bảng/giá rời ngữ cảnh; gate per-bot qua plan_limits (cost).

**Late Chunking (Jina, ICLR 2025)** — ✅ embed cả document TRƯỚC rồi mới cắt+mean-pool → chunk mang ngữ cảnh toàn cục. +1.5–3.6% nDCG (sentence-boundary), NFCorpus +6.5%. Rẻ hơn CR (không cần LLM). Cần embedder long-context.
- Nguồn: https://arxiv.org/abs/2409.04701
- **ÁP**: EVOLVE — late-chunking của mình hiện chỉ là prefix-200-char (xấp xỉ). Nâng lên token-pool thật nếu embedder hỗ trợ; bật sliding cho doc dài.

**Layout-aware / table (Docling, Unstructured)** — ✅ Docling HierarchicalChunker + HybridChunker giữ table/heading/reading-order (42k★, 1.5M/mo). Structure-aware table chunk khóa value↔(row+header) → chống conflate giá. ParseBench: best parser ~90% faithfulness (1/10 trang vẫn miss); LlamaParse Agentic 84.9% overall, ~1.2¢/trang; chart-extract chỉ 4/14 method >50%.
- Nguồn: https://arxiv.org/abs/2501.17887 · https://www.ijournalse.org/index.php/ESJ/article/view/3380 · https://www.llamaindex.ai/blog/parsebench
- **ÁP**: 🔴 EVOLVE/REWRITE parser-adapter — wire `ctx.blocks`→`smart_chunk_atomic` (1 dòng), bật per-table description; table per-row khóa header (đúng DEEPDIVE §3).

**Reranking 2025** — ✅ jina-reranker-v3 (0.6B, 100+ lang, BEIR 61.94); survey: RLT −15% noise, ToolRerank +12% recall. ⚠️ RAGPerf: rerank chiếm **28–87% latency** pipeline multimodal — profile trước khi thêm.
- Nguồn: https://arxiv.org/abs/2509.25085 · https://arxiv.org/abs/2512.16236
- **ÁP**: EVOLVE — rerank của mình có CLIFF filter; cân nhắc cost-gate rerank khi top-score đã cao.

---

## 4. RAG EVALUATION (đánh "cứ test là lòi bug, fix bừa")

**Đây là lời giải cho frustration của bạn**: thay whack-a-mole bằng **eval CI có gate**.

**RAGAS** — ✅ reference-free, 4 metric: Faithfulness (=entailed_claims/total), Answer-Relevancy, **Context-Precision** (chunk liên quan xếp đầu?), **Context-Recall** (đủ chunk cần?). `TestsetGenerator` tự sinh QA từ corpus (giải cold-start). 5M evals/mo (AWS/MS/Databricks). *Faithfulness 1.0 + Context-Recall thấp = bot refuse mà vẫn "faithful"* → phải đo cả 2 (khớp Coverage của CLAUDE.md).
- Nguồn: https://arxiv.org/abs/2309.15217 · https://docs.ragas.io
**ARES** — ✅ Context-Relevance +59.9pp acc vs RAGAS, chỉ cần 150–300 annotation. **TruLens** — RAG Triad (Context-Relevance/Groundedness/Answer-Relevance) + OTel tracing; groundedness F1 80.82%. **RAGChecker** — tách retriever-recall vs generator-faithfulness → biết conflate là retrieval-miss hay generation-fabrication.
- Nguồn: https://arxiv.org/abs/2311.09476 · https://www.trulens.org · https://arxiv.org/abs/2408.08067
**Benchmark thực tế** — ✅ FaithJudge leaderboard: Gemini-2.5-Pro 6.65% → Llama-3.1-8B 28.38% hallucination. Production gap: MS MARCO sạch 95% → enterprise corpus 78%. RAGPerf: generation = 75–91% latency (DB choice marginal).
- Nguồn: https://arxiv.org/abs/2505.04847 · https://arxiv.org/html/2603.10765v1
- **ÁP**: 🔴 NEW (ưu tiên cao) — build eval harness: RAGAS synthetic test-set từ corpus thật + gate CI {Context-Recall≥0.8, Faithfulness≥0.9, Coverage≥0.95, conflate=0}. Đây là cách "đo có hệ thống" thay vì fix bừa. Tận dụng `golden_set/` + `scripts/verify_fixes_loadtest.py` đã có.

---

## 5. LATENCY / COST (đánh p95 ~15s)

**Adaptive retrieval gating** — ✅ **TARG** (training-free): margin của 20-token draft → gate retrieve; −70–90% retrieval, +0.012s, Always-RAG còn *hại* accuracy (off-topic). **Self-Routing RAG**: −26% retrieval +8.5% acc. **Adaptive-RAG**: −54% avg-steps @ −4% F1.
- Nguồn: https://arxiv.org/abs/2511.09803 · https://arxiv.org/abs/2504.01018 · https://arxiv.org/abs/2403.14403
**Semantic caching xấp xỉ** — ✅ **Proximity** (LSH τ≈0.93): −59–75% latency, −77% DB call, hit 93–98%, lookup 4.8µs. **CAR** (cluster adaptive-k): −60% token, −22% latency, −10% hallucination, <50ms. **CacheRAG**: cache cả retrieval-plan, +13.2% acc.
- Nguồn: https://arxiv.org/abs/2503.05530 · https://arxiv.org/abs/2511.14769 · https://arxiv.org/html/2604.26176v1
**Speculative / cascade** — ✅ **Speculative-RAG**: draft nhỏ song song + verify lớn → −44–51% latency (PubHealth), median −15–25%, +12.97% acc. **Cascade routing**: rẻ-trước escalate; ⚠️ SLO cost-heavy → "refusal collapse" (95.5% refuse) — cần ràng buộc abstention.
- Nguồn: https://arxiv.org/abs/2407.08223 · https://arxiv.org/abs/2410.10347 · https://arxiv.org/abs/2601.00841
**CRAG / Self-RAG** — ✅ CRAG +0.15s overhead, PubHealth 39%→75.6%; Self-RAG reflection token (IsRel/IsSup/IsUse) chọn output grounded; Self-CRAG Biography FactScore 86.2.
- Nguồn: https://arxiv.org/abs/2401.15884 · https://arxiv.org/abs/2310.11511
- **ÁP**: 🔴 EVOLVE (config-flip, đã build): bật MQ-complexity-gate + async-grounding + reflect-skip + cascade per-bot + semantic-cache τ=0.93 factoid. Đo p50/p95 trước/sau — KHÔNG claim % trước khi đo.

---

## 6. VIETNAMESE / MULTILINGUAL (đánh VN recall + i18n target)

**Embedder/Reranker VN** — ✅ **ViRanker** (BGE-M3 + Blockwise, `namdp-ptit/ViRanker`): NDCG@3 **0.6815** MMARCO-VI, beat PhoRanker/BGE, 8GB train. **VN-MTEB** (41 dataset, 18 model): **RoPE > Absolute-PE** cho VN. **VCS/ViIR**: bi-encoder VN top khi k=10–20; SentencePiece retrain để bỏ phụ thuộc word-seg ngoài. **BGE-M3**: dense+sparse+ColBERT 1 model, 100+ lang, 72% retrieval-acc.
- Nguồn: https://arxiv.org/abs/2509.09131 · https://arxiv.org/abs/2507.21500 · https://arxiv.org/abs/2503.07470 · https://github.com/flagopen/flagembedding
**Cross-lingual** — ✅ **retrieval là bottleneck** (không phải generation) khi query-lang ≠ doc-lang. **CrossRAG** (dịch doc→1 lang trước gen): low-resource +6.6–8.4%, high +3.6–4.4%. **XRAG**: 2 lỗi mới — sai response-language + cross-lingual reasoning. Fix tầng retrieval: query-translation hoặc balanced bilingual retrieval.
- Nguồn: https://arxiv.org/abs/2504.03616 · https://arxiv.org/abs/2505.10089 · https://arxiv.org/abs/2507.07543
- **ÁP**: 🔴 NEW — swap reranker sang **ViRanker** qua registry (1 config) cho bot VN; đây là fix targeted nhất cho VN-recall (đúng bài học cross-lingual mismatch của mình). Word-seg-before-BPE mình đã có (mở rộng feed `content_segmented` vào dense embed). Cho mục tiêu VI/EN: i18n đã DB-driven (language_packs) — thêm lang = INSERT SQL cho prompt, NHƯNG superlative/tokenizer gate đang hardcode tuple `("vi","en")` → cần sửa code (xem §multi-tenant).

---

## 7. MULTI-TENANT RAG (đánh RLS bypass + target tenant→workspace→bot)

**Taxonomy Silo/Pool/Bridge** — ✅ Silo (full stack/tenant, max isolation/cost) · Pool (shared + metadata filter, rẻ, leak nếu quên filter) · Bridge (index riêng + infra chung, ~100 tenant). pgvector 4 mức: table/schema/logical-DB/DB-service (schema-level khuyến nghị). Milvus: DB-level ~1k / collection ~10k / partition-key ~10M tenant.
- Nguồn: https://www.tigerdata.com/blog/building-multi-tenant-rag-applications-with-postgresql · https://milvus.io/blog/build-multi-tenancy-rag-with-milvus-best-practices-part-one.md
**RLS đúng cách** — 🔴 ✅ **post-filter là anti-pattern nguy hiểm**: chunk chưa-phép vẫn ảnh hưởng ANN ranking qua side-channel dù bị lọc khỏi output. Filter PHẢI nằm TRONG ANN query (`WHERE tenant_id=$1 ORDER BY emb <=> $2`). Tenant-scoped search = exact-NN, nhanh "nhiều bậc". **HoneyBee**: partition theo role, 6× nhanh hơn RLS post-filter, 1.4× storage.
- Nguồn: https://photokheecher.medium.com/secure-rag-authorisation-aware-retrieval-and-row-level-security-c6542500ec21 · https://arxiv.org/html/2505.01538v1
**Attack surface 2025** — ⚠️ 95% leak cross-tenant qua entity-overlap trong corpus chung (blaxel); KV-cache timing side-channel; semantic-cache reuse cross-tenant (key PHẢI có record_tenant_id); OTel trace leak. OWASP LLM08:2025: 5 doc độc → >90% manipulate corpus triệu doc.
- Nguồn: https://blaxel.ai/blog/multi-tenant-isolation-ai-agents · https://advent-of-ai-security.com/doors/08
- **ÁP**: 🔴 EVOLVE (ops, không sửa code) — set `DATABASE_URL_APP=ragbot_app` + gỡ `RAGBOT_ALLOW_SUPERUSER_RUNTIME` → bật RLS runtime (policy 0069/0187 + role 0186 đã có). Verify pgvector query đã embed `record_tenant_id` IN-query (đã đúng theo agent §3). Cache namespace per-tenant đã đúng.

---

## 8. ĐỐI CHIẾU AdapChunk (Ekimetrics) — mình control tới đâu, yếu gì

**AdapChunk thật** (https://github.com/ekimetrics/adaptive-chunking): parse(Docling/PyMuPDF) → multi-method chunk song song (recursive-600/1100, page, LLM-regex) → 5 intrinsic metric **SC/ICC/DCC/BI/RC** → chọn best per-doc (rule-based, mean-score). Benchmark 33 doc/1.18M tok: Retrieval-Completeness 67.7% vs LangChain 58.1%; Answer-Correctness 78% vs 70.1%; mean intrinsic 91.07. (RC = coreference không vỡ qua boundary; BI = giữ block table/list nguyên).

**Mình control tới đâu** (verified `analyze.py`): có `select_strategy` weighted-rule + L5 cross-check ON; **Ekimetrics 5-metric selector implement sẵn nhưng `ekimetrics_enabled=False`**; L3 doc-profile OFF. → khung AdapChunk có, **chưa bật metric-based selection**.
**Yếu nhất**: (a) block-pipeline dead-wire (`parsed_blocks=[]` hardcode) → `smart_chunk_atomic` never called; (b) narrate-then-embed OFF → table embed CSV thô; (c) late-chunking chỉ xấp xỉ prefix; (d) `table_dual_index` group-chunk gây CONFLATE; (e) RC/coreference metric không chạy hot-path.
- **ÁP**: bật `ekimetrics_5metric_selector` (config) + wire blocks + per-table-narrate (đúng §3). Đo strategy-distribution + answer-correctness trên golden_set.

---

## 9. DANH SÁCH NGUỒN ĐẦY ĐỦ (theo chủ đề)

**Routing/Self-Query/Text-to-SQL**: 2604.03455 · 2602.00296 · 2403.14403 · 2504.01018 · 2602.03442 · 2506.12571 · 2406.13213 · 2601.11863 · 2510.24402 · 2505.23052 · 2604.22849 · 2510.02388 · 2604.14222 · 2606.11350 · 2604.19777 · 2507.04701 · 2511.17190 · 2510.14296 · 2502.11438 · 2502.14913 · 2511.10192 · bird-bench.github.io · LangChain/LlamaIndex self-query docs · weaviate.io/blog/query-agent-generally-available

**Faithfulness/Hallucination**: 2412.18004 · 2401.00396 · 2603.27752 · 2506.05243 · 2509.07475 · 2510.00880 · 2512.15068 · 2601.06519 · 2505.21072 · 2512.20182 · 2505.04847 · 2409.11242 · 2510.17853 · 2510.11394 · 2509.21557 · 2506.16988 · 2601.03669 · 2512.08892 · 2502.17125 · 2408.08067 · 2604.23588 · 2602.05723 · 2508.05201 · 2604.00387 · 2509.06902 · 2406.09155 · 2504.17550 · claude.com/blog/introducing-citations-api · GaRAGe(ACL2025) · deepmind FACTS Grounding

**Chunking/Retrieval/Eval**: anthropic.com/news/contextual-retrieval · 2409.04701 · 2501.17887 · ijournalse 3380 · 2509.25085 · 2512.16236 · 2309.15217 · 2311.09476 · 2408.08067 · 2603.10765 · 2506.20128 · trulens.org · 2502.15854 · 2601.15487 · llamaindex.ai/blog/parsebench · weaviate.io/blog/search-mode-benchmarking

**Latency/Cost**: 2511.09803 · 2407.08223 · 2503.05530 · 2511.14769 · 2604.26176 · 2410.10347 · 2601.00841 · 2401.15884 · 2310.11511

**Vietnamese/Multilingual**: 2509.09131 · 2507.21500 · 2503.07470 · 2504.03616 · 2505.10089 · 2507.07543 · github.com/flagopen/flagembedding · github.com/VinAIResearch/PhoBERT

**Multi-tenant**: 2505.01538 · tigerdata.com multi-tenant-rag · milvus.io multi-tenancy · photokheecher RLS · blaxel.ai isolation · aws bedrock multi-tenant-rag · learn.microsoft secure-multitenant-rag · neondatabase/db-per-tenant

**AdapChunk**: github.com/ekimetrics/adaptive-chunking

---

## 10. CAVEAT (rule #0)
- Mọi % là của benchmark trong paper, **KHÔNG transfer** sang corpus mình tới khi load-test. Đa số là directional.
- Adversarial-verify đã giết vài claim: "LlamaIndex 40% faster/35% acc" (REFUTED, no methodology); "Acurai 100%" (corpus-specific, GPT-only); Weaviate Hybrid-2.0 numbers (third-party unverified).
- Bug nền của mình đã đo thật (PROJECT_ALL_FLOWS §0); kỹ thuật ở đây là **ứng viên fix**, phải qua /plan + load-test gate trước khi tin.
- DB local hiện CHƯA dựng được (migration chain vỡ — `bot_model_bindings.tenant_id`), nên chưa load-test trên data thật được.

---
*Sinh từ ~30 deep-research agent (web+arXiv 2024–2026) phiên 2026-06-18. Sibling: reports/PROJECT_UNDERSTANDING_EXPERT_RAG_20260618.md · reports/CHUNKING_RESEARCH_VS_CODE_20260618.md · plans/260618-phaseA-bug1-conflate/plan.md*
