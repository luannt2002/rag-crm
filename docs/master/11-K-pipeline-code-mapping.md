# PHẦN K — RAG PIPELINE CHUẨN & CODE MAPPING (v1.2)

> Mỗi bước trong pipeline RAG chuẩn, benchmark data, nguồn tham khảo, và mapping với code ragbot.
> Cập nhật: 2026-04-20 | Nguồn: 80+ tài liệu từ Anthropic, Google, Microsoft, Perplexity, Cohere, và cộng đồng.

## 52. Tổng quan Pipeline — 24 bước (10 Ingestion + 14 Query)

```
═══════════════════════════════════════════════════════════
  INGESTION PIPELINE (Offline — khi upload tài liệu)
═══════════════════════════════════════════════════════════

[I1] Document Upload → dedup check, store raw file
[I2] Document Parsing → layout-aware (tables, headers, images)
[I3] Document Cleaning → noise removal, Unicode normalization
[I4] Metadata Extraction → title, date, author, type, topics
[I5] Chunking Strategy Selection → adaptive per document type
[I6] Chunking Execution → sentence boundary, overlap, parent-child
[I7] Contextual Enrichment → LLM context prefix (Anthropic style)
[I8] Embedding Generation → batch, versioned, late chunking
[I9] Index Storage → HNSW + BM25 hybrid, metadata indexed
[I10] Quality Validation → self-retrieval test, chunk coherence

═══════════════════════════════════════════════════════════
  QUERY PIPELINE (Online — khi user hỏi)
═══════════════════════════════════════════════════════════

[Q1] Input Guardrail → empty, injection, PII, too-short
[Q2] Semantic Cache Check → hash + cosine similarity
[Q3] Query Understanding → intent classification, routing
[Q4] Query Preprocessing → Vietnamese segment, diacritic normalize
[Q5] Query Rewriting → HyDE, multi-query decomposition
[Q6] Hybrid Retrieval → Dense (HNSW) + BM25 → RRF fusion
[Q7] Reranking → cross-encoder top-N
[Q8] Relevance Grading → CRAG pattern, retry logic
[Q9] Context Assembly → XML tags, citation whitelist
[Q10] Generation → faithful, cited, low temperature
[Q11] Output Guardrail → citation validation, PII check
[Q12] Self-Reflection → Self-RAG, max 2 retries
[Q13] Cache Storage → TTL, corpus version invalidation
[Q14] Response Delivery → streaming, sources, feedback
```

## 53. INGESTION PIPELINE — 10 bước chi tiết

### 53.1 Document Upload & Dedup

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Nhận file upload, kiểm tra trùng lặp bằng SHA-256 hash, lưu raw file bất biến |
| **Best practice** | SHA-256 hash → reject exact duplicates trước mọi xử lý; lưu raw file bất biến (S3/MinIO). Near-dedup bằng MinHash nếu cần |
| **Benchmark** | MinHash 128 permutations = 95%+ recall cho near-duplicate detection |
| **Source** | [kapa.ai RAG Pipeline 2026](https://www.kapa.ai/blog/how-to-build-a-rag-pipeline-from-scratch-in-2026) |
| **Ragbot** | ✅ Done — `shared/hashing.py` → `content_hash_required()` |

### 53.2 Document Parsing

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Extract text + cấu trúc (headings, tables, images) từ PDF/HTML/DOCX. OCR cho scanned PDFs |
| **Best practice** | Layout-aware parser (Docling, Unstructured, Marker). Tables → markdown. Images → vision LLM description |
| **Benchmark** | Unstructured ~95% text accuracy (digital PDF), ~85% scanned. Marker ~92% structural fidelity |
| **Source** | [Unstructured docs](https://docs.unstructured.io), [Docling](https://github.com/DS4SD/docling), [Marker](https://github.com/VikParuchuri/marker) |
| **Ragbot** | ⚠️ Partial — `SimpleTextParser` chỉ basic paragraph splitting, no layout awareness, no OCR |
| **Gap** | **P1** — Integrate Docling hoặc Marker cho table/header extraction |

### 53.3 Document Cleaning

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Bỏ noise (headers, footers, boilerplate), normalize Unicode (NFKC), near-duplicate detection |
| **Best practice** | MinHash/SimHash cho near-dedup (Jaccard > 0.85), regex strip repeated elements |
| **Benchmark** | Cleaning giảm 10-30% text volume, cải thiện embedding quality |
| **Source** | [Databricks Quality Pipeline](https://docs.databricks.com/aws/en/generative-ai/tutorials/ai-cookbook/quality-data-pipeline-rag), [Elastic Advanced RAG](https://www.elastic.co/search-labs/blog/advanced-rag-techniques-part-1) |
| **Ragbot** | ⚠️ Partial — Compression có boilerplate removal nhưng chỉ ở query time, không ở ingest time |
| **Gap** | P2 — Thêm ingestion-time cleaning pipeline |

### 53.4 Metadata Extraction

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Trích xuất title, date, author, document type, key topics. LLM-based extraction cho accuracy cao |
| **Best practice** | LLM (Haiku/GPT-4o-mini) extract structured metadata → store trong DB và vector store. Cost ~$0.001/document |
| **Source** | [Production RAG Pipeline (Medium)](https://medium.com/@manoharallu03/production-grade-rag-pipeline-a-complete-implementation-guide-968cd1cfce79) |
| **Ragbot** | ⚠️ Partial — Basic metadata (chunk_index, document_name), GraphRAG entity extraction có nhưng limited |
| **Gap** | P2 — Thêm LLM-based metadata extraction |

### 53.5 Chunking Strategy Selection

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Chọn chunking strategy phù hợp dựa trên cấu trúc document (HDT, SEMANTIC implemented; PROPOSITION planned; HYBRID = recursive fallback) |
| **Best practice** | AdapChunk 3-layer pipeline: analyze_document → select_strategy → dispatch. (7-layer described in spec not fully implemented; confidence scoring planned) |
| **Benchmark** | Recursive 512 token = **69% accuracy** (#1 FloTorch). Semantic = 54% (over-segments). Adaptive = **87%** (Mayo Clinic, p=0.001) |
| **Source** | [FloTorch Benchmark](https://www.runvecta.com/blog/we-benchmarked-7-chunking-strategies-most-advice-was-wrong), [NVIDIA Benchmark](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/), [AdapChunk](https://docs.google.com/document/d/1EP6aHWnLgvszX-UWlgl8WQhb7RWuqgiG/edit) |
| **Ragbot** | ⚠️ Partial — `shared/chunking/` package (`analyze.py` analyze_document + select_strategy; `strategies.py` recursive/hdt/semantic/proposition/hybrid) + recursive fallback. PROPOSITION planned |

### 53.6 Chunking Execution

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Split document thành chunks, respect sentence boundaries, overlap, parent-child relationships |
| **Best practice** | 400-512 tokens, 10-20% overlap, sentence boundary respect. **Parent-child chunking** = #1 pattern cho production RAG 2026 (index nhỏ cho matching, trả lớn cho LLM) |
| **Benchmark** | Parent-child retrieval +10-20% answer accuracy. Sentence-boundary-aware giảm 25% "broken context" errors |
| **Source** | [LanceDB Parent Document](https://www.lancedb.com/blog/modified-rag-parent-document-bigger-chunk-retriever-62b3d1e79bc6), [Multi-Vector Indexing](https://dev.to/jamesli/optimizing-rag-indexing-strategy-multi-vector-indexing-and-parent-document-retrieval-49hf) |
| **Ragbot** | ✅ Done (chunking) / ❌ Missing (parent-child) |
| **Gap** | **P1** — Thêm parent-child chunking (child=256 tokens retrieval, parent=1024 context) |

### 53.7 Contextual Enrichment

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Prepend LLM-generated context prefix vào mỗi chunk trước khi embedding. Giúp chunk "biết" mình thuộc document nào |
| **Best practice** | Anthropic Contextual Retrieval — send full doc + chunk → LLM generate 2-3 câu context → prepend. Dùng prompt caching để giảm cost 50-90% |
| **Benchmark** | **-35%** retrieval failure (contextual alone), **-49%** (+ BM25), **-67%** (+ reranking). Cost: $1.02/M tokens |
| **Source** | [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval), [Claude Cookbook](https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide) |
| **Ragbot** | ✅ Done — `shared/contextual_enrichment.py` |
| **Gap** | **P1** — Chưa dùng prompt caching → cost enrichment có thể giảm 50-90% |

### 53.8 Embedding Generation

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Convert enriched chunks → dense vectors. Batch processing, model versioning, late chunking |
| **Best practice** | Batch 100-500 chunks. Late chunking (embed full doc → split at boundaries). Asymmetric embedding (query vs passage mode) |
| **Benchmark** | BGE-M3: nDCG@10 = **0.72** cho Vietnamese (MIRACL). OpenAI text-embedding-3-small: ~0.60-0.65. Fine-tuning +18.15% MAP |
| **Source** | [VN-MTEB](https://arxiv.org/html/2507.21500v1), [Jina Late Chunking](https://arxiv.org/abs/2409.04701), [Viblo Vietnamese Embedding](https://viblo.asia/p/so-sanh-cac-mo-hinh-embedding-cho-tieng-viet-qua-benchmark-2025-AoJe88G141j) |
| **Ragbot** | ✅ Done — batch processing, model versioning, late chunking (`shared/late_chunking.py`) |
| **Gap** | P2 — Chưa asymmetric embedding, chưa dùng BGE-M3 (đang dùng OpenAI) |

### 53.9 Index Storage (Hybrid)

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Store dense vectors (HNSW) + sparse index (BM25) + metadata. RRF fusion cho hybrid search |
| **Best practice** | HNSW cho < 10M vectors. **PostgreSQL BM25 extension** (pg_textsearch/VectorChord-BM25) thay ts_rank. Metadata pre-filtering |
| **Benchmark** | VectorChord-BM25 **3x faster** QPS (112 vs 49). Hybrid search **+10-25%** recall vs dense-only |
| **Source** | [pg_textsearch](https://github.com/timescale/pg_textsearch), [VectorChord-BM25](https://blog.vectorchord.ai/vectorchord-bm25-revolutionize-postgresql-search-with-bm25-ranking-3x-faster-than-elasticsearch), [ParadeDB Hybrid](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) |
| **Ragbot** | ⚠️ Partial — Hybrid search + HNSW + RRF done, nhưng dùng ts_rank thay vì BM25 thật |
| **Gap** | **P0** — Upgrade ts_rank → pg_textsearch hoặc VectorChord-BM25 |

### 53.10 Quality Validation

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Verify parsing/chunking/embedding quality sau ingest. Self-retrieval test, chunk coherence check |
| **Best practice** | Golden query test (generate 2-3 queries per doc, verify top-10). Empty chunk check. Vector norm validation |
| **Benchmark** | Hệ thống có validation phát hiện 15-20% documents gây degrade quality |
| **Source** | [BenchmarkQED (Microsoft)](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/) |
| **Ragbot** | ⚠️ Partial — Golden dataset có nhưng chưa automated per-document validation |
| **Gap** | P2 — Thêm automated quality check khi ingest |

## 54. QUERY PIPELINE — 14-16 bước chi tiết (configurable)

### 54.1 Input Guardrail

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Chặn query nguy hiểm/vô nghĩa: empty, injection, PII, SQL injection, secrets, too-short |
| **Source** | [LLM Guard](https://llmguard.com/), [Simon Willison Prompt Injection](https://simonwillison.net/series/prompt-injection/) |
| **Ragbot** | ✅ Done — `shared/local_guardrail.py`: empty, length, injection, PII, SQL, secrets, too_short (min 2 alphanumeric) |
| **Gap** | P2 — No toxicity detection, regex-only injection (chưa ML-based) |

### 54.2 Semantic Cache Check

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Tìm câu trả lời đã cache bằng hash exact match + cosine similarity. Tiết kiệm 61-73% cost |
| **Benchmark** | Redis semantic cache: **68.8% cost savings**, 61-73% hit rate |
| **Source** | [GPTCache](https://github.com/zilliztech/GPTCache), [Redis Semantic Cache](https://redis.io/blog/rag-at-scale/) |
| **Ragbot** | ✅ Done — 2-tier (hash + cosine), TTL, corpus_version invalidation |
| **Gap** | P2 — Indirect invalidation khi document thay đổi |

### 54.3 Query Understanding

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Classify intent (factoid, multi-hop, aggregation, comparison, out_of_scope), route qua pipeline phù hợp |
| **Source** | [Adaptive-RAG](https://arxiv.org/abs/2312.10997), [SetFit](https://huggingface.co/blog/setfit) |
| **Ragbot** | ✅ Done — 5 intents, adaptive routing, conservative default (out_of_scope on parse failure) |
| **Gap** | P2 — No confidence threshold cho routing decisions |

### 54.4 Query Preprocessing

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Vietnamese word segmentation (underthesea), diacritics normalization, teencode expansion |
| **Benchmark** | underthesea: 80% accuracy, **+32% BM25 recall** (MIRACL). Diacritic normalization critical cho "goi dau" → "gội đầu" |
| **Source** | [VnCoreNLP](https://github.com/vncorenlp/VnCoreNLP), [ViSoLex](https://arxiv.org/abs/2501.07020) |
| **Ragbot** | ⚠️ Partial — `shared/vi_tokenizer.py`: Vietnamese segmentation + `remove_diacritics()` done |
| **Gap** | **P1** — No abbreviation/teencode expansion ("ko" → "không", "BHXH" → "bảo hiểm xã hội") |

### 54.5 Query Rewriting

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | HyDE (Hypothetical Document Embedding) cho short queries. Multi-query decomposition cho complex queries |
| **Benchmark** | HyDE: **+15-25% retrieval** cho queries < 5 tokens. Multi-query: **+20% recall** nhưng **FAILS after reranking** (-3% Hit@10) |
| **Source** | [HyDE paper](https://arxiv.org/abs/2212.10496), [RAG-Fusion Fails (March 2026)](https://arxiv.org/abs/2603.02153) |
| **Ragbot** | ⚠️ Partial — HyDE done. Multi-query intentionally NOT implemented (fails after reranking per arXiv 2603.02153) |

### 54.6 Hybrid Retrieval

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Dense search (HNSW cosine) + Sparse search (BM25) → RRF fusion. Permission pre-filtering |
| **Benchmark** | Hybrid search **+10-25% recall** vs dense-only. RRF consistently outperforms linear combination |
| **Source** | [BGE-M3](https://arxiv.org/abs/2402.03367), [Superlinked Hybrid Search](https://superlinked.com/vectorhub/articles/optimizing-rag-with-hybrid-search-reranking) |
| **Ragbot** | ✅ Done — `infrastructure/vector/pgvector_store.py`: Dense + BM25 + RRF + permission filtering |

### 54.7 Reranking

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Cross-encoder rerank top-20 → top-5. Retrieval quality tăng đáng kể |
| **Benchmark** | Cohere reranking: **+48% retrieval quality**. Pattern: retrieve-20-rerank-to-5 |
| **Source** | [BGE-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3), [ColBERTv2](https://arxiv.org/abs/2112.01488), [ViRanker](https://arxiv.org/abs/2509.09131) |
| **Ragbot** | ✅ Done — `infrastructure/reranker/registry.py` Port+Registry: `zeroentropy_reranker.py` (default, `zerank-2` multilingual instruction-following), `jina_reranker.py`, `litellm_reranker.py` (Cohere/Voyage), `viranker_local_reranker.py` (BGE-M3 local), `null_reranker.py`. Active per-bot via `bot_model_bindings` purpose=`rerank`. |
| **Gap** | P2 — Per-bot `reranker_min_score` tuning still threshold-only (no calibrated score gate). |

### 54.8 Relevance Grading (CRAG)

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | CRAG pattern: grade retrieved chunks → nếu không đủ relevant thì retry hoặc return OOS answer |
| **Source** | [CRAG paper](https://arxiv.org/abs/2401.15884), [LangGraph CRAG](https://langchain-ai.github.io/langgraph/tutorials/rag/langgraph_crag/) |
| **Ragbot** | ✅ Done — `orchestration/query_graph.py`: CRAG-lite, min fallback score, retry, OOS answer khi retrieval fails |

### 54.9 Context Assembly

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Assemble retrieved chunks thành context block cho LLM. XML tags, citation whitelist, conversation history |
| **Best practice** | Đặt relevant chunks đầu + cuối context (avoid "Lost in the Middle" effect). XML sandboxing ngăn injection |
| **Source** | [Lost in the Middle](https://arxiv.org/abs/2307.03172), [Prompt Engineering for RAG](https://arxiv.org/abs/2312.16171) |
| **Ragbot** | ✅ Done — XML context blocks, citation whitelist, conversation history condensing |

### 54.10 Generation

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Generate answer từ context. Faithful (chỉ dùng context), cited (gắn source), low temperature |
| **Best practice** | Temperature 0.0-0.1. 5 mandatory rules: context-only, say "không biết", cite sources, no fabrication, Vietnamese response |
| **Source** | [Anthropic RAG Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/rag) |
| **Ragbot** | ⚠️ Partial — Citation enforcement + anti-hallucination prompt done, no streaming |
| **Gap** | **P1** — Temperature chưa explicit, no SSE streaming |

### 54.11 Output Guardrail

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Validate output: citation check, PII scan, hallucination detection (NLI), secret scan |
| **Source** | [RAGAS](https://docs.ragas.io/), [LLM Guard Output](https://llmguard.com/output_scanners/relevance) |
| **Ragbot** | ⚠️ Partial — Citation validation + secret scan done |
| **Gap** | **P1** — No hallucination detection (NLI-based), no output PII check |

### 54.12 Self-Reflection

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | LLM tự evaluate answer quality. Nếu không đạt → retry (max 2 lần). Skip cho factoid queries |
| **Source** | [Self-RAG paper](https://arxiv.org/abs/2310.11511) |
| **Ragbot** | ✅ Done — `orchestration/query_graph.py`: LLM evaluate, retry, max limit, skip cho factoid |

### 54.13 Cache Storage

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Lưu answer vào semantic cache. TTL per domain, invalidation khi corpus version thay đổi |
| **Source** | [GPTCache](https://github.com/zilliztech/GPTCache), [Redis RAG at Scale](https://redis.io/blog/rag-at-scale/) |
| **Ragbot** | ✅ Done — TTL, corpus_version, conditional store (chỉ cache khi answer quality đủ) |

### 54.14 Response Delivery

| Mục | Chi tiết |
|-----|----------|
| **Làm gì** | Trả answer cho user kèm sources, confidence. Lý tưởng: SSE streaming cho real-time UX |
| **Source** | [MDN Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) |
| **Ragbot** | ✅ Done — SSE streaming endpoint `POST /api/ragbot/chat/stream` (`interfaces/http/routes/chat_stream.py`) + `_sse_helper.stream_real_llm()` shared framing. Event types: `status` / `token` / `replace` (post-validation rewrite) / `done`. Same 4-key resolve + RBAC (`chat:stream`) as `/chat`. |
| **Note** | FE wire (web/Zalo channel) is the remaining UX gap — backend contract stable. See FE wire recommendation in `reports/CODER_S8_STREAMING_DOCS_20260512.md`. |

## 55. Code Mapping Scorecard — 24-step canonical (post Phase 2.4-2.12)

> **Replaces** the legacy I1-I10 + Q1-Q14 schema (14 step). Canonical 24-step
> matches `query_graph.py` + `application/services/document_service/` (package) since L7 (2026-04-29).

**Tổng kết Upload (7 step): 7/7 ✅ Done · Query (17 step): 16/17 ✅, 1 ⚠️ (Q15 false-positive fixed Phase 2.11)**

### Upload pipeline (U1-U7)

| # | Step | Status | File:line | Phase 2 fix | Priority |
|---|------|--------|-----------|-------------|----------|
| U1 | VALIDATE (size + dedup) | ✅ | `application/services/document_service/ingest_core.py::_IngestMixin.ingest()` | — | — |
| U2 | PARSE (mime → registry) | ✅ | `infrastructure/parser/registry.py` + adapters | — | — |
| U3 | CLEAN (NFC + injection strip) | ✅ | `shared/text_normalization.py` + `_clean_document_text` | — | — |
| U4 | CHUNK (strategy registry) | ✅ | `shared/chunking/` package (`__init__.smart_chunk()` + `strategies.py` + `analyze.py` + `csv_chunker.py` + `vn_structural.py`) | — | — |
| U5 | ENRICH (parent-child + CR) | ✅ | `application/services/contextual_chunk_enrichment.py` | — | — |
| U6 | VN SEGMENT (underthesea) | ✅ | `shared/vi_tokenizer.py` | — | — |
| U7 | EMBED + STORE | ✅ | `infrastructure/embedding/litellm_embedder.py` + `pgvector_store.upsert_chunks()` | **Phase 2.9**: raise on length mismatch (no NULL embedding) | — |

### Query pipeline (Q1-Q17)

| # | Step | Status | File:line | Phase 2 fix | Priority |
|---|------|--------|-----------|-------------|----------|
| Q1 | GUARD INPUT | ✅ | `query_graph.py:875` (guard_input) + `local_guardrail.check_input` | — | P2 (per-bot scope) |
| Q2 | CHECK CACHE | ✅ | `query_graph.py:915` (check_cache) | open: re-embed waste in persist (B-Z5-Q2-1) | P2 |
| Q3 | UNDERSTAND QUERY | ✅ | `query_graph.py:1025` (intent + condense merged) | open: VN labels in user content (B-Z5-Q3-1) | P2 |
| Q4 | REWRITE (HyDE) | ✅ | `query_graph.py` rewrite | — | — |
| Q5 | DECOMPOSE | ✅ | `query_graph.py` decompose_query | **Phase 2.12**: max_tokens forwarded | — |
| Q6 | RETRIEVE (hybrid + RRF) | ✅ | `query_graph.py:548` retrieve | — | — |
| Q7 | GRAPH RETRIEVE | ✅ | `query_graph.py` graph_retrieve | — | — |
| Q8 | FILTER (min-score) | ✅ | `query_graph.py` post-retrieve | — | — |
| Q9 | MMR DEDUP (pre-rerank) | ✅ | `query_graph.py` mmr_dedup | — | — |
| Q10 | RERANK (ZeroEntropy zerank-2 per-bot, post 2026-05-12 ZE migrate) | ✅ | `query_graph.py:1954` + `application/services/reranker_resolver.py` + `infrastructure/reranker/zeroentropy_reranker.py` | **Phase 2.5**: SQLAlchemyError catch · **ZE-migrate**: default rerank model `jina-reranker-v3` → `zerank-2` (`DEFAULT_RERANK_MODEL` constant) | — |
| Q11 | MMR DEDUP (post-rerank) | ✅ | `query_graph.py` mmr_dedup_post | — | — |
| Q12 | GRADE (CRAG) | ✅ | `query_graph.py` grade | **Phase 2.12**: max_tokens forwarded | — |
| Q13 | REWRITE RETRY | ✅ | `query_graph.py` rewrite_retry (max 2) | — | — |
| Q14 | GENERATE | ✅ | `query_graph.py:2440-2530` | **Phase 2.11**: history per-msg cap 800c + cite-marker strip · **Phase 2.12**: max_tokens forward | — |
| Q15 | GUARD OUTPUT | ✅ | `query_graph.py:2670-2740` + `local_guardrail.check_output` | **Phase 2.11**: shingle hash on platform-rules ONLY (no false-positive on persona) | — |
| Q16 | REFLECT | ✅ | `query_graph.py:2746` reflect | — | — |
| Q17 | PERSIST | ✅ | `query_graph.py:2819` persist | — | — |

### Phase 2 ship summary (24-step layer)

| Phase | Commit | Step affected | What changed |
|---|---|---|---|
| 2.4 | `7836cec` | post-Q17 (worker) | re-raise on handler error → no XACK loss → no job stuck "running" |
| 2.5 | `04affbd` | Q10 | reranker_resolver narrow except (SQLAlchemyError) + 5 zero-hardcode constants |
| 2.7 | `730d3d6` | post-Q17 (outbox) | RedisError/OSError/Timeout → retry+DLQ |
| 2.9 | `4004016` | U7 | raise on embed length mismatch (no silent NULL) |
| 2.11 | `45181b0` | Q14 + Q15 | persona-vs-platform shingle split + history cap + cite-marker strip |
| 2.12 | `0337b85` | Q5 + Q12 + Q14 (all LLM nodes) | forward `cfg.params.max_tokens` for ALL purposes |

## 56. Phase 4 Work Plan — Chi tiết từng task

> Mỗi task có: vấn đề hiện tại, giải pháp cụ thể, files cần sửa, acceptance criteria, dependencies, source tham khảo.

---

### 56.1 P0 — CRITICAL (làm ngay, block tất cả)

#### P0-1: Upgrade ts_rank → BM25 thật (pg_textsearch)

**Vấn đề hiện tại**: Sparse search dùng PostgreSQL `ts_rank(to_tsvector('simple', content), plainto_tsquery(...))` — chỉ có Term Frequency, KHÔNG có IDF (inverse document frequency), length normalization, TF saturation. Kết quả: document dài bị ưu tiên sai, rare terms không được boost.

**Giải pháp**: Install `pg_textsearch` extension (Timescale, Apache 2.0) hoặc `VectorChord-BM25` (TensorChord, Apache 2.0). Cả hai cung cấp BM25 scoring thật trong PostgreSQL, designed để hybrid với pgvector.

**Files cần sửa**:
- `src/ragbot/infrastructure/vector/pgvector_store.py:188-198` — thay CTE `sparse` dùng `ts_rank` bằng BM25 scoring function từ extension
- Migration mới: `CREATE EXTENSION IF NOT EXISTS pg_textsearch` hoặc `vectorchord_bm25`
- `scripts/init_system_config.py` — thêm `bm25_k1` (default 1.2), `bm25_b` (default 0.75) vào system_config

**Cách làm cụ thể**:
1. Chọn extension: `pg_textsearch` (recommended — designed cho hybrid với pgvector, 2.4-6.5x faster)
2. Install extension trên server: `apt install postgresql-16-pg-textsearch` hoặc build from source
3. Migration: `CREATE EXTENSION pg_textsearch; CREATE INDEX idx_chunks_bm25 ON document_chunks USING bm25(content);`
4. Sửa sparse CTE trong `pgvector_store.py`: thay `ts_rank(...)` bằng `bm25_score(content, :query)` (exact syntax phụ thuộc extension)
5. Config: thêm `bm25_k1`, `bm25_b` vào `system_config` để tunable

**Acceptance criteria**:
- [ ] Sparse search dùng BM25 scoring thay vì ts_rank
- [ ] BM25 parameters (k1, b) configurable qua system_config
- [ ] Hybrid search quality tăng (verify trên golden dataset)
- [ ] 165 tests vẫn pass

**Dependencies**: Server cần install PostgreSQL extension (check compatibility với PostgreSQL version hiện tại)

**Benchmark**: VectorChord-BM25: 3x faster QPS (112 vs 49). pg_textsearch: 2.4-6.5x faster ở 138M scale.

**Source**: [pg_textsearch GitHub](https://github.com/timescale/pg_textsearch), [VectorChord-BM25 Blog](https://blog.vectorchord.ai/vectorchord-bm25-revolutionize-postgresql-search-with-bm25-ranking-3x-faster-than-elasticsearch), [BM25 in Postgres](https://www.tigerdata.com/blog/you-dont-need-elasticsearch-bm25-is-now-in-postgres)

**Effort**: 2 ngày

---

### 56.2 P1 — IMPORTANT (sprint tiếp theo)

#### P1-1: Layout-aware Document Parser

**Vấn đề hiện tại**: `SimpleTextParser` chỉ extract text bằng pypdfium2 + paragraph splitting. Không nhận diện tables, headers, images, multi-column layouts. Tables bị flatten thành text vô nghĩa.

**Giải pháp**: Integrate Docling (IBM, open-source) hoặc Marker (VikParuchuri) làm parser chính. Docling mạnh cho tables + academic papers. Marker mạnh cho PDF → Markdown preserve structure.

**Files cần sửa**:
- `src/ragbot/infrastructure/ocr/simple_text_parser.py` — thêm Docling adapter, fallback về simple nếu Docling không available
- `src/ragbot/application/services/document_service/` package — routing parser dựa trên doc type
- `pyproject.toml` — thêm `docling` dependency (optional)
- `system_config` — thêm `parser_engine` key (default: "docling", fallback: "simple")

**Cách làm cụ thể**:
1. Install Docling: `pip install docling`
2. Tạo `DoclingParser` class implement cùng interface `SimpleTextParser`
3. Tables → Markdown tables (Docling output native). Images → skip hoặc OCR description
4. Headers → preserve hierarchy cho AdapChunk HDT strategy
5. Fallback: nếu Docling fail → dùng SimpleTextParser

**Acceptance criteria**:
- [ ] PDF có tables → extract thành markdown tables chính xác
- [ ] PDF có headers → preserve heading hierarchy cho chunking
- [ ] Parser configurable qua system_config
- [ ] Fallback khi Docling unavailable

**Benchmark**: Docling ~95% accuracy digital PDFs. Tables: 80-90% depending on complexity.

**Source**: [Docling GitHub](https://github.com/DS4SD/docling), [Marker GitHub](https://github.com/VikParuchuri/marker), [Unstructured docs](https://docs.unstructured.io)

**Effort**: 3 ngày

---

#### P1-2: Parent-Child Chunking (Small-to-Big Retrieval)

**Vấn đề hiện tại**: Chunking tạo flat chunks — khi retrieve, LLM chỉ thấy đoạn nhỏ mà không có context xung quanh. Đặc biệt yếu với documents dài (regulations, policies) cần nhiều context.

**Giải pháp**: Index child chunks nhỏ (256 tokens) cho retrieval precision, nhưng khi retrieve trả về parent chunk lớn (1024 tokens) cho LLM. Child → precise embedding matching. Parent → đủ context cho LLM generate.

**Files cần sửa**:
- `src/ragbot/shared/chunking/` package — thêm `generate_parent_child_chunks()`: split thành parents, mỗi parent split thành children
- `src/ragbot/application/services/document_service/` package — khi ingest, embed CẢ child lẫn parent. Vector search trên children, trả về parent.
- Migration: `ALTER TABLE document_chunks ADD COLUMN parent_chunk_id UUID REFERENCES document_chunks(id);`
- `src/ragbot/orchestration/query_graph.py` retrieve node — khi retrieve, dùng child matches để tìm parent chunks, trả parent cho downstream

**Cách làm cụ thể**:
1. Chunking: parent = 1024 tokens, child = 256 tokens, child overlap = 50 tokens
2. Mỗi child có FK `parent_chunk_id` → parent
3. Embed children (nhỏ → precise). Parents cũng embed (backup search)
4. Retrieve node: search children → group by parent → trả parent chunks (deduplicated)
5. Config: `parent_child_enabled` (bool, default: true), `parent_chunk_size` (int), `child_chunk_size` (int)

**Acceptance criteria**:
- [ ] Documents > 2000 tokens tạo parent-child hierarchy
- [ ] Retrieve trả parent chunks (chứa context rộng hơn)
- [ ] Children dedup theo parent (không trả 3 children từ cùng 1 parent)
- [ ] Backwards compatible: documents cũ vẫn hoạt động (parent_chunk_id = NULL)

**Benchmark**: +10-20% answer accuracy trên long-document queries.

**Source**: [LanceDB Small-to-Big](https://www.lancedb.com/blog/modified-rag-parent-document-bigger-chunk-retriever-62b3d1e79bc6), [DEV.to Multi-Vector](https://dev.to/jamesli/optimizing-rag-indexing-strategy-multi-vector-indexing-and-parent-document-retrieval-49hf), [LangCopilot Guide](https://langcopilot.com/posts/2025-10-11-document-chunking-for-rag-practical-guide)

**Effort**: 2 ngày

---

#### P1-3: Prompt Caching cho Contextual Enrichment

**Vấn đề hiện tại**: `contextual_enrichment.py` gọi LLM riêng lẻ cho TỪNG chunk để generate context prefix. Mỗi call gửi full document + chunk → wasteful vì full document lặp lại mỗi chunk.

**Giải pháp**: Dùng Anthropic prompt caching — load document vào cache 1 lần, reference cached content cho mỗi chunk. Giảm cost 50-90%, giảm latency 2x.

**Files cần sửa**:
- `src/ragbot/shared/contextual_enrichment.py` — refactor: batch xử lý chunks cùng document, system prompt + document content ở đầu (cached), chỉ chunk content thay đổi
- `system_config` — thêm `enrichment_use_cache` (bool, default: true)

**Cách làm cụ thể**:
1. Group chunks theo document_id
2. Gọi LLM với `cache_control` header cho system prompt + document content (Anthropic API)
3. Mỗi chunk chỉ thay phần user message (chunk content) → cache hit cho system + document
4. Nếu dùng OpenAI thay Anthropic: structured prompts với stable prefix cũng trigger automatic caching

**Acceptance criteria**:
- [ ] Enrichment cost giảm >= 50% (verify qua token usage logs)
- [ ] Quality enrichment output không thay đổi
- [ ] Fallback khi caching unavailable

**Benchmark**: Anthropic prompt caching: latency -2x, cost -90%. OpenAI auto caching: cost -50%.

**Source**: [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval), [Claude Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)

**Effort**: 1 ngày

---

#### P1-4: Vietnamese Abbreviation + Teencode Dictionary

**Vấn đề hiện tại**: User gõ "ko biet bhxh la gi" → pipeline search nguyên "ko", "bhxh" → miss documents chứa "không biết", "bảo hiểm xã hội". Không có bước expand abbreviations/teencode.

**Giải pháp**: Thêm abbreviation dictionary vào system_config (zero hardcode). Expand tại query preprocessing step trước retrieval.

**Files cần sửa**:
- `src/ragbot/shared/vi_tokenizer.py` — thêm `expand_abbreviations(text, abbrev_dict)` và `normalize_teencode(text, teencode_dict)`
- `src/ragbot/orchestration/query_graph.py` — gọi normalize trước retrieve node (sau condense, trước rewrite)
- `scripts/init_system_config.py` — seed `vietnamese_abbreviations` (JSON dict) và `vietnamese_teencode` (JSON dict)

**Cách làm cụ thể**:
1. Abbreviation dict (system_config, JSON): `{"bhxh": "bảo hiểm xã hội", "gds": "giáo dục sớm", "ubnd": "ủy ban nhân dân", "sdt": "số điện thoại", ...}` — khoảng 50-100 entries phổ biến
2. Teencode dict: `{"ko": "không", "dc": "được", "mk": "mình", "ns": "nói", "tks": "cảm ơn", "j": "gì", "đc": "được", "k": "không", ...}` — khoảng 100-200 entries
3. Normalize: scan query, replace toàn bộ token matches, case-insensitive
4. Giữ cả original query + normalized query → search cả hai (như diacritics dual-search)

**Acceptance criteria**:
- [ ] "ko biet bhxh la gi" → expand thành "không biết bảo hiểm xã hội là gì"
- [ ] Dict configurable qua system_config (admin thêm/bớt không cần code)
- [ ] Không break queries bình thường (expand chỉ khi match whole word)

**Source**: [ViSoLex](https://arxiv.org/abs/2501.07020), [ViSoLex GitHub](https://github.com/HaDung2002/visolex), [Vietnamese Abbreviations](https://howtovietnamese.com/vietnamese-text-abbreviations-slang/)

**Effort**: 1 ngày

---

#### P1-5: Generation Temperature = 0.1

**Vấn đề hiện tại**: Generate node không set temperature explicit → có thể dùng default model temperature (thường 0.7-1.0), gây non-deterministic answers và hallucination risk cao hơn.

**Giải pháp**: Set temperature = 0.1 cho generate node. Giữ temperature hiện tại cho các node khác (router, rewrite — cần creativity).

**Files cần sửa**:
- `src/ragbot/orchestration/query_graph.py` — trong `_invoke_llm_node()`, thêm temperature override khi purpose = "generation"
- `system_config` — thêm `generation_temperature` (float, default: 0.1)

**Cách làm cụ thể**:
1. Trong `_invoke_llm_node()`: check `purpose == "generation"` → set `temperature = _pcfg(state, "generation_temperature", 0.1)`
2. Các purpose khác (routing, grading, reflection) giữ temperature hiện tại

**Acceptance criteria**:
- [ ] Generate node dùng temperature 0.1
- [ ] Temperature configurable qua system_config
- [ ] Answers deterministic hơn (cùng query → cùng answer)

**Benchmark**: Temperature 0.0-0.1 cải thiện faithfulness 10-15% so với 0.7.

**Source**: [Anthropic RAG Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/rag)

**Effort**: 30 phút

---

#### P1-6: Output Hallucination Detection

**Vấn đề hiện tại**: Output guardrail chỉ check citation markers + secret scan. KHÔNG verify câu trả lời có grounded trong context hay không. LLM có thể fabricate facts dù context không chứa.

**Giải pháp**: Thêm NLI (Natural Language Inference) check — verify mỗi claim trong answer được supported bởi retrieved context.

**Files cần sửa**:
- `src/ragbot/infrastructure/guardrails/local_guardrail.py` — thêm `OutputGuardrail.grounding_check(answer, context_chunks)` dùng LLM lightweight
- `src/ragbot/orchestration/query_graph.py` guard_output node — gọi grounding check
- `system_config` — `grounding_check_enabled` (bool, default: false ban đầu, bật sau khi test)

**Cách làm cụ thể**:
1. Extract claims từ answer (split thành sentences)
2. Cho mỗi sentence (hoặc batch), hỏi LLM mini: "Sentence X có được support bởi context Y không? YES/NO"
3. Nếu > 30% sentences không supported → flag answer, có thể trigger regenerate hoặc warning
4. Dùng model rẻ (GPT-4o-mini / Haiku) cho check — cost ~$0.001/query
5. Feature flag: tắt cho MVP, bật khi stable

**Acceptance criteria**:
- [ ] Detect khi answer chứa facts không có trong context
- [ ] Feature flag on/off
- [ ] Latency thêm < 500ms
- [ ] Không false-positive trên general knowledge statements ("chào bạn", greetings)

**Source**: [RAGAS Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/), [LLM Guard Output](https://llmguard.com/output_scanners/relevance)

**Effort**: 2 ngày

---

#### P1-7: SSE Streaming Response

**Vấn đề hiện tại**: Chat API trả full response 1 lần sau khi generate xong (2-10s). User stare vào blank screen chờ đợi. UX kém, đặc biệt cho web/Zalo channels.

**Giải pháp**: Thêm SSE (Server-Sent Events) streaming — trả từng token khi LLM generate, user thấy answer xuất hiện real-time.

**Files cần sửa**:
- `src/ragbot/orchestration/query_graph.py` generate node — dùng `astream` thay `acompletion` khi streaming enabled
- `src/ragbot/interfaces/http/routes/test_chat/` package (`chat_routes.py`) — endpoint `POST /test/chat/stream` trả `StreamingResponse` SSE
- `src/ragbot/interfaces/workers/chat_worker/` package (`pipeline.py`) — hỗ trợ streaming mode cho production
- `system_config` — `streaming_enabled` (bool, default: true)

**Cách làm cụ thể**:
1. LiteLLM hỗ trợ `acompletion(stream=True)` → trả async iterator
2. Tạo `StreamingResponse(media_type="text/event-stream")` trong FastAPI
3. Format: `data: {"token": "...", "done": false}\n\n` cho mỗi chunk
4. Final message: `data: {"token": "", "done": true, "sources": [...], "duration_ms": N}\n\n`
5. Post-generation steps (guardrail, reflection, cache) chạy sau khi stream xong

**Acceptance criteria**:
- [ ] First token latency < 500ms
- [ ] Answer stream real-time
- [ ] Sources trả ở cuối stream
- [ ] Non-streaming mode vẫn hoạt động (backwards compatible)
- [ ] Citation validation chạy sau stream complete

**Source**: [MDN Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events), [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)

**Effort**: 2 ngày

---

### 56.3 P2 — ENHANCEMENT (backlog)

| # | Task | Mô tả chi tiết | Source | Effort |
|---|------|----------------|--------|--------|
| P2-1 | **BGE-M3 Embedding** | Thay OpenAI text-embedding-3-small (nDCG@10 ~0.60) bằng BGE-M3 (nDCG@10 = 0.72 cho Vietnamese). Self-host hoặc API. Cần re-embed toàn bộ corpus. | [VN-MTEB](https://arxiv.org/html/2507.21500v1), [Viblo](https://viblo.asia/p/so-sanh-cac-mo-hinh-embedding-cho-tieng-viet-qua-benchmark-2025-AoJe88G141j) | 2 ngày |
| P2-2 | **ViRanker / ZeroEntropy alt** | Reranker registry now includes `ZeroEntropy zerank-2` (default since 2026-05-12), `Jina v3`, `ViRanker` BGE-M3 local (NDCG@3 = 0.6815 on MMARCO-VI), `LiteLLM` (Cohere/Voyage), `Null`. Per-bot active via `bot_model_bindings`. Self-host ViRanker still available for Vietnamese-heavy tenants. | [ViRanker](https://arxiv.org/abs/2509.09131) | shipped |
| P2-3 | **LazyGraphRAG** | Thay full GraphRAG (đắt, indexing cost cao) bằng LazyGraphRAG (Microsoft) — indexing cost = vector RAG, query cost 700x cheaper, quality tương đương. | [Microsoft LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) | 3 ngày |
| P2-4 | **Ingestion Cleaning** | Thêm cleaning pipeline ở ingest time: strip repeated headers/footers, normalize Unicode NFKC, remove boilerplate. Hiện chỉ clean ở query time (compression). | [Databricks Pipeline](https://docs.databricks.com/aws/en/generative-ai/tutorials/ai-cookbook/quality-data-pipeline-rag) | 1 ngày |
| P2-5 | **LLM Metadata Extraction** | Dùng LLM mini extract structured metadata (title, date, author, topics) từ first 2 pages. Store trong cả DB và vector payload. Cost ~$0.001/doc. | Production best practice ([kapa.ai](https://www.kapa.ai/blog/how-to-build-a-rag-pipeline-from-scratch-in-2026)) | 1 ngày |
| P2-6 | **Dynamic Cutoff (Autocut)** | Thay fixed top-K bằng dynamic cutoff dựa trên score distribution. Nếu score cliff sau result #3 → chỉ trả 3. Giảm noise cho LLM. | Weaviate autocut pattern | 1 ngày |
| P2-7 | **Asymmetric Embedding** | Dùng separate query/passage modes (Jina v3 `retrieval.query` vs `retrieval.passage`). Passage embed at ingest, query embed at search. +5-10% matching. | [Jina v3](https://jina.ai/news/late-chunking-in-long-context-embedding-models/) | 1 ngày |
| P2-8 | **Ingestion Validation** | Auto-generate 2-3 test queries per document sau ingest. Verify chunks appear in top-10. Flag documents có parse issues. | [BenchmarkQED](https://github.com/microsoft/benchmark-qed) | 2 ngày |
| P2-9 | **Reranker Min Threshold** | Thêm minimum relevance score cho reranker output. Chunks dưới threshold bị drop trước grading. Configurable qua system_config. | Best practice (Perplexity, Cohere) | 30 phút |
| P2-10 | **ML Diacritic Restoration** | Transformer model restore diacritics: "goi dau" → "gội đầu" (94-98% accuracy). Dùng làm THÊM 1 search path, không thay original query. | [Transformer](https://github.com/duongntbk/restore_vietnamese_diacritics), [TDP 98.37%](https://aclanthology.org/2020.paclic-1.9.pdf) | 2 ngày |

---

### 56.4 Timeline tổng hợp

```
P0 (2 ngày):    ████ BM25 upgrade
P1 (12 ngày):   ████████████████████████
                 P1-1 Parser    (3d)
                 P1-2 Parent    (2d)
                 P1-3 Cache     (1d)
                 P1-4 Abbrev    (1d)
                 P1-5 Temp      (0.5d)
                 P1-6 Halluc    (2d)
                 P1-7 Stream    (2d)
P2 (15 ngày):   (backlog, pick when ready)
─────────────────────────────────────────
Total P0+P1:    ~14 ngày (2-3 tuần sprint)
```

### 56.5 Dependencies

```
P0-1 (BM25) ─── không dependency, làm trước
P1-1 (Parser) ── không dependency
P1-2 (Parent) ── không dependency, nhưng nên làm SAU P1-1 (parser output tốt hơn → chunking tốt hơn)
P1-3 (Cache) ─── không dependency
P1-4 (Abbrev) ── không dependency
P1-5 (Temp) ──── không dependency, làm ngay (30 phút)
P1-6 (Halluc) ── không dependency
P1-7 (Stream) ── không dependency, nhưng nên làm SAU P1-5 (temperature fix cho generate node ổn trước)
P2-1 (BGE-M3) ── SAU P0-1 (BM25 phải ổn trước khi đổi embedding)
P2-3 (LazyGR) ── thay thế GraphRAG hiện tại
```

---

## 57. Sprint 7+8 code deltas (added 2026-04-28)

### 57.1 — Sprint 7 F1: CSV chunking + zero-hardcode sweep
- **Code**: `src/ragbot/shared/chunking/csv_chunker.py` — `_is_table_line()` detect CSV (≥2 commas + no sentence punctuation).
- **Constants**: `src/ragbot/shared/constants.py` — 10 magic numbers lifted.
- **Config seed**: `parent_child_enabled=true`, `enrichment_max_concurrency=5`.
- **Test**: `tests/unit/test_s7_csv_chunking.py` (5 tests).

### 57.2 — Sprint 7 F2: Docs-Only STRICT
- **Code**: `src/ragbot/orchestration/query_graph.py:1535-1549` — `generate()` prepend `docs_only_strict_rule`.
- **LangPack**: VI + EN.
- **Config**: `DEFAULT_DOCS_ONLY_STRICT_ENABLED=True`. Per-bot override `plan_limits.docs_only_strict_enabled`.
- Composes với P29-A math lockdown.
- **Test**: `tests/unit/test_s7_docs_only_strict.py` (3 tests).

### 57.3 — Sprint 7 F4: Chunk audit log
- **Code**: `query_graph.py` `rerank()` emit `retrieval_chunks_debug` structured log.
- **Trigger**: `DEBUG_RETRIEVAL=true` env OR `state["debug_full"]`.

### 57.4 — Sprint 8 A: P34-B strategy weights
- **Code**: 14 coefficients + 10 norm thresholds → `DEFAULT_STRATEGY_WEIGHTS` dict (byte-identical refactor).

### 57.5 — Sprint 8 B: δ1 raw_content column
- **Migration**: `alembic/versions/0040_*.py` — add `raw_content` column.
- **Use**: HARN-3 opt-in chunk-content payload cho LLM judge.

### 57.6 — Sprint 8 E: pg extension preflight
- **Startup check**: `pg_trgm` 1.6 + `unaccent` 1.1 + `pgvector` ≥ 0.7.
- **`pg_textsearch`**: chưa cần (P15-1 dropped).

---

## 58. Brutal-audit gaps need fix (2026-04-28)

Xem [SPRINT9_AUDIT_VERDICT §G.1](../../reports/SPRINT9_AUDIT_VERDICT.md) cho full list. Top 3 priority Sprint 9:

1. **Integration test count** claim 636 thực 627 — sửa STATE_SNAPSHOT.
2. **4 model names hardcoded** (`document_service/` package, `litellm_embedder.py:31`) — vi phạm zero-hardcode rule.
3. **Reranker silent disabled** — fail-loud thay silent fallback khi `reranker_enabled=true` + key missing.

---

## 59. graph_assembly canonical DI — transport parity (W1-DI, added 2026-06-10)

> Root cause: 4 production callsite của `get_graph` mỗi cái tự hand-roll kwargs + initial `GraphState` → drift. `get_graph` là first-caller-wins (`query_graph.py:8062-8078`, singleton `:8058`) nên transport warm-up đầu tiên quyết deps cho cả process; SSE state thiếu key graph deref trực tiếp (persist subscript `workspace_id`). Stance EVOLVE: KHÔNG đụng singleton/`build_graph` signature — chỉ sửa **callsite assembly**.

### 59.1 — Shared assembly module
- **Code**: `src/ragbot/orchestration/graph_assembly.py` (module mới, không import node nội bộ `query_graph.py`):
  - `build_graph_di_kwargs(container)` (`:92`) — canonical kwarg set lấy từ `inspect.signature(build_graph)` (`:63`, không drift được khỏi engine); required dep (`GRAPH_DI_REQUIRED` frozenset `:45`: llm/model_resolver/invocation_logger/guardrail/vector_store/embedder) fail → `GraphAssemblyError` (route map 503); optional dep → None + warning `graph_di_optional_dep_unavailable` (`:83`); emit 1 event `graph_di_assembled none_deps=[...]` (`:121`) → warm-up thiếu gì THẤY được, hết silent.
  - `resolve_kg_service(pipeline_config)` (`:125`) — `KnowledgeGraphService()` khi `graph_rag_mode != "disabled"`, NHẤC NGUYÊN VĂN từ worker → mọi transport honor per-bot GraphRAG giống nhau.
  - `build_chat_initial_state(...)` (`:136`) — canonical `GraphState` đủ key worker đang set gồm `workspace_id`/`user_groups`/`bot_extra_output_tokens_per_response`; `tokens={"prompt":0,"completion":0,"cached":0}`. Transport-specific key (`_stream_sink`, `bypass_cache`) do caller bổ sung SAU.

### 59.2 — 4 callsite dùng chung builder
| Callsite | `get_graph` DI | initial_state | kg_service |
|---|---|---|---|
| chat_worker (async 202) | `chat_worker/pipeline.py` | `chat_worker/pipeline.py` | `chat_worker/pipeline.py` |
| chat_stream (SSE prod) | `chat_stream.py:239` | `:287` | `:306` |
| test_chat sync (demo) | `test_chat/chat_routes.py` | `test_chat/chat_routes.py` | `test_chat/chat_routes.py` |
| test_chat stream (demo) | `test_chat/chat_routes.py` | `test_chat/chat_routes.py` | `test_chat/chat_routes.py` |

- **Singleton get_graph GIỮ NGUYÊN**: `get_graph(**build_graph_di_kwargs(container))` — first-caller-wins + ignore-kwargs-after-first-build semantics không đổi; shared builder làm divergence = 0 (mọi callsite cùng 1 kwarg-set) nên singleton luôn close-over cùng handle = tiền đề an toàn của docstring `:8065-8070` được thoả thật.
- **kg_service parity**: stream + test_chat trước hardcode `None` (P2-A 🐛-2 mìn chờ flip GraphRAG); nay dùng chung `resolve_kg_service(pipeline_config)`.
- **Fail-loud ở builder, KHÔNG ở engine**: `GraphAssemblyError` raise trong builder (prod path duy nhất) — giữ test-mode minimal-kwargs hợp lệ không vỡ.

---
