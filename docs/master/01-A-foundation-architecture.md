# PHẦN A — NỀN TẢNG & KIẾN TRÚC

## 1. Định nghĩa, phạm vi, nguyên tắc

### 1.1 Định nghĩa

**RAGbot** là hệ thống hội thoại có khả năng trả lời câu hỏi bằng cách truy xuất tri thức từ corpus riêng của tổ chức rồi tổng hợp câu trả lời qua mô hình ngôn ngữ lớn (LLM). Khác chatbot thuần ở chỗ **câu trả lời bắt buộc có nguồn tham chiếu (citation) kiểm chứng được từ corpus**, không dựa vào kiến thức đóng của mô hình.

### 1.2 Phạm vi tài liệu

File này định nghĩa kiến trúc logic + build spec Python. Mọi triển khai muốn đạt production-grade phải tuân thủ:
- Kiến trúc logic (Phần A–E) — độc lập ngôn ngữ.
- Build spec (Phần F) — đặc tả Python cụ thể.
- Enforcement (Phần H) — 18 rules + 13 acceptance.

### 1.3 8 Nguyên tắc bất di bất dịch

1. **Accuracy là tối thượng** — không đánh đổi độ chính xác lấy tốc độ hay chi phí. Sai thì không dùng được.
2. **Mọi câu trả lời phải có citation kiểm chứng được** — LLM không được bịa nguồn; citation phải thuộc tập retrieved trong request hiện tại.
3. **Multi-tenancy là first-class** — cách ly dữ liệu ở mọi tầng (database, vector, cache, log, trace). Không tin caller, enforce ở hạ tầng.
4. **Event-driven as default** — client không chặn chờ LLM; kết quả đẩy qua kênh thích hợp (webhook, WebSocket, SSE, push).
5. **Versioning toàn diện** — embedding model, corpus, prompt, bot config đều có version; cache key và retrieval phải scope theo version.
6. **Observability không phải tùy chọn** — mọi LLM call, tool call, retrieval stage đều phải trace được end-to-end với cost attribution.
7. **Defense-in-depth về an ninh** — không bao giờ chỉ 1 lớp phòng thủ cho prompt injection / PII / jailbreak.
8. **Measurability** — mọi quyết định kiến trúc phải gắn với metric đo được; không đo được không đưa vào production.

## 2. Kiến trúc vs Tech Stack vs Feature

| Khía cạnh | Kiến trúc | Tech Stack | Feature |
|---|---|---|---|
| **Tính chất** | Quyết định logic, bất biến | Công cụ hiện thực hóa | Khả năng cụ thể |
| **Ví dụ** | "Retrieval 2-stage: retrieve → rerank" | "Qdrant + BGE-reranker" | "Hybrid search" |
| **Vòng đời** | Năm/thập kỷ | 2–5 năm | Sprint/tháng |
| **Câu hỏi trả lời** | Tại sao + Cái gì | Bằng gì | Có gì |

Lỗi phổ biến: nhầm tech stack thành kiến trúc. "Dùng Python + FastAPI + Qdrant" không phải kiến trúc — đó là chọn công cụ. Kiến trúc là: "Tách 2 graph độc lập Ingestion + Query, giao tiếp qua vector store + event bus; mọi retrieval đi qua 2 stage bắt buộc; citation bind vào retrieved set."

## 3. Ngộ nhận phổ biến về RAG

| Ngộ nhận | Thực tế |
|---|---|
| "Dùng vector DB + embed là có RAG" | Chưa có reranking, query understanding, citation validation thì chỉ là demo |
| "Chunk 512 token là đủ" | Fixed chunking vỡ bảng/công thức, mất context dài |
| "LangChain/LlamaIndex giải quyết hết" | Framework lo cơ chế; chất lượng retrieval vẫn do kiến trúc + dữ liệu |
| "RAG không hallucinate" | Không có citation validation + faithfulness check thì vẫn bịa |
| "Thêm agent là thông minh hơn" | Agent không kỷ luật dễ loop, tốn token, khó debug |
| "Lớn là HNSW, nhỏ là flat" | Sai. Luôn dùng index từ đầu, giữ ef_search thấp khi nhỏ là đủ |
| "Semantic search thay được BM25" | Sai. Long-tail entity, số hiệu, tên riêng — BM25 vẫn thắng. Hybrid mới đủ |

## 4. Mô hình 7 tầng logic (spec/target) + 3 trục ngang

> **HONEST NOTE**: The 7-layer model below is the architectural SPEC/TARGET. Actual implementation uses a 3-layer pipeline: `analyze_document → select_strategy → dispatch`. Not all 7 layers are fully implemented.

```
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 7: FEEDBACK LAYER                                     │
│  Evaluation · Golden Set · Shadow Eval · Feedback Loop      │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 6: GENERATION LAYER                                   │
│  Prompt Assembly · Structured Output · Citation Binding     │
│  Context Sandboxing · Guardrails · Streaming                │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 5: REASONING LAYER                                    │
│  Agent Graph · Self-RAG · CRAG · Multi-hop · Tools          │
│  Checkpointing · Iteration Control                          │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 4: RETRIEVAL LAYER                                    │
│  Query Understanding · Routing · Rewriting · HyDE           │
│  Hybrid Search · Reranking · MMR · Freshness                │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 3: INDEXING LAYER                                     │
│  Dense + Sparse + Multi-vector · HNSW · Payload Index       │
│  Versioning · Tenant Isolation · Reindex                    │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 2: INGESTION LAYER                                    │
│  Parsing · Block Detection · Adaptive Chunking (AdapChunk)  │
│  Atomic Preservation · Narration · Enrichment               │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 1: KNOWLEDGE LAYER                                    │
│  Sources · Lifecycle · Versioning · Ownership · ACL         │
└─────────────────────────────────────────────────────────────┘

╔═════════════════════════════════════════════════════════════╗
║  3 TRỤC NGANG (xuyên suốt 7 tầng):                          ║
║  • Security & Multi-Tenancy (isolation, injection, PII)     ║
║  • Observability (trace, metric, log, cost)                 ║
║  • Lifecycle & Event-Driven (version, reindex, events)      ║
╚═════════════════════════════════════════════════════════════╝
```

**Đọc từ dưới lên**: Knowledge là nguồn → Ingestion biến thành chunk có cấu trúc → Indexing lưu vector + metadata → Retrieval truy xuất → Reasoning orchestrate → Generation tạo câu trả lời → Feedback đo và cải tiến.

**Ranh giới domain**: mỗi tầng là **bounded context** với interface rõ ràng. Tầng trên phụ thuộc abstraction của tầng dưới, không phụ thuộc implementation. Vi phạm ranh giới = ăn mòn kiến trúc.

---
