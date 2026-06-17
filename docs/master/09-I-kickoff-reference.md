# PHẦN I — KICK-OFF & REFERENCE

## 42. Kick-off Command cho Agent

Paste vào Claude/Cursor/Windsurf:

```
Đọc RAGBOT_MASTER.md (file này). Sinh repo Python RAGbot theo spec trong file.

Start theo thứ tự:
1. pyproject.toml + dependencies khóa version từ Phần 24.
2. src/ragbot/domain/ — entities + VO pure (Phần 26).
3. src/ragbot/application/ports/ — Protocol interfaces (Phần 27.1).
4. src/ragbot/application/use_cases/answer_question.py + ingest_document.py (Phần 27.2).
5. src/ragbot/application/sagas/rag_graph.py + ingest_graph.py — LangGraph StateGraph (Phần 16, 17).
6. src/ragbot/infrastructure/ — adapters (Phần 28).
7. src/ragbot/interfaces/http/ — FastAPI với 202 Accepted (Phần 29.1).
8. alembic migration initial.
9. docker-compose.yml full stack (Phần 32.2).
10. tests/unit + tests/integration + tests/eval (Phần 31).
11. golden_set/ với 10 mẫu mỗi loại (Phần 40).
12. README.md với Mermaid architecture diagram.

Note (2026-04-23): project runs LOCAL ONLY — không có CI/CD GitHub
Actions. Testing + deploy qua `deploy.sh` + `docker-compose`.

TUÂN THỦ tuyệt đối 18 Enforcement Rules (Phần 38). KHÔNG skip rule nào.

Mọi LLM call qua LiteLLM. Mọi external call có circuit breaker.
Mọi cache key prefix tenant + version. Mọi repository filter tenant_id.
Mọi LangGraph node structlog traced (Langfuse planned). Mọi Taskiq consumer idempotent.

Khi hoàn thành, verify 13 Acceptance Criteria (Phần 39) pass.

Nếu gặp ambiguity, đọc lại phần liên quan trong RAGBOT_MASTER.md — KHÔNG tự quyết định trái với spec.
```

## 43. Decision Records (ADR Pattern)

Khi gặp quyết định ngoài spec, tạo ADR `docs/adr/NNNN-title.md`:

```markdown
# ADR NNNN: <title>
## Context
<Situation, constraints, problem>
## Decision
<What we decided>
## Consequences
<Trade-offs, implications, what becomes easier/harder>
## Alternatives considered
<A, B, C — and why rejected>
## Metrics to validate
<How we know decision is correct>
```

10 quyết định mandatory có ADR:
1. Redis Streams vs NATS JetStream vs Kafka.
2. LangGraph vs LlamaIndex Workflow.
3. Qdrant vs Weaviate vs pgvector.
4. BGE-m3 vs multilingual-e5 vs vietnamese-sbert.
5. Shared collection vs per-tenant.
6. Taskiq vs Celery vs Arq.
7. Self-host reranker vs Cohere SaaS.
8. Outbox pattern vs CDC (Debezium).
9. LiteLLM proxy vs direct SDK.
10. Redis Stack vs Memcached.

### 43.1 Trade-off Matrix

| Quyết định | A | B | Tiêu chí chọn |
|---|---|---|---|
| Sync vs Event-driven | Sync HTTP | Event-driven | Event-driven default; sync chỉ CRUD trivial |
| Shared vs per-tenant collection | Shared + filter | Per-tenant | Per-tenant khi > 10M vectors hoặc compliance |
| SaaS vs self-host LLM | SaaS | Self-host vLLM | SaaS MVP; self-host scale + compliance |
| SaaS vs self-host reranker | Cohere | BGE local | BGE local default |
| Dense only vs Dense+sparse+ColBERT | Dense | Multi-vector | Dense+sparse mandatory; ColBERT khi có budget |
| Chunk size | Small (128-256) | Large (512-1024) | Large cho narrative; small cho fact-dense |
| Iteration cap | 2 | 3 | 3 default; 2 nếu latency critical |

## 44. Glossary

- **Adaptive Chunking (AdapChunk)**: chọn strategy chunking dựa trên cấu trúc tài liệu, thay vì fixed size.
- **Atomic Block**: block không được cắt khi chunking (bảng, công thức, ảnh).
- **BM25**: thuật toán sparse retrieval cổ điển, mạnh cho exact keyword.
- **Canary Token**: chuỗi bí mật chèn vào system prompt; leak = sign of injection.
- **Checkpointing**: lưu state agent graph sau mỗi node, resume được.
- **Citation Binding**: ràng buộc citation phải thuộc retrieved set của request.
- **Context Sandboxing**: wrap retrieved context trong tag + instruction chống injection.
- **Contextual Retrieval**: prepend tiền tố ngữ cảnh vào chunk trước khi embed (Anthropic 09/2024).
- **CRAG (Corrective RAG)**: pattern có grader đánh giá retrieved chất lượng, fallback nếu kém.
- **DataLoader Pattern**: gom nhiều request trong cửa sổ thời gian, gửi batch.
- **Defense-in-Depth**: nhiều lớp phòng thủ độc lập cho cùng threat.
- **Dense Embedding**: vector ngữ nghĩa từ neural model.
- **Event-Driven**: giao tiếp qua event async, không sync request-response.
- **Freshness Decay**: score giảm theo tuổi doc.
- **Golden Set**: bộ test chuẩn cho RAG evaluation.
- **Hexagonal (Ports & Adapters)**: kiến trúc tách domain khỏi infra qua port interfaces.
- **HNSW**: Hierarchical Navigable Small World, index ANN phổ biến.
- **HyDE**: Hypothetical Document Embedding, LLM sinh câu trả lời giả định để embed.
- **Hybrid Search**: dense + sparse kết hợp qua fusion.
- **Idempotency Key**: định danh để dedup khi xử lý event nhiều lần.
- **IRCoT**: Interleaved Retrieval with Chain-of-Thought, multi-hop pattern.
- **Late Chunking**: embed cả doc trước, pool theo boundary chunk sau (Jina 2024).
- **LLM-as-Judge**: dùng LLM đánh giá câu trả lời cho shadow eval.
- **MMR**: Maximal Marginal Relevance, cân bằng relevance + diversity.
- **Narration**: chuyển nội dung phi-text (LaTeX, table) thành câu tự nhiên trước khi embed.
- **Outbox Pattern**: ghi event vào bảng trong cùng transaction với business data, poller publish sau.
- **PII**: Personally Identifiable Information.
- **Proposition**: phát biểu nguyên tử, tự đủ nghĩa.
- **RAGAS**: framework đánh giá RAG với faithfulness, relevancy, precision, recall.
- **Rerank**: sắp xếp lại top K candidates bằng cross-encoder.
- **RRF**: Reciprocal Rank Fusion, fuse nhiều ranking bằng rank.
- **Self-RAG**: pattern agent tự reflect câu trả lời.
- **Shadow Eval**: eval trên production traffic sample, không ảnh hưởng user.
- **Sparse Embedding**: representation keyword-based, BM25 hoặc SPLADE.
- **Structural Path**: breadcrumb của chunk trong cấu trúc doc.
- **Tenant**: đơn vị cách ly dữ liệu (khách hàng, tổ chức).
- **Token Budget**: giới hạn token per tenant.
- **Transactional Outbox**: xem Outbox Pattern.

---
