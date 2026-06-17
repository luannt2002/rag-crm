# PHẦN L — NGHIÊN CỨU & PHÂN TÍCH CẠNH TRANH (v1.3)

> Tổng hợp từ 80+ nguồn nghiên cứu (Anthropic, Google, Microsoft, Perplexity, Cohere, và cộng đồng).
> Cập nhật: 2026-04-28 (Sprint 10 + 04/2026 BP deltas appended ở §60.5). Mỗi kỹ thuật đều có benchmark data và source URL inline.

## 57. PostgreSQL BM25 — Lựa chọn Sparse Search

### Kết luận: pgvector + BM25 extension là lựa chọn tối ưu

Ragbot sử dụng PostgreSQL làm single source of truth. Nghiên cứu từ 7+ nguồn xác nhận PostgreSQL với BM25 extension vượt trội cả về performance lẫn operational simplicity:

| Extension | BM25 | Speed vs Elasticsearch | License | Notes |
|-----------|------|------------------------|---------|-------|
| **pg_textsearch** (Timescale) | Full BM25 | 2.4-6.5x faster ([Source](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres)) | Apache 2.0 | Designed for hybrid với pgvector |
| **VectorChord-BM25** (TensorChord) | Full BM25 | 3x faster QPS (112 vs 49) ([Source](https://blog.vectorchord.ai/vectorchord-bm25-revolutionize-postgresql-search-with-bm25-ranking-3x-faster-than-elasticsearch)) | Apache 2.0 | BPE tokenizer support |
| **ParadeDB pg_search** | Full BM25 via Tantivy | Comparable | AGPL | Full Elastic-quality search |

**Vấn đề hiện tại**: Ragbot dùng `ts_rank` — chỉ có term frequency, KHÔNG có IDF, length normalization, TF saturation. Đây là gap lớn nhất (P0).

**Lợi thế kiến trúc**: Single source of truth, zero data sync, zero operational overhead. Instacart case study: migrate sang pgvector tiết kiệm 80% storage ([Source](https://zilliz.com/comparison/elastic-vs-pgvector)). Vietnamese tokenization qua underthesea (app-level) tương đương index-level analyzers ([Source](https://blog.tuando.me/vietnamese-full-text-search-on-postgresql)).

## 58. Vietnamese NLP cho RAG

### 58.1 Xử lý không dấu (Tri-path Search)

Vietnamese queries thường thiếu dấu ("goi dau" thay vì "gội đầu"). Giải pháp: tri-path search.

```
User query → Normalize (teencode, abbreviations)
  ├── Path 1: Original query → Vector search (dense)
  ├── Path 2: remove_diacritics(query) → BM25 trên unaccented column
  └── Path 3: restore_diacritics(query) → BM25 trên accented column
  → RRF merge → Rerank → Generate
```

**Ragbot status**: ⚠️ Partial — `remove_diacritics()` done trong `shared/vi_tokenizer.py`, hybrid search có diacritic-normalized variant. Chưa có dual-index (accented + unaccented columns) và chưa ML diacritic restoration.

**ML Diacritic Restoration**: Transformer model đạt 94-98% accuracy ([Source](https://github.com/duongntbk/restore_vietnamese_diacritics) — 94.05%, [TDP](https://aclanthology.org/2020.paclic-1.9.pdf) — 98.37%). Caveat: ambiguous cases ("da" = đã/da/dã).

### 58.2 Word Segmentation

| Tool | Accuracy | BM25 Impact | Notes |
|------|----------|-------------|-------|
| **underthesea** | 80% | **+32% recall** (MIRACL) | **MUST** dùng cho CẢ indexing VÀ querying |

**Source**: [Word Tokenizer Benchmark](https://huybik.github.io/Word-Tokenizer-Benchmark/)

**Ragbot status**: ✅ Done — underthesea word_tokenize trong `shared/vi_tokenizer.py`

### 58.3 Embedding Models cho tiếng Việt

| Model | Vietnamese nDCG@10 | Dimensions | Self-host? | Ragbot |
|-------|-------------------|------------|------------|--------|
| **BGE-M3** | **0.72** | 1024 | Yes | ❌ Chưa |
| bge-vi-base | ~0.70 | 768 | Yes | ❌ |
| multilingual-e5-large | 0.65 | 1024 | Yes | ❌ |
| text-embedding-3-small (đang dùng) | ~0.60-0.65 | 1536 | No (API) | ✅ |

**BGE-M3 hơn OpenAI 10-15%** cho Vietnamese retrieval. Source: [VN-MTEB](https://arxiv.org/html/2507.21500v1), [Viblo Benchmark](https://viblo.asia/p/so-sanh-cac-mo-hinh-embedding-cho-tieng-viet-qua-benchmark-2025-AoJe88G141j).

### 58.4 Vietnamese Reranker

**ViRanker**: BGE-M3-based, NDCG@3 = **0.6815** on MMARCO-VI. Hiện tại ragbot dùng **ZeroEntropy `zerank-2`** (multilingual instruction-following cross-encoder) làm default reranker — swap-in từ 2026-05-12 (commit `b9e7761`). Reranker registry còn cung cấp `Jina v3`, `LiteLLM` (Cohere/Voyage), `ViRanker` BGE-M3 local, `Null`. Workload Vietnamese-heavy có thể switch sang `ViRanker` per-bot. Source: [ViRanker](https://arxiv.org/abs/2509.09131), [ZeroEntropy zerank-2](https://docs.zeroentropy.dev).

### 58.5 Teencode & Abbreviation

**ViSoLex**: Normalize "ko" → "không", "BHXH" → "bảo hiểm xã hội", "tks" → "thanks". Source: [ViSoLex paper](https://arxiv.org/abs/2501.07020), [GitHub](https://github.com/HaDung2002/visolex).

**Ragbot status**: ❌ Chưa có — cần thêm abbreviation dict vào `system_config` (P1-4).

## 59. Kỹ thuật nâng cao Context Quality

### 59.1 Contextual Retrieval (Anthropic)

Prepend LLM-generated context vào mỗi chunk trước embedding: "This chunk discusses [X] from document [Y] about [Z]."

| Metric | Improvement |
|--------|-------------|
| Contextual alone | -35% retrieval failure |
| + BM25 hybrid | -49% |
| + Reranking | **-67%** |
| Cost | $1.02/M tokens (one-time ingest) |

Source: [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval). **Ragbot**: ✅ Done — `shared/contextual_enrichment.py`.

### 59.2 Parent Document Retrieval (Small-to-Big)

Index chunks nhỏ (128-256 tokens) cho precise matching → trả về parent chunk lớn (1024+) cho LLM context. Kết quả: **+10-20% answer accuracy**.

Source: [LanceDB](https://www.lancedb.com/blog/modified-rag-parent-document-bigger-chunk-retriever-62b3d1e79bc6), [Multi-Vector Indexing](https://dev.to/jamesli/optimizing-rag-indexing-strategy-multi-vector-indexing-and-parent-document-retrieval-49hf). **Ragbot**: ❌ Chưa có — cần thêm `parent_chunk_id` FK (P1-2).

### 59.3 Late Chunking (Jina)

Embed full document → split embeddings at chunk boundaries. Giữ được cross-chunk context mà traditional chunking mất.

| Metric | Result |
|--------|--------|
| Average improvement | **+24.47%** across BeIR benchmarks |
| Finding | Fixed-token + late chunking > semantic chunking |

Source: [Jina Late Chunking](https://arxiv.org/abs/2409.04701). **Ragbot**: ✅ Done — `shared/late_chunking.py`.

### 59.4 RAPTOR Tree Retrieval

Cluster chunks → summarize → build tree hierarchy. Root = broad themes, leaves = details. **+20% accuracy** trên QuALITY benchmark. Source: [RAPTOR paper](https://arxiv.org/abs/2401.18059). **Ragbot**: ❌ Chưa có (P3).

### 59.5 Chunking Benchmark 2026

| Strategy | Accuracy | Source |
|----------|----------|--------|
| **Recursive 512-token** | **69%** (#1 general) | [FloTorch](https://www.runvecta.com/blog/we-benchmarked-7-chunking-strategies-most-advice-was-wrong) |
| Page-level | 64.8% | [NVIDIA](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/) |
| Semantic | 54% (over-segments, avg 43 tokens) | [NAACL 2025 Vectara](https://arxiv.org/abs/2410.13070) |
| **Adaptive (AdapChunk)** | **87%** (p=0.001) | [Mayo Clinic PMC12649634](https://pmc.ncbi.nlm.nih.gov/articles/PMC12649634/) |

**Ragbot decision**: Adaptive chunking (HDT + paragraph-based, 2 strategies implemented) cho specialized domains, recursive 512-token làm fallback. PROPOSITION planned.

## 60. Bài học từ Production Systems

### 60.1 Perplexity AI

- Query understanding = **50%+ RAG quality**. Invest more in retrieval than generation
- BM25 vẫn outperform vector cho entity/factual queries
- Citation verification bằng separate model
- pplx-embed: 81.96% nDCG@10, beats Voyage-context-3 (79.45%), MIT license, INT8 native = 4x storage reduction

Source: [Architecture](https://blog.bytebytego.com/p/how-perplexity-built-an-ai-google), [pplx-embed](https://research.perplexity.ai/articles/pplx-embed-state-of-the-art-embedding-models-for-web-scale-retrieval)

### 60.2 Glean

- **Chunking strategy > embedding model choice** — spend nhiều time hơn vào chunking hơn switching models
- Metadata is king: title, author, date, team → boost relevance hơn switching embedding models
- Permission filtering = table-stakes, phải BEFORE vector search

Source: NeurIPS 2024 industry track, [Glean Knowledge Graph](https://www.glean.com/blog/knowledge-graph-agentic-engine)

### 60.3 Notion AI

- Hierarchical context critical: "Penalty is 5M VND" vô nghĩa nếu không biết regulation nào
- Table handling riêng — serialize rows with headers, không flatten

Source: Notion engineering blog 2024

### 60.4 Cursor AI

- Fewer, better chunks > more, noisier chunks
- File path = surprisingly strong retrieval signal
- Incremental re-indexing via Merkle tree

Source: [How Cursor Indexes Codebases](https://towardsdatascience.com/how-cursor-actually-indexes-your-codebase/)

### 60.5 NotebookLM (Google)

- Source-only grounding = eliminate hallucination at architecture level
- Multi-pass retrieval cho synthesis tasks
- PDF parsing quality = major bottleneck — "quality in, quality out"

Source: [NotebookLM Architecture](https://arxiv.org/html/2504.09720v2)

### 60.6 Cohere

- Reranking: **+15-30% relevance** consistently across domains
- Multi-step search query generation: **+20% recall**
- ColBERT late interaction: near cross-encoder quality at bi-encoder speed

Source: Nils Reimers talks 2024-2025, [ColBERT explained](https://developer.ibm.com/articles/how-colbert-works/)

## 61. Papers & Projects mới nhất 2026

### Papers (arxiv)

| Paper | Key Finding | Year | Source |
|-------|------------|------|--------|
| A-RAG | LLM autonomously chooses keyword/semantic/chunk tools | 2026 | [arxiv 2602.03442](https://arxiv.org/abs/2602.03442) |
| RAG-Fusion Fails | Multi-query **FAILS** after reranking (-3% Hit@10) | 2026 | [arxiv 2603.02153](https://arxiv.org/abs/2603.02153) |
| Graph RAG at Scale | LPG + RDF, dynamic doc retrieval | 2026 | [arxiv 2603.22340](https://arxiv.org/pdf/2603.22340) |
| GraphRAG-Bench (ICLR) | +85.7% multi-hop, -13.4% factoid | 2026 | [arxiv 2506.05690](https://arxiv.org/html/2506.05690v3) |
| RAPTOR | Recursive tree retrieval +20% | 2024 | [arxiv 2401.18059](https://arxiv.org/abs/2401.18059) |
| Self-RAG | Learn to retrieve, generate, critique | 2024 | [arxiv 2310.11511](https://arxiv.org/abs/2310.11511) |
| CRAG | Corrective retrieval evaluator | 2024 | [arxiv 2401.15884](https://arxiv.org/abs/2401.15884) |
| HyDE | Hypothetical document embedding | 2023 | [arxiv 2212.10496](https://arxiv.org/abs/2212.10496) |
| VietNormalizer | Vietnamese text normalization | 2026 | [arxiv 2603.04145](https://arxiv.org/html/2603.04145v1) |
| ViSoLex | Teencode normalization | 2025 | [arxiv 2501.07020](https://arxiv.org/abs/2501.07020) |

### Projects (GitHub, sorted by stars)

| Project | Stars | Key Feature | URL |
|---------|-------|-------------|-----|
| RAGFlow | 78K+ | Deep document parsing, "quality in quality out" | [GitHub](https://github.com/infiniflow/ragflow) |
| Pathway | 63K+ | Real-time incremental indexing, 350+ connectors | [GitHub](https://github.com/pathwaycom/pathway) |
| LightRAG | 33K+ | Simple graph RAG, EMNLP 2025 | [GitHub](https://github.com/HKUDS/LightRAG) |
| Microsoft GraphRAG | 32K+ | LazyGraphRAG: 700x cheaper, same quality | [GitHub](https://github.com/microsoft/graphrag) |
| R2R (SciPhi) | 7.7K | Production RAG API, hybrid search | [GitHub](https://github.com/SciPhi-AI/R2R) |
| AutoRAG | 4.7K | Auto-find optimal RAG pipeline for YOUR data | [GitHub](https://github.com/Marker-Inc-Korea/AutoRAG) |
| UltraRAG | — | MCP-based RAG IDE, Tsinghua | [GitHub](https://github.com/OpenBMB/UltraRAG) |
| RAGatouille | — | ColBERT integration for RAG | [GitHub](https://github.com/AnswerDotAI/RAGatouille) |

### Xu hướng Châu Á 2026

- **China**: Graph-RAG thay pure vector, agentic multi-round loops, long-term memory systems
- **Japan**: GUI-based RAG SaaS, enterprise adoption in regulated industries

## 62. Danh mục nguồn tham khảo (81 URLs)

### PostgreSQL BM25 (1-7)

1. https://www.tigerdata.com/blog/you-dont-need-elasticsearch-bm25-is-now-in-postgres
2. https://blog.vectorchord.ai/vectorchord-bm25-revolutionize-postgresql-search-with-bm25-ranking-3x-faster-than-elasticsearch
3. https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres
4. https://zilliz.com/comparison/elastic-vs-pgvector
5. https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual
6. https://github.com/duydo/elasticsearch-analysis-vietnamese
7. https://blog.tuando.me/vietnamese-full-text-search-on-postgresql

### Vietnamese NLP (8-23)

8. https://viblo.asia/p/elasticsearch-phan-tich-va-tim-kiem-du-lieu-tieng-viet-3P0lPveoKox
9. https://github.com/duongntbk/restore_vietnamese_diacritics
10. https://github.com/VNOpenAI/vn-accent
11. https://aclanthology.org/2020.paclic-1.9.pdf
12. https://huybik.github.io/Word-Tokenizer-Benchmark/
13. https://underthesea.readthedocs.io/en/latest/readme.html
14. https://arxiv.org/html/2507.21500v1
15. https://viblo.asia/p/so-sanh-cac-mo-hinh-embedding-cho-tieng-viet-qua-benchmark-2025-AoJe88G141j
16. https://nqbao.medium.com/benchmarking-text-embedding-models-for-vietnamese-retrieval-tasks-3c4342e0ff9d
17. https://arxiv.org/abs/2509.09131
18. https://arxiv.org/html/2603.04145v1
19. https://arxiv.org/abs/2501.07020
20. https://github.com/HaDung2002/visolex
21. https://howtovietnamese.com/vietnamese-text-abbreviations-slang/
22. https://arxiv.org/html/2507.14619
23. https://arxiv.org/html/2403.01616v1

### RAG Best Practices (24-36)

24. https://superlinked.com/vectorhub/articles/optimizing-rag-with-hybrid-search-reranking
25. https://ragflow.io/blog/rag-review-2025-from-rag-to-context
26. https://www.anthropic.com/news/contextual-retrieval
27. https://langcopilot.com/posts/2025-10-11-document-chunking-for-rag-practical-guide
28. https://www.firecrawl.dev/blog/best-chunking-strategies-rag
29. https://dev.to/gabrielanhaia/the-rag-chunking-strategy-that-beat-all-the-trendy-ones-in-production-1en2
30. https://dev.to/jamesli/optimizing-rag-indexing-strategy-multi-vector-indexing-and-parent-document-retrieval-49hf
31. https://dasroot.net/posts/2026/04/multi-query-re-ranking-advanced-rag/
32. https://www.meilisearch.com/blog/adaptive-rag
33. https://dev.to/young_gao/rag-is-not-dead-advanced-retrieval-patterns-that-actually-work-in-2026-2gbo
34. https://www.morphik.ai/blog/retrieval-augmented-generation-strategies
35. https://markaicode.com/bge-reranker-cross-encoder-reranking-rag/
36. https://blog.premai.io/rag-chunking-strategies-the-2026-benchmark-guide/

### Production Systems (37-42)

37. https://blog.bytebytego.com/p/how-perplexity-built-an-ai-google
38. https://ziptie.dev/blog/how-perplexity-ai-answers-work/
39. https://vespa.ai/perplexity/
40. https://research.perplexity.ai/articles/pplx-embed-state-of-the-art-embedding-models-for-web-scale-retrieval
41. https://developer.ibm.com/articles/how-colbert-works/
42. https://www.lateinteraction.com/

### Papers (43-56)

43. https://arxiv.org/abs/2602.03442
44. https://arxiv.org/abs/2603.02153
45. https://arxiv.org/pdf/2603.22340
46. https://arxiv.org/abs/2510.12323
47. https://arxiv.org/abs/2501.09136
48. https://arxiv.org/abs/2504.14891
49. https://arxiv.org/abs/2401.18059
50. https://arxiv.org/abs/2310.11511
51. https://arxiv.org/abs/2401.15884
52. https://arxiv.org/abs/2409.04701
53. https://arxiv.org/html/2506.05690v3
54. https://arxiv.org/html/2507.03226v3
55. https://arxiv.org/pdf/2505.23052
56. https://arxiv.org/abs/2504.19754

### GitHub Projects (57-68)

57. https://github.com/infiniflow/ragflow
58. https://github.com/HKUDS/LightRAG
59. https://github.com/microsoft/graphrag
60. https://github.com/microsoft/benchmark-qed
61. https://github.com/OpenBMB/UltraRAG
62. https://github.com/SciPhi-AI/R2R
63. https://github.com/pathwaycom/pathway
64. https://github.com/truefoundry/cognita
65. https://github.com/Marker-Inc-Korea/AutoRAG
66. https://github.com/AnswerDotAI/RAGatouille
67. https://github.com/timescale/pg_textsearch
68. https://github.com/parthsarthi03/raptor

### Chinese/Japanese (69-71)

69. https://segmentfault.com/a/1190000047621497
70. https://cloud.tencent.cn/developer/article/2649862
71. https://aws.amazon.com/what-is/retrieval-augmented-generation/

### Evaluation (72-74)

72. https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
73. https://www.confident-ai.com/blog/rag-evaluation-metrics-answer-relevancy-faithfulness-and-more
74. https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/

### Other (75-81)

75. https://blog.cloudflare.com/introducing-autorag-on-cloudflare/
76. https://www.sthambh.com/blog/agentic-rag-enterprise-guide/
77. https://tianpan.co/blog/2026-04-09-long-context-vs-rag-production-decision-framework
78. https://www.llamaindex.ai/blog/towards-long-context-rag
79. https://redis.io/blog/rag-at-scale/
80. https://calmops.com/ai/graphrag-complete-guide-2026/
81. https://www.akira.ai/blog/graph-based-filtering-enhances-rag

---

## 60.5 NEW Q1-Q2 2026 best-practice deltas (added Sprint 10, 2026-04-28)

Source: [BEST_PRACTICE_BENCHMARK_2026.md](../../reports/BEST_PRACTICE_BENCHMARK_2026.md) — 9 agent-runs research session 2026-04-28.

### Models released Q1-Q2 2026

| Model | Date | Note |
|---|---|---|
| Claude Opus 4.7 | Q2 2026 | 1M context, lead TruthfulQA + GPQA + SWE-bench Verified 87.6%, $15/$75 |
| Claude Sonnet 4.6 | Q1 2026 | 1M context, $3/$15 |
| Claude Haiku 4.5 | Oct 2025 | $1/$5, cheap judge/grader |
| GPT-5.4 | Q1 2026 | 400K+ ctx, structured output <0.1% fail |
| Gemini 3.1 Pro | Q1 2026 | 2M ctx, multimodal SOTA |
| Voyage-4-large | Jan 15 2026 | First production MoE embedder, MRL+binary native |
| Jina Reranker v3 | Sep 2025 | 0.6B listwise SOTA, 188ms, 81.3% Hit@1, MIRACL 66.5 |
| **ViRanker** | arXiv 2509.09131 | **BGE-M3 + BPT, SOTA cho Vietnamese reranking** |
| AITeamVN/Vietnamese_Embedding | 2025 | bge-m3 fine-tuned ~300K VN triplets |
| RankLLM CLI 1.0 | Mar 26 2026 | Listwise rerankers (RankGPT/RankZephyr/RankGemini) packaged |
| pgvector 0.8 | 2025-2026 | halfvec mainstream, 67× HNSW build speed-up |
| PaddleOCR-VL-1.5 | Q1 2026 | 0.9B vision-language, VN supported |

### Patterns mainstream Q1-Q2 2026

- **Contextual Retrieval** (Anthropic 2024-09) — production-validated, -49% retrieval failure (-67% với rerank). **Ragbot Sprint 10 ship** ở `contextual_chunk_enrichment.py`.
- **Late Chunking** (Jina v3/v4) — production-ready với 8K-ctx embedder. Ragbot có file `late_chunking.py` nhưng dead-feature (chưa measure).
- **MCP (Model Context Protocol)** — donated to Linux Foundation AAIF Dec 2025, 17K+ servers Q1-2026, 97M monthly SDK DL. Ragbot defer (chưa có tool usecase).
- **Adaptive RAG** (router by complexity) — MANDATORY 2026. Ragbot 14-node LangGraph + CRAG grade + Self-RAG reflect → đi đúng playbook.
- **Prompt caching** (Anthropic 90%, OpenAI 75%) — break-even 1.4-2 reads (5-min). **Ragbot Sprint 9 Wave C ship** helper `_apply_anthropic_cache_control()`.
- **JSON mode / structured output** > LLM-as-judge guardrail — Anthropic + OpenAI <0.1% fail rate. Ragbot defer Sprint 11 (C.3).
- **DeepEval** = best for CI; **Phoenix** + **Langfuse** = best obs OSS. **Ragbot Sprint 10 ship** DeepEval scaffold; Phoenix defer Sprint 11.
- **Multi-query expansion** (3-5 paraphrases + RRF merge) > HyDE single-shot (BEIR/MIRACL). **Ragbot Sprint 10 ship** ở `multi_query_expansion.py`.

### Patterns ragbot SHIP Sprint 9-10 (mapping)

| BP 04/2026 | Ragbot impl | Sprint |
|---|---|---|
| 3-key identity tenant_id NOT NULL | migration 0041 + 0042 + ORM tighten | 9 A0 |
| Contextual Retrieval (Anthropic) | `contextual_chunk_enrichment.py` | 10 |
| Multi-query + RRF merge | `multi_query_expansion.py` | 10 |
| Metadata-aware retrieval + GIN index | `query_intent_extractor.py` + migration 0044 | 10 |
| VN compound segmentation | `segment_vi_compounds()` + migration 0046 | 10 |
| CircuitBreaker per-provider | `dynamic_litellm_router.py` wired | 10 |
| Cache-stampede single-flight | `semantic_cache.py` asyncio.Lock | 10 |
| Anthropic prompt caching | `_apply_anthropic_cache_control()` | 9 C |
| Lost-in-the-middle reorder | `context_utils.reorder_for_lost_in_middle` | 9 D |
| DeepEval RAGAS runner | `scripts/deepeval_runner.py` + golden 40+60 | 10 |
| Real LLM SSE streaming | `complete_runtime_stream()` | 9 Tier 1 |
| Per-tenant rate-limit + token cap | `tenant_rate_limiter.py` + `tenant_token_meter.py` + migration 0045 | 10 |

### Patterns ragbot DEFER (chờ trigger)

- ColPali / ColQwen2-VL — chỉ khi tenant scanned PDF/CCCD.
- GraphRAG community summary — chỉ khi corpus >100K chunks.
- BERAG (Bayesian Ensemble RAG, arXiv 2604.22678 Apr 2026) — paper 4 ngày tuổi, defer 6-12 tháng.
- LazyGraphRAG (Microsoft 2024) — TODO comment trong `knowledge_graph.py:7`, defer.

---
