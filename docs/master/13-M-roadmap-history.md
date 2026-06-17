# PHẦN M — LỊCH SỬ ROADMAP & KẾT QUẢ (v1.5 — VERSION 1 RAG, 2026-04-28)

> Target: Multi-domain RAG chatbot platform (domain-neutral) production-grade.
> Stack: Python/FastAPI, LangGraph 14-16 node pipeline, pgvector + PostgreSQL + Redis Streams, LiteLLM (per-purpose model binding qua system_config DB).
> **Kết quả VERSION 1 (post Sprint 10, 2026-04-28)**: brutal-honest **7.5/10** (single-tenant pilot 8.5/10, multi-tenant customer-facing pending RBAC). 772 active tests, 46 migrations.
> Score 8.5 trong v1.2 đã được brutal-audit pass 2 điều chỉnh xuống 6.0 → tăng lại 7.5 sau Sprint 9-10 ship multi-agent.

## 63. Phase 1: Intelligence (7/7 ✅ HOÀN THÀNH)

**Focus**: Make the bot smart enough to handle ALL domains correctly.
**Score**: 7.3 → 8.5 (+1.2 points) | **Effort**: ~44 hours

| # | Feature | Impact | Key Files |
|---|---------|--------|-----------|
| 1.1 | **Cross-Encoder Reranker** — Cohere rerank-v3.5, retrieve-20-rerank-to-5 | +48% retrieval quality ([Cohere benchmark](https://docs.cohere.com/docs/reranking-with-cohere)) | `infrastructure/reranker/cross_encoder.py`, `orchestration/query_graph.py` |
| 1.2 | **AdapChunk** — 2 strategies implemented (HDT + paragraph-based), PROPOSITION planned, HYBRID = recursive fallback + LLM selector + rule cross-check | 87% vs 13% fixed chunking ([Mayo Clinic, p=0.001](https://pmc.ncbi.nlm.nih.gov/articles/PMC12649634/)) | `shared/chunking/` package (`strategies.py` + `analyze.py`) |
| 1.3 | **Late Chunking** (Jina) — embed full doc → split at boundaries | +24.47% retrieval ([Jina paper](https://arxiv.org/abs/2409.04701)) | `shared/late_chunking.py`, `infrastructure/embedding/litellm_embedder.py` |
| 1.4 | **Contextual Enrichment** (Anthropic) — LLM context prefix per chunk | -67% retrieval failure ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)) | `shared/contextual_enrichment.py` |
| 1.5 | **Adaptive Query Routing** — classify intent, route pipeline | -35% latency, -28% cost, +8% accuracy | `orchestration/query_graph.py`, `orchestration/state.py` |
| 1.6 | **HyDE** — hypothetical doc embedding cho short Vietnamese queries | +10-20% retrieval cho < 5 tokens ([HyDE paper](https://arxiv.org/abs/2212.10496)) | `orchestration/query_graph.py`, `shared/vi_tokenizer.py` |
| 1.7 | **RAG-Fusion: DO NOT IMPLEMENT** — architectural decision | Multi-query FAILS after reranking -3% Hit@10 ([arXiv 2603.02153](https://arxiv.org/abs/2603.02153)) | N/A |

## 64. Phase 2: Cost (5/5 ✅ HOÀN THÀNH)

**Focus**: Reduce cost per query from ~$0.005 to $0.002-0.003 while maintaining quality.
**Score**: 8.5 → 9.0 (+0.5 points) | **Effort**: ~27 hours

| # | Feature | Savings | Source |
|---|---------|---------|--------|
| 2.1 | **Semantic Cache** — 2-tier (hash + cosine), TTL, corpus_version | 68.8% cost savings, 61-73% hit rate | [Redis benchmark](https://redis.io/blog/rag-at-scale/) |
| 2.2 | **OpenAI Prompt Caching** — structure system prompts for auto-cache | 50% discount on cached tokens | [OpenAI](https://openai.com/index/api-prompt-caching/) |
| 2.3 | **LLMLingua Compression** — compress context before LLM | 80% token reduction, 95-98% accuracy | [LLMLingua (Microsoft)](https://arxiv.org/abs/2310.05736) |
| 2.4 | **Model Routing** — simple → mini, complex → full model | 30-70% cost reduction | Industry benchmark |
| 2.5 | **BGE-M3 Evaluation** — benchmark vs text-embedding-3-small | nDCG@10 = 0.72 Vietnamese (MIRACL) | [VN-MTEB](https://arxiv.org/html/2507.21500v1) |

**Kết quả**: ~$0.002-0.003/query (industry average: $0.005/query).

## 65. Phase 3: Excellence (7/7 ✅ HOÀN THÀNH)

**Focus**: Production-grade quality monitoring, multi-domain optimization, automated evaluation.
**Score**: 9.0 → 8.5 (honest re-assessment) | **Effort**: ~66 hours

| # | Feature | Why | Key Files |
|---|---------|-----|-----------|
| 3.1 | **Golden Dataset + RAGAS** — 50-100 questions, 10 categories, automated scoring | "No universal best pipeline — evaluate on YOUR data" ([AutoRAG](https://github.com/Marker-Inc-Korea/AutoRAG)) | Golden dataset config, evaluation scripts |
| 3.2 | **Quality Dashboard** — retrieval recall, faithfulness, cache hit, cost, latency | Prevents regression, enables data-driven decisions | Dashboard endpoints |
| 3.3 | **Whole-Document Context** — skip chunking for < 2,000 token docs | NotebookLM pattern: whole-doc > chunked for small docs | `application/use_cases/ingest_document.py` |
| 3.4 | **Permission Pre-Filter** — tenant + role filter BEFORE vector search | Glean: permission filtering = table-stakes, must be pre-filter | `infrastructure/vector/pgvector_store.py` |
| 3.5 | **Incremental Re-Indexing** — Merkle tree change detection | Cursor pattern: avoid redundant embedding computation (-80% re-embed cost) | `application/use_cases/ingest_document.py` |
| 3.6 | **GraphRAG Conditional** — optional per domain, enabled via bot config | +85.7% multi-hop, -13.4% factoid ([Microsoft Research](https://arxiv.org/html/2506.05690v3)) | GraphRAG modules |
| 3.7 | **CI Gating** — block deploys if RAGAS scores drop below thresholds | Faithfulness >= 0.85, Context Recall >= 0.85 | CI pipeline config |

### Multi-Domain Strategy

| Domain | Chunking | GraphRAG | Lý do |
|--------|----------|----------|-------|
| **Spa** | HDT (tables) + whole-doc cho menus | ❌ DISABLE | Queries đơn giản, GraphRAG -13.4% |
| **Education** | SEMANTIC (paragraph-boundary, section-aware) | ✅ ENABLE | Multi-hop queries (prerequisites, policies) |
| **Finance** | PROPOSITION (PLANNED — currently uses HDT) | ✅ ENABLE | Legal accuracy, entity chains |
| **Environment** | RECURSIVE fallback (data + text) | ⚠️ CONDITIONAL | Chỉ enable cho regulation chains |

## 66. Phase 4: Status & Known Gaps (PENDING)

Phase 4 work plan defined in Phan K (section 56) but NOT yet implemented. Key pending items:

| Priority | Task | Status |
|----------|------|--------|
| P0-1 | BM25 that (pg_textsearch thay ts_rank) | NOT STARTED |
| P0-2 | Parent-Child Chunking | Code exists, **OFF by default** (`parent_child_enabled = false`) |
| P1-1 | Docling Parser | NOT STARTED |
| P1-2 | Generation Temperature tuning | NOT STARTED |
| P1-3 | Output Guardrail (hallucination detect) | NOT STARTED |
| P1-4 | Vietnamese Abbreviation Dict | NOT STARTED |
| P1-5 | SSE Streaming | NOT STARTED |
| P2-6 | Dynamic Cutoff (Autocut) | NOT STARTED (`autocut_enabled = false`) |
| P2-7 | Metadata Extraction | Code exists, **OFF by default** (`metadata_extraction_enabled = false`) |

**Honest assessment**: Phase 1-3 specs are COMPLETE in code, but Phase 4 is 0% implemented. Score reflects this: 8.5/10.

---

## 67. Audit Results (2026-04-20)

Audit toàn bộ codebase, fix 20+ issues across 3 severity levels:

**CRITICAL fixes**:
- Fix 8 config key mismatches (rag_embedding_dimension → embedding_dimension, etc.)
- Add 8 missing `system_config` keys (100+ total): golden_dataset_*, quality_dashboard_*, short_query_*, semantic_cache_ttl
- Fix GraphRAG circular reference prevention + self-loop skip + early break
- Fix background task safety: `create_task` + `add_done_callback` error logging

**HIGH fixes**:
- Path traversal validation cho bot_id/channel_type trong file operations
- Triple validation: max length, self-reference skip, batch dedup
- `unicodedata.normalize('NFC')` cho Vietnamese entity matching (11 locations)
- Graph chunk structure: None instead of empty string for IDs

**MEDIUM fixes**:
- Fix `generate_test_questions`: return ok=false on JSON parse failure
- Compression hardening, config safety nets

## 68. Answer Quality Hardening (2026-04-20)

**Anti-hallucination** (5 mandatory rules in `_PROMPT_GENERATOR`):
1. Context-only answers — KHÔNG bịa thêm thông tin ngoài context
2. Say "không biết" khi context không đủ — thay vì guess
3. Cite sources — gắn chunk ID vào mỗi claim
4. Citation whitelist — LLM chỉ được cite chunk IDs có trong context
5. CRAG fallback: min score threshold, force OOS khi không có adequate chunks

**Guardrails**:
- `too_short` guardrail: block empty, whitespace-only, emoji-only queries (min 2 alphanumeric)
- Friendly Vietnamese rejection message

**Vietnamese text**:
- `remove_diacritics()` trong `shared/vi_tokenizer.py`
- Hybrid search: diacritic-normalized query as additional BM25 variant

### Production-Ready Checklist (18/18 ✅)

| # | Feature | Mức độ | Ragbot | Validation Source |
|---|---------|--------|--------|-------------------|
| 1 | Hybrid Search (vector + BM25) | Bắt buộc | ✅ | Perplexity, Glean, Cohere |
| 2 | Cross-Encoder Reranking | Bắt buộc | ✅ | Cohere (+48%), Perplexity |
| 3 | Structure-Aware Chunking | Bắt buộc | ✅ | Mayo Clinic (87%), RAGFlow |
| 4 | Citation/Source Tracking | Bắt buộc | ✅ | NotebookLM, Cohere |
| 5 | Condense Question | Bắt buộc | ✅ | Industry standard |
| 6 | CRAG (grade → retry) | Bắt buộc | ✅ | CRAG paper, LangGraph |
| 7 | Self-RAG (reflect) | Khuyến nghị | ✅ | Self-RAG paper |
| 8 | Guardrails (input + output) | Bắt buộc | ✅ | Production standard |
| 9 | Context Sandboxing (XML) | Bắt buộc | ✅ | Anthropic, OpenAI |
| 10 | Semantic Cache | Khuyến nghị | ✅ | Redis (68.8% savings) |
| 11 | Prompt Caching | Bắt buộc | ✅ | OpenAI (50%), Anthropic (90%) |
| 12 | RAGAS Evaluation | Bắt buộc | ✅ | AutoRAG, production standard |
| 13 | Contextual Enrichment | Khuyến nghị | ✅ | Anthropic (-67% failure) |
| 14 | MMR Diversity | Khuyến nghị | ✅ | Qdrant pattern |
| 15 | Multi-tenant Isolation | Bắt buộc | ✅ | Glean, enterprise standard |
| 16 | Adaptive Query Routing | Khuyến nghị | ✅ | -35% latency, +8% accuracy |
| 17 | Incremental Re-indexing | Khuyến nghị | ✅ | Cursor (Merkle tree) |
| 18 | Observability (trace) | Bắt buộc | ✅ | structlog + OTel (Langfuse planned) |

### RAGAS Production Thresholds

| Metric | Minimum Viable | Good | Excellent |
|--------|---------------|------|-----------|
| Faithfulness | >= 0.70 | >= 0.85 | >= 0.95 |
| Answer Relevancy | >= 0.70 | >= 0.80 | >= 0.90 |
| Context Precision | >= 0.60 | >= 0.75 | >= 0.90 |
| Context Recall | >= 0.70 | >= 0.85 | >= 0.95 |

### Effort & Impact Summary (Phase 1-3)

| Phase | Hours | Score Impact | Cost Impact |
|-------|-------|-------------|-------------|
| Phase 1: Intelligence | 44h | 7.3 → 8.5 (+1.2) | +$0.001/query (reranker) |
| Phase 2: Cost | 27h | 8.5 → 9.0 (+0.5) | $0.005 → $0.002-0.003/query |
| Phase 3: Excellence | 66h | Spec complete, 4 features OFF by default | -80% re-embed cost |
| **Total** | **137h** | **7.3 → 8.5** | **~$0.002-0.003/query** |

---

## 69. Features OFF by Default

These features exist in code but are **disabled by default** via `system_config`. Must be explicitly enabled per bot/tenant:

| system_config Key | Default | Why OFF | Enable When |
|-------------------|---------|---------|-------------|
| `parent_child_enabled` | `false` | Increases storage 2-3x, needs tuning per domain | Documents with deep hierarchy needing multi-granularity retrieval |
| `permission_filtering_enabled` | `false` | Requires ACL setup per tenant, adds latency to retrieval | Multi-role tenants where users should NOT see all documents |
| `metadata_extraction_enabled` | `false` | Adds LLM cost per ingested document | Domains needing structured metadata for filtering (date, category, etc.) |
| `autocut_enabled` | `false` | Not yet implemented (Phase 4 P2-6) | After implementing dynamic cutoff based on score distribution |

**Impact on scoring**: These 4 features being OFF means production deployments get ~80% of the documented capability out of the box. The remaining 20% requires explicit configuration and testing per domain.

---

## 70. Rate Limiting (bot_limits.py) & Plan Limits

### 70.1 bot_limits.py

`src/ragbot/shared/bot_limits.py` — per-token rate limiting middleware.

| Rule | Value |
|------|-------|
| Backend owner (token owner = bot owner) | **0** (unlimited) |
| External API consumers | **120 requests / 60s** window |
| Dynamic values | Both `max_requests` and `window_seconds` from `system_config`, zero hardcode |

### 70.2 plan_limits JSONB Schema

The `bots` table has a `plan_limits` JSONB column storing per-bot resource limits:

```json
{
  "max_documents": 100,
  "max_document_size_mb": 10,
  "max_queries_per_day": 1000,
  "max_tokens_per_query": 4096,
  "allowed_models": ["gpt-4.1-mini"],
  "features_enabled": ["semantic_cache", "reranker"]
}
```

All values configurable per bot. Enforcement in `bot_limits.py` and ingestion use cases. No hardcoded defaults — all from `system_config` or `plan_limits` JSONB.

---

### Pipeline Node Count: 14-16 (Configurable)

The query pipeline has **16 registered nodes** when all features are enabled:

| # | Node | Configurable |
|---|------|-------------|
| 1 | input_guardrail | Always |
| 2 | semantic_cache_check | Always |
| 3 | **understand_query** | Merged into condense when `merge_condense_router = true` |
| 4 | **condense_question** | Merged with understand_query when `merge_condense_router = true` |
| 5 | **router** | Merged with condense when `merge_condense_router = true` |
| 6 | hyde_generator | Conditional (short queries) |
| 7 | retriever | Always |
| 8 | reranker | Always |
| 9 | context_assembly | Always |
| 10 | grader | Always |
| 11 | generator | Always |
| 12 | hallucination_check | Always |
| 13 | self_reflection | Always |
| 14 | cache_storage | Always |
| 15 | response_delivery | Always |
| 16 | web_search_fallback | CRAG fallback |

With `merge_condense_router = true` (default): nodes 3-5 merge into 1, giving **14 active nodes**.
With `merge_condense_router = false`: all 16 nodes active.

---

**END OF RAGBOT MASTER (v1.2 — superseded by v1.5 update at top)**

---

## VERSION 1 — Sprint progression (2026-04-28 update)

| Sprint | Date | Score | Tests | Migrations | Key deliverables |
|---|---|---:|---:|---:|---|
| Pre-Sprint-9 baseline | 2026-04-25→28 | 6.0/10 (brutal-honest) | 627 | 0040 | P1-P26 + Sprint 7 F1/F2/F4 + Sprint 8 P34-B + δ1 raw_content |
| **Sprint 9 Wave A0** | 2026-04-28 | 6.5 | 635 | 0042 | 3-key identity REQUIRED (cross-tenant fix), 9 phases, migration 0041 NOT NULL + 0042 drop legacy partial index |
| **Sprint 9 Wave A1+A2+B+C+D+E** | 2026-04-28 | 7.0 | 659 | 0043 | Brutal fixes (model names → constants, reranker fail-loud, test count honest), filter cleanup, Anthropic prompt caching helper, Lost-in-the-middle reorder, brand redact |
| **Sprint 9 Tier 1** | 2026-04-28 | 7.0 | 669 | 0043 | Real LLM SSE streaming, VN accent (DEFER honest), metadata_extraction (DEFER honest), TTL constant cleanup |
| **Sprint 10 — VERSION 1** | 2026-04-28 | **7.5** | **772 active** | **0046** | Contextual Retrieval (Anthropic 2024-09), Multi-query expansion, DeepEval RAGAS runner, Metadata-aware retrieval (migration 0044 GIN), VN compound segmentation (migration 0046), P25 Phase B+C resilience (CircuitBreaker + cache-stampede), P33 per-tenant rate-limit + token cap (migration 0045) |

## Score progression honest (per [BEST_PRACTICE_BENCHMARK_2026.md](../../reports/BEST_PRACTICE_BENCHMARK_2026.md) 8-axis)

| Axis | Pre-9 | Post-9 | **Post-10** |
|---|---:|---:|---:|
| 1 Retrieval | 5 | 5 | **8** (CR + MQ + metadata filter + VN compound) |
| 2 Faithfulness | 8 | 8 | 8 (cần reranker + 100q DeepEval để flip 9) |
| 3 Latency | 4 | 4 | **5** (CB + cache-stampede single-flight) |
| 4 Cost | 4 | 7 | 7 (token meter ready, middleware wire defer) |
| 5 Multi-tenancy | 6 | 8 | **9** (3-key identity + P33 services) |
| 6 Observability | 5 | 5 | **7** (Prometheus +5 metrics) |
| 7 Code quality | 6 | 8 | 8 (zero-hardcode 0 hits) |
| 8 Docs | 5 | 7 | 7 (this commit) |
| **Overall** | **6.0** | **6.5** | **7.5/10** |

## Sprint 11A candidates (CURRENT NEXT, VERSION 1 feature close — KHÔNG deploy/ops)

> Scrubbed 2026-04-28: anh xác nhận chưa cần đến giai đoạn deploy. Bỏ OTel/Phoenix/Prometheus alerting/Grafana/CI gating ra khỏi Sprint 11. Reranker S8 vẫn DEFER chờ provider/budget.

1. **P33 middleware wire** — services Sprint 10 đã ship nhưng chưa active runtime (~2-3h).
2. **CB streaming path** — soft CircuitBreaker cho `complete_runtime_stream` (~2-3h).
3. **C.3 Structured Output JSON schema** cho grade/reflect/decompose (~1 ngày).
4. **Migration 0046 backward-compat audit** (~1h).
5. **PLAN_V0 §H cleanup** — move 11 OBSOLETE → `_archive/` (~45 phút).
6. **2 TODO leftover decide** — knowledge_graph.py:7 + repository_ports.py:96 (~20 phút).

## Sprint 11B (separate, dedicated focus, 1-2 tuần)

- **RBAC migration** — 10 P0 routes ungated + 13 tenant-scope checks. P0 BLOCKER customer-facing multi-tenant launch.

## Sprint 12 candidates

1. **C.8 Ingestion PDF/DOCX/Excel** (PyMuPDF4LLM + Docling + PaddleOCR-VL) — mở thị trường enterprise.
2. **B.6** documents.channel_type column drop (denormalized thừa).
3. **query_graph.py refactor** (2043 lines god object) → modular per-node files.

## Backlog (trigger-gated)

- Compliance VN Decree 356 + EU AI Act (khách enterprise B2B trigger).
- MCP server (tenant tool usecase trigger).
- GraphRAG (corpus >100K chunks trigger).
- ColPali / ColQwen2-VL (scanned PDF trigger).
- LazyGraphRAG (cost optimization graph).
- Presidio PII Redactor (compliance trigger).

## BLOCKED on user decision

- ~~**S8 reranker activation**~~ — RESOLVED 2026-05-12: ZeroEntropy `zerank-2` shipped as default reranker (commit `b9e7761`); Cohere / ViRanker / Jina v3 remain available in registry as alternatives.
- **P29-B per-bot autonomy %**: chờ P29-A harness green.
- **VN accent ML restoration**: chờ transformers + bartpho/vit5 stack approval.

## Plans tracked: 55 (per [PLAN_V0_CHANGELOG.md](../../plans/PLAN_V0_CHANGELOG.md))

41 SHIPPED + 1 PARTIAL + 2 DROPPED + 4 SUPERSEDED + 2 DRAFT_ACTIVE BLOCKED + 7 OBSOLETE + 1 UNCLEAR + 3 KEEP_REFERENCE.

Đi cặp với [`ZALO_MASTER.md`](../../ZALO_MASTER.md) cho Zalo channel context — 2 file bù trừ, không lặp.
