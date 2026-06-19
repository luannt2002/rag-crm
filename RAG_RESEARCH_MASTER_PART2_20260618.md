# RAG RESEARCH MASTER — PART 2 (2026-06-18)

> Tiếp nối [RAG_RESEARCH_MASTER_20260618.md]. Gom ~50 deep-research agent (GraphRAG/RAPTOR/embedding/
> pgvector-scaling/security/reranking/table-RAG/eval-CI) + **adversarial-verify** (đã bác nhiều claim vendor).
> Nhãn: ✅ verified ≥2 nguồn · ⚠️ 1 nguồn/vendor · 🔴 map thẳng bug mình (conflate/p95/faithfulness/multi-tenant).
> Caveat #0: mọi % là benchmark của họ, KHÔNG transfer tới khi load-test trên corpus mình.

---

## A. GraphRAG / Knowledge-Graph RAG — verdict cho mình

**Sự thật cốt lõi (✅ nhiều paper):** GraphRAG **thắng multi-hop + global-sensemaking**, **THUA simple factoid**.
- RAG-vs-GraphRAG (2502.11371): single-hop NQ **flat RAG F1 64.78 > Community-GraphRAG 63.01**; multi-hop MultiHop-RAG +5.4pp, temporal +23pp. Construction **41× chậm hơn**.
- When-to-use-Graphs (2506.05690): fact-retrieval **RAG 60.92% > MS-GraphRAG 49.29%**; complex-reasoning HippoRAG2 53.38% > RAG 42.93%. Token/query: vanilla ~900 vs **MS-GraphRAG ~331k (350×)**.
- 🚨 **Win-rate LLM-judge bị thổi phồng** (2506.06331): LightRAG "66.7% vs NaiveRAG" → sau khử position/length/trial-bias còn **39%** (NaiveRAG thắng). Đừng tin win-rate GraphRAG/LightRAG chưa khử bias.

**Biến thể & chi phí (✅):** MS-GraphRAG index $33k (2024 GPT-4) → ~$33 (2025 gpt-4o-mini, do giá model giảm, KHÔNG do kiến trúc). **LazyGraphRAG**: index = 0.1% GraphRAG, query 700× rẻ (chỉ global). **HippoRAG2** (PPR trên KG, ~0.25s/query, 9.2M index-tok) + **GFM-RAG** (GNN 8M-param, 0.107s/query, best recall) + **EcphoryRAG** (2M index-tok, best EM HotpotQA 0.722) = nhánh hiệu quả nhất.

**🔴 Liên quan conflate (KG typed-edge cho entity↔attribute):** ✅ HybridRAG faithfulness 0.96 / context-recall 1.00; **NumCoKE** ordinal-aware numeric KG (1200>800, phân biệt 1.2tr vs 1.2k); typed edge `hasPrice` ≠ `hasFeature` → retrieval theo relation-type không lẫn giá↔spec. HalluGraph AUC 0.94 (entity-dense legal).

**Postgres-native (✅):** **Apache AGE + pgvector** (Cypher-in-SQL CTE bridge; pgvector op KHÔNG dùng được trong Cypher; embedding phải bảng riêng) HOẶC **pure-SQL `WITH RECURSIVE` + entity_links** (đơn giản hơn cho ≤3 hop, <10k entity). AGE traversal >10 hop chậm hơn Neo4j.

- **ÁP cho mình:** ❌ KHÔNG full GraphRAG (over-engineering cho catalog factoid + cost 350×). ✅ Cân nhắc **typed-edge KG nhẹ** (entity→hasPrice→value) cho conflate — nhưng **structured stats-route đã có của mình giải quyết rẻ hơn** (Phase A). Mình đã có `knowledge_edges` + graph_rag_default_mode → nếu cần multi-hop legal sau này, dùng **HippoRAG2/pure-SQL recursive**, gate per-bot. Nguồn: 2404.16130 · 2502.11371 · 2506.05690 · 2506.06331 · 2410.05779 · 2502.14802 · 2502.01113 · 2510.08958 · 2408.04948 · 2411.12950.

## B. RAPTOR / hierarchical retrieval
✅ **RAPTOR** (ICLR24, 2401.18059): recursive cluster(UMAP+GMM)→summarize→tree; collapsed-tree retrieve. QuALITY **82.6%** (+20pp SOTA), QASPER 55.7. Retrieval ~0.37s. Tốt cho **long-doc synthesis**, thua flat trên single-hop NQ (-4.7 F1).
- LlamaIndex parent-child / auto-merging / sentence-window: small-to-big = ngắm bắn nhỏ + context lớn.
- **ÁP:** EVOLVE — bật `parent_child_enabled` (đang OFF) + neighbor_expand (đã có) cho doc dài/legal; RAPTOR-tree là tùy chọn ingest nặng, gate per-bot. Map UX case-study (sentence-window CSAT lever).

## C. Embedding models + efficiency
✅ **Qwen3-Embedding-0.6B** (Apache-2, **1024-dim = cột mình**, MTEB-multi 64.33, +1.8 vs Jina-v3, self-host free) = ứng viên nâng cấp tốt nhất same-dim. Qwen3-4B/8B + **MRL truncation→1024** = chất lượng top, cột pgvector không đổi. **BGE-M3** (dense+sparse+ColBERT 1 model) cho hybrid. VN: gte-Qwen2/e5-mistral-7B top VN-MTEB (67-68); **RoPE > Absolute-PE** cho VN.
- **Quantization:** ✅ **halfvec** (fp16, **2× storage, ~0% recall loss** verified jkatz05 — nhưng dim-dependent, test trước); int8 IVF-SQ8 (4×, ~3% loss @1024-dim); ⚠️ binary chỉ khi >50M vec + rescore.
- **ÁP:** EVOLVE — (1) bật **halfvec HNSW** trên cột embedding 1024 hiện tại = giảm 50% storage gần-zero risk; (2) thử Qwen3-0.6B vs Jina-v3 trên golden_set VN; (3) feed `content_segmented` vào dense embed. Nguồn: 2506.05176 · 2507.21500 · jkatz05 quantization · huggingface embedding-quantization.

## D. pgvector scaling (🔴 p95 + multi-tenant)
✅ **Defaults của mình quá thấp:** HNSW `ef_search=40` → recall thực ~**82-88%** (Alibaba bench, KHÔNG phải 90-93%); `ef_construction=64` = demo-grade. → **set `ef_construction=128-200` (rebuild) + `SET LOCAL hnsw.ef_search=160` per-query** (trong transaction — pgbouncer transaction-mode nuốt session GUC).
- ✅ **pgvector 0.8 iterative_scan** cho filtered search (AWS Aurora: filtered recall 10%→~95-99%, **KHÔNG phải 100%** — `max_scan_tuples=20000` cap, subquery-filter không kích hoạt). RLS WHERE phải có index trên `record_tenant_id` + policy `LEAKPROOF STABLE`.
- ✅ **per-tenant partial HNSW index** cho top tenant (11× nhỏ hơn, build 20× nhanh). ⚠️ "37.2× per-tenant" là FAISS-not-pgvector + best-case; "pgvectorscale 28× vs Pinecone" = vendor cherry-pick (s1 tier). ⚠️ LWLock ~32-conn plateau = config-specific, maintainer đóng không fix.
- ✅ Parallel index build: `maintenance_work_mem=4-8GB` + `max_parallel_maintenance_workers` (30× nhanh), `REINDEX CONCURRENTLY`.
- **ÁP:** 🔴 EVOLVE (ops, đánh p95): ef_construction=128 + ef_search=160 per-query + halfvec + iterative_scan + audit RLS index/LEAKPROOF + pgbouncer transaction-mode SET LOCAL. Nguồn: jkatz05 · AWS Aurora 0.8 · ACORN 2403.04871 · Curator 2401.07119.

## E. RAG security (🔴 multi-tenant + URL ingest)
🔴 ✅ **Indirect prompt injection qua doc retrieve** — guardrail regex của mình **chỉ soi user-turn, MÙ với chunk retrieve**. ASR undefended ~25% (hidden span/CSS/alt-text); web-crawl 22.7% RAG follow injected. Defense: **PromptGuard-2** (97.5% recall@1%FPR, 19ms) / **InstructDetector** (ASR→0.03%) **scan từng chunk TRƯỚC context-assembly** + HTML-sanitize+Unicode-normalize tại ingest.
- ✅ **PoisonedRAG** (USENIX25): **5 doc độc → 90% ASR** corpus triệu doc; 0.04% corpus → 98% ASR. Defense: provenance-tag + trust-tier nguồn URL + rate-limit ingest/tenant + RevPRAG (98% TPR).
- 🔴 ✅ **Cross-tenant cache leak**: semantic-cache key PHẢI có `record_tenant_id` (mình đã đúng — verify test); KV-cache timing side-channel nếu shared LLM endpoint; embedding-inversion (đừng expose raw vector qua API).
- OWASP LLM 2025: LLM01 injection, LLM04 poisoning, LLM08 vector/embedding weakness. Guardrail framework: LlamaFirewall (PromptGuard2+AlignmentCheck → ASR 17.6%→1.75%), NeMo, Llama-Guard-3.
- **ÁP:** 🔴 NEW (P0) — retrieval-layer injection scanner cho chunk; ingest-time HTML-sanitize + provenance-tag cho URL-source; verify cache tenant-isolation test. Nguồn: OWASP-LLM-2025 · 2402.07867 · 2505.06311 · 2505.03574 · 2604.00387.

## F. Reranking (🔴 p95 lever)
✅ **jina-reranker-v3** (0.6B, BEIR **61.94**, 188ms, Qwen3-base, listwise) = top cross-encoder; **Qwen3-Reranker-0.6B** (MTEB-R 65.8); **ViRanker** VN; mxbai-v2 61.44. ColBERT/late-interaction 2× nhanh hơn cross-encoder (22ms vs 50ms) nhưng BEIR thấp hơn ~9pt.
- ⚠️ **Rerank = 28-87% latency** pipeline (RAGPerf); production 1,895 QPS (ANN) → <100 QPS (+cross-encoder) = 18× cost cho +10% MRR. **Cost-gate: skip rerank khi top-score ≥0.85** (~20-30% query); LLM-listwise >5s (đừng dùng realtime).
- **ÁP:** EVOLVE — rerank của mình có CLIFF filter; thêm **cost-gate skip rerank** khi retrieval-score cao (đánh p95); cân nhắc ViRanker cho bot VN (đã ghi PART-1). Nguồn: 2509.25085 · 2506.05176 · RAGPerf 2603.10765.

## G. Hybrid-search tuning (🔴 retrieval quality)
✅ **RRF k=60 default sub-optimal** — k=10 thường thắng (Recall@5 0.716 vs 0.695; AutoRAG ctx-precision k=10>k=5>k=3); Bruch (TOIS23) per-retriever tuned k +2.6 nDCG. **DBSF** (3-sigma norm) top AutoRAG. **Convex-combo α=0.5** > RRF k=60. **DAT dynamic-alpha** per-query +6.6% (P@1 0.874 vs 0.846, +1.3% overhead). α theo query-type: technical/code ~0.3 (sparse), conversational ~0.7-0.8 (dense).
- Hybrid + cross-encoder rerank: Recall@5 0.695→**0.816**, MRR@3 +39.7%.
- **ÁP:** EVOLVE — mình có adaptive RRF per-intent + bm25/vector weight. Thử **rrf_k=10-40** + per-query-type alpha + autocut(CLIFF có rồi). Nguồn: 2210.11934 · 2410.20878 · 2503.23013 · 2604.01733.

## H. Table / structured-data RAG (🔴 ĐÁNH THẲNG CONFLATE)
🔴 **Đây là cụm fix mạnh nhất cho bug giá của mình:**
- ✅ **STC structure-aware chunking** (2605.00318): Row-Tree key-value block, header↔value binding, overlap-free → **MRR +66%, Recall@1 +106%** vs recursive. Mỗi row = 1 chunk atomic, không trộn 2 giá.
- ✅ **Narrativization + metadata pre-filter** (row→prose + indexed `price_tier`/`duration` fields): production **numeric-accuracy 99.7%, hallucination 0.02%**, p95 423ms. Metadata-filter exact-key TRƯỚC cosine → loại variant sai trước khi rank.
- ✅ **TableRAG cell-level** (NeurIPS24): cell `(col,value)` atomic, schema+cell retrieval → recall 98.3%. **H-STAR** (NAACL25): route price-lookup → SQL exact (no conflate); descriptive → vector.
- ✅ **Per-table LLM description** (RAG-Anything) embed kèm row; **type-gated O(tables)** không O(rows).
- **ÁP:** 🔴 (xếp #1-3 trong DEEPDIVE §3) — table_csv **per-row + header-bind** (STC), bỏ group-chunk; metadata pre-filter; price-intent→stats SQL (= Phase A). Nguồn: 2605.00318 · 2410.04739 · 2407.05952 · ijournalse-3380.

## I. RAG Evaluation CI + monitoring (🔴 thoát "test lòi bug, fix bừa")
🔴 **Lời giải hệ thống cho frustration của bạn:**
- ✅ **CI gate** (RAGAS `in_ci=True` + pytest / DeepEval `test run` / Promptfoo) → fail PR khi metric < threshold. ~$0.001-0.003/test-case (200-q golden ~$1/run). Gate **per-layer**: retrieval {Context-Recall≥0.85, Context-Precision≥0.75} · generation {Faithfulness≥0.85, Answer-Relevancy≥0.75}.
- ✅ **4-quadrant diagnostic** (Faithfulness × Context-Recall): **HIGH-faith + LOW-recall = SILENT REFUSAL** = đúng Coverage<0.95 blocker của mình. Fix LOW-recall = **sửa retrieval, KHÔNG thêm sysprompt rule** (khớp bài học spa-07 alembic 0154/0156/0158). Metric **ARSP** (over-abstention) = 1−Coverage.
- ✅ **Synthetic test-set** (RAGAS TestsetGenerator / DeepEval / **DataMorgana** best-diversity) tự sinh QA từ corpus → giải cold-start. ⚠️ synthetic tin được cho retrieval-A/B, KHÔNG cho generator-compare.
- ✅ **Monitoring:** Langfuse (LangGraph callback 3-dòng — khớp stack mình) + RAGAS-on-sampled-traces; Arize-Phoenix (OTel, drift embedding-space only — miss behavioral); Evidently (drift query-cluster). Sample 1-5% trace + 100% low-confidence + 100% new-deploy 48h.
- ✅ **LLM-judge:** GPT-4o ~80-85% human-agreement (faithfulness khó nhất, Spearman 0.55); **binary>numeric** (77% vs 65% consistency); **cross-family judge bắt buộc** (self-enhancement bias); randomize order (position bias). ARES + PPI (150-300 human label) → confidence-interval.
- ✅ **Deploy:** shadow → canary (1%→5%→20%→50%→100%, hold 24-48h) → A/B; rollback khi p99 +40% / refuse +5% / faithfulness drop.
- **ÁP:** 🔴 NEW (ưu tiên cao) — wire `tests/eval/test_rag_gates.py` (RAGAS dual-gate, chạy khi `src/`/`alembic/` đổi) + Langfuse trace + RefusalBench-style ARSP tracking. Tận dụng `golden_set/` + `verify_fixes_loadtest.py` có sẵn. Nguồn: RAGAS 2309.15217 · ARES 2311.09476 · RefusalBench 2510.10390 · DataMorgana 2501.12789 · 2410.15531 · Langfuse/Phoenix/Evidently docs.

---

## J. ADVERSARIAL CAVEATS (claim đã bị bác — đừng tin mù)
| Claim | Verdict thật |
|---|---|
| GraphRAG/LightRAG win-rate 66-72% vs NaiveRAG | 🔴 sau khử bias còn ~32-39% (NaiveRAG thắng) — 2506.06331 |
| pgvector iterative_scan → 100% completeness | ⚠️ cap `max_scan_tuples=20k`, subquery-filter không kích hoạt → partial |
| pgvectorscale 28× vs Pinecone | ⚠️ vendor cherry-pick (Pinecone s1 cost-tier); fair p2 = 1.4× |
| per-tenant HNSW 37.2× | ⚠️ FAISS-not-pgvector, uniform-1000-tenant best-case, vs IVF-Flat baseline |
| ef_search=40 → 90-93% recall | ⚠️ thực ~82-88% @ M=16 (Alibaba); cần ef_search 80-160 cho 95%+ |
| halfvec <1% recall loss | ✅ ~0% verified nhưng dim-dependent (384-dim rủi ro hơn 1024); test trước |
| GraphRAG "86% vs 32% multi-hop" | ⚠️ blog/enterprise cherry-pick; academic chỉ +1-5pp |
| Acurai "100% hallucination elim" | ⚠️ corpus sạch 3-passage, GPT-only |

---

## K. TOP ADOPTION MỚI (PART-2) map vào bug mình
1. 🔴 **Table STC per-row + metadata pre-filter** (conflate) — STC MRR+66%/R@1+106%, numeric-acc 99.7%.
2. 🔴 **Eval-CI dual-gate + ARSP** (thoát fix-bừa) — Coverage/Faithfulness per-layer, fix retrieval không sysprompt.
3. 🔴 **Retrieval-layer injection scanner + URL provenance** (security P0) — guardrail hiện mù chunk-injection.
4. 🔴 **pgvector tune** (p95): ef_construction=128 + ef_search=160 + halfvec + iterative_scan + RLS-index.
5. **Rerank cost-gate** (skip khi top-score cao) + **rrf_k=10-40** + per-query alpha (p95 + quality).
6. **Qwen3-0.6B / ViRanker** swap thử (VN recall, same-dim).
7. **Langfuse trace** (LangGraph 3-dòng) + sampled RAGAS (monitoring).
8. (sau) HippoRAG2/pure-SQL-recursive cho multi-hop legal — KHÔNG full MS-GraphRAG.

---
*Sinh từ ~50 agent (PART-2/3 + adversarial). Sibling: RAG_RESEARCH_MASTER_20260618.md · reports/{PROJECT_UNDERSTANDING_EXPERT_RAG,CHUNKING_RESEARCH_VS_CODE,PROJECT_ALL_FLOWS}_20260618.md · plans/260618-phaseA-bug1-conflate/plan.md*
