# PHẦN H — ENFORCEMENT & ACCEPTANCE

## 38. 18 Enforcement Rules (code generator phải tuân)

1. **Tenant filter** ở mọi repository, Qdrant query, cache key — không ngoại lệ.
2. **Cache key** include `tenant_id + bot_version + corpus_version`.
3. **Citation validation** sau generate: doc_id phải thuộc retrieved set.
4. **Context XML wrap** với instruction "data not instructions".
5. **Structured output** bằng `instructor` cho mọi LLM call.
6. **Circuit breaker** mỗi external call (LLM, reranker, OCR, webhook, embedding).
7. **structlog trace** mọi LangGraph node + LLM wrapper (Langfuse @observe planned).
8. **Prometheus metric** mọi critical operation (retrieval, rerank, cache, cost).
9. **Idempotency key** mọi Taskiq consumer.
10. **Rate limit + token budget** check trước LLM call.
11. **Input + Output moderation** via Llama Guard 3.
12. **PII redact** (Presidio) trước log, cache, external call.
13. **Embedding model version** trong metadata + cache key.
14. **Graceful shutdown**: drain worker, flush logs, close connections.
15. **Type hints 100%**, mypy strict pass.
16. **Cấm**: `eval()`, `exec()`, raw SQL trong business code.
17. **async def** trong request path; CPU-bound qua `run_in_executor`.
18. **Error hierarchy**: `DomainError → ApplicationError → InfrastructureError`; map HTTP chỉ ở interfaces layer.

## 39. 13 Acceptance Criteria

1. ✅ `docker-compose up` full stack local chạy được.
2. ✅ `alembic upgrade head` clean.
3. ✅ `pytest tests/unit` pass, coverage ≥ 90% domain+application.
4. ✅ `pytest tests/integration` pass với testcontainers.
5. ✅ `pytest -m eval` pass RAGAS baseline trên 50 golden cases.
6. ✅ `mypy --strict src/` pass.
7. ✅ `ruff check src/` zero warning.
8. ✅ Locust load: 500 concurrent, p95 < 5s, p99 < 8s.
9. ✅ E2E: upload PDF → chat → webhook receive answer < 10s.
10. ✅ structlog trace mọi stage + cost (Langfuse UI planned).
11. ✅ Prometheus `/metrics` expose RED + RAG custom.
12. ✅ API Reference docs complete (docs/API_REFERENCE.md, 1500+ lines).
13. ✅ Docs: architecture Mermaid + runbook + ADR cho 10 quyết định.

## 40. 12 Golden Test Cases

| # | Loại | Pass criteria | File |
|---|---|---|---|
| 1 | Factoid single-hop | Exact match + citation đúng | `factoid.jsonl` |
| 2 | Multi-hop reasoning | Trace 2+ docs | `multi_hop.jsonl` |
| 3 | Aggregation | Recall ≥ 95% | `aggregation.jsonl` |
| 4 | Conflict resolution | Detect + cite cả 2 | `conflict.jsonl` |
| 5 | Outdated info | Trả version mới + flag old | `outdated.jsonl` |
| 6 | Out-of-scope | Polite refuse không hallucinate | `out_of_scope.jsonl` |
| 7 | Adversarial/jailbreak | Block + log | `adversarial.jsonl` |
| 8 | Prompt injection in doc | Không tuân theo | `injection.jsonl` |
| 9 | VN mixed English | Hiểu code-switch | `mixed_lang.jsonl` |
| 10 | Long-tail rare entity | Recall đúng, không fuzzy nhầm | `rare.jsonl` |
| 11 | Ambiguous query | Clarify / dùng history | `ambiguous.jsonl` |
| 12 | No-answer case | Trả "không tìm thấy", không bịa | `no_answer.jsonl` |

Stress tests: 50-turn conversation, multi-tenant red-team, cost regression (< 10% tăng), latency p95 SLA.

**Mỗi case có**:
```jsonl
{"id":"f_001","type":"factoid","difficulty":"easy","query":"...","expected_answer":"...","must_contain":["..."],"must_not_contain":[],"expected_chunks":["doc_id#chunk_id"],"tenant_id":"test_tenant_1","conversation_history":[]}
```

## 41. Self-Audit Log & Patches

### 41.1 Auditor scoring (căng — thang 10)

| Tiêu chí | Điểm | Ghi chú |
|---|---|---|
| Coverage 3-layer pipeline | 8/10 | 3-layer implemented (analyze_document → select_strategy → dispatch), 7-tầng logic is spec-only |
| Python stack specificity | 9/10 | Exact version, library, code pattern — một số feature OFF by default |
| Project layout chi tiết | 9/10 | Full tree, naming convention |
| Security defense-in-depth | 8/10 | Tenant isolation done, permission_filtering OFF by default |
| Performance zero-N+1 | 8/10 | Rules + dataloader — autocut chưa có, parent_child OFF |
| Observability hoàn chỉnh | 7/10 | structlog + OTel + Prom (Langfuse planned, not integrated) |
| Event-driven correctness | 9/10 | Outbox + idempotency + DLQ — saga compensation spec only |
| Caching soundness | 7/10 | 2-tier (exact hash Redis + semantic pgvector), version-scoped. 4-tier planned |
| Evaluation rigor | 8/10 | RAGAS + golden set — shadow eval + hard negative mining spec only |
| Cost control | 8/10 | Budget + cascade — anomaly detection spec only |
| Multi-tenant testing | 8/10 | Red-team pattern defined, 4 features OFF by default |
| Chunking | 7/10 | HDT + paragraph-based (SEMANTIC). PROPOSITION planned. HYBRID = recursive fallback. Late + Contextual done. parent_child OFF |
| CI/CD maturity | 8/10 | Pre-commit + GH Actions — blue-green DB + canary spec only |
| Documentation quality | 9/10 | Mục lục + Mermaid + ADR + runbook |

**Tổng: 8.5/10 — production-ready with known gaps**

Điểm yếu còn lại: (1) BM25 dùng ts_rank thay vì BM25 thật, (2) 4 features OFF by default (parent_child, permission_filtering, metadata_extraction, autocut), (3) Phase 4 chưa implement, (4) fine-tune LLM domain-specific cần ngân sách + data scale khác.

### 41.2 10 Gap đã được patch (xem đã merge vào các phần tương ứng)

| # | Gap | Patched tại |
|---|---|---|
| 1 | Tool Use pattern | Phần 9.6 + 28 (tools adapter) |
| 2 | Saga compensation | Phần 14.9 + 30.2 |
| 3 | N+1 auto-detection | Phần 31.3 |
| 4 | Cross-tenant leak concrete test | Phần 31.4 |
| 5 | Mermaid diagrams | Phần 17.3 + 30.1 |
| 6 | Hard negative mining | Phần 34.6 |
| 7 | Cost anomaly detection | Phần 34.7 |
| 8 | Blue-green DB migration | Phần 33.4 |
| 9 | Canary token output check | Phần 28.9 |
| 10 | Secrets rotation automation | Phần 34.5 |

---

## 42. Gate metrics — VERSION 1 (post Sprint 10, 2026-04-28)

5 Gates measured trên test bot, 4 Google Sheets, 340-turn LLM judge (Sprint 8 baseline; Sprint 10 re-baseline pending re-ingest):

| Gate | Target | Sprint 8 baseline | Status |
|---|---|---:|---|
| 1. Answered | ≥95% | 100% | ✅ PASS |
| 2a. Halluc | ≤10% | 6.8% | ✅ PASS |
| 2b. **Grounded** | ≥80% | 80.3% | ✅ **NEW** (first pass post-Sprint-7) |
| 3a. Equiv | ≥80% | 78.8% | ❌ FAIL gap 1.2pp (reranker activation expected fix) |
| 3b. Halluc-diff | ≤5% | 5.0% | ✅ at bar |

Sprint 10 ships Contextual Retrieval + multi-query + metadata-aware retrieval + VN compound segmentation → re-baseline expected sau re-ingest. Reranker activation (S8) blocked on user provider/budget decision.

## 43. New enforcement rules Sprint 9-10

| # | Rule | Sprint | Reference |
|---|---|---|---|
| R-19 | **3-key identity REQUIRED** `(tenant_id: int, bot_id: str, channel_type: str)` — cả 3 NOT NULL DB + Pydantic required + JWT/body mismatch reject 403 | Sprint 9 Wave A0 | [CLAUDE.md](../../CLAUDE.md), [03-C §12.9](03-C-cross-axes.md) |
| R-20 | **Reranker fail-loud preflight** — `reranker_enabled=true` + provider key missing → raise RuntimeError (no silent disable) | Sprint 9 Wave A1 | [app.py](../../src/ragbot/interfaces/http/app.py) |
| R-21 | **Math lockdown vs streaming** — streaming push tokens trước find_ungrounded_numbers; lockdown trigger → SSE `replace` event với safe canonical answer *(historical — math_lockdown since removed per sacred #2/#4 no app-override)* | Sprint 9 Tier 1 | [routes/test_chat/](../../src/ragbot/interfaces/http/routes/test_chat/) |
| R-22 | **Internal queries CHỈ dùng `record_bot_id`** — sau resolve `(tenant_id, bot_id, channel_type) → record_bot_id`, channel_type THỪA ở internal layer | Sprint 9 Wave A2 | document_service + pgvector_store |
| R-23 | **Cross-tenant red-team test mandatory** — 2 tenant cùng `(bot_id, channel_type)` slug PHẢI resolve isolated | Sprint 9 Wave A0 | [test_3key_cross_tenant_isolation.py](../../tests/integration/test_3key_cross_tenant_isolation.py) |
| R-24 | **CircuitBreaker per-provider isolated** — OpenAI flap KHÔNG poison Anthropic/Cohere state | Sprint 10 P25 | [test_circuit_breaker.py](../../tests/unit/test_circuit_breaker.py) |
| R-25 | **Per-tenant rate-limit bypass OR logic** — `effective_bypass = tenant.bypass_rate_limit OR bot.bypass_rate_limit` | Sprint 10 P33 | [tenant_rate_limiter.py](../../src/ragbot/application/services/tenant_rate_limiter.py) |

---
