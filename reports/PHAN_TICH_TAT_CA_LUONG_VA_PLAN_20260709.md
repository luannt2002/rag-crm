# TÀI LIỆU BÀN GIAO — HỆ EXPERT RAG "RAGBOT": TẤT CẢ CÁC LUỒNG, BUG ĐÃ KIỂM CHỨNG & ACTION PLAN

> **Loại tài liệu:** Bàn giao kỹ thuật (engineering handover) — dành cho team tiếp nhận đọc lại và hành động.
> **Nguồn:** (1) Báo cáo phân tích luồng tổng thể `PHAN-TICH-LUONG-RAGBOT.md` (đã đối chiếu code thật `rag-crm/` qua 4 nhóm verify: `query_graph`, `chat_stream`, `guardrail_persist`, `architecture-flows`); (2) 4 section deep-dive đọc code thật `rag-crm/src/` — RETRIEVE, ANTI-HALLUCINATION, 4 CRITICAL BUGS, INGESTION & CHUNKING.
> **Ngày lập:** 2026-07-09
> **Mục đích:** Bàn giao đầy đủ kiến trúc + mọi luồng (Ingestion / Retrieval-Serving / Query end-to-end), bản đồ bug đã kiểm chứng (giữ nguyên trạng thái Confirmed/Refuted/Partial + mọi `file:line`), và một ACTION PLAN có backlog + roadmap để team thực thi.

## Cách đọc tài liệu này

- **Nếu bạn là kỹ sư sắp fix bug:** đọc thẳng **Mục 6 (Bản đồ bug)** → **Mục 8 (Action Plan)** → tra `file:line` trong **Mục 7 (Deep-dive)**.
- **Nếu bạn là lead/PM:** đọc **Mục 1 (Executive Summary)** + **Mục 8 (Action Plan, roadmap 4 tuần)**.
- **Nếu bạn cần hiểu hệ thống từ đầu:** đọc tuần tự **Mục 2 → 5**.
- **Quy ước trạng thái:** mỗi claim bug mang một nhãn kiểm chứng:
  - **Confirmed** = đọc code thật xác nhận đúng (có `file:line`).
  - **Refuted** = code thật cho thấy claim SAI — **KHÔNG được sửa** (xem Mục 6.2 và Mục 8 "KHÔNG làm").
  - **Partial** = đúng một phần, cần đọc kỹ sắc thái trước khi hành động.
- **Quy ước bằng chứng:** mọi khẳng định về hành vi đều kèm đường dẫn `file:line` (link markdown tương đối vào `rag-crm/`). Các nhận định trong deep-dive là **code-evidence tĩnh**; nơi cần **runtime-verify** (query `system_config`) đều được ghi rõ.
- **Quy ước "code thắng comment":** khi comment/docstring mâu thuẫn với hằng số/constant thật, tài liệu này lấy **hằng số** làm chuẩn (đã đánh dấu ⚠️ ở các điểm lệch).

---

## 1. TÓM TẮT ĐIỀU HÀNH (EXECUTIVE SUMMARY)

**Ragbot là gì.** Một hệ **Expert RAG production-grade, multi-tenant**, phục vụ chat streaming (SSE) trên kho tài liệu đa định dạng (PDF/DOCX/XLSX/CSV/Sheets/PPTX/HTML/MD/ảnh). Khác "Naïve RAG" ở hai điểm: (i) **Expert Chunking** hiểu cấu trúc tài liệu thay vì cắt theo số ký tự; (ii) một **hàng rào chống hallucination nhiều lớp** với hợp đồng thiết kế `HALLU_FABRICATE = 0`. Hệ chia thành **hai pipeline tách biệt**: Ingestion (U1→U7) và Retrieval & Serving (Q0→Q17, adaptive tới Q32). Stack: LangGraph orchestration, pgvector HNSW (dense) + tsvector BM25 (sparse) + RRF, cross-encoder rerank, LLM tiering (`gpt-4.1-mini`/`nano`), Hexagonal/DDD (Port+Adapter+Registry+Null Object+DI), multi-tenant 4-key + RLS.

**Mức độ khớp tài liệu ↔ code.** **CAO.** Kiến trúc, hai pipeline, LangGraph, Hexagonal, RLS 4-key đều **Confirmed** có bằng chứng code trực tiếp. Hai đính chính: (i) dải node đúng là **Q0/Q0.5 + Q1→Q17 (adaptive → Q32), KHÔNG có "Q18"**; (ii) modular hóa chưa xong — `query_graph.py` vẫn là megafile ~3071 dòng, `retrieve()` là mega-node ~1773 LOC.

**4 bug CRITICAL đã xác nhận bằng đọc code thật (ưu tiên tuyệt đối):**

| # | Bug | file:line | Blast radius |
|---|-----|-----------|--------------|
| B1 | `embed_degraded` là **dead-write** — SET nhưng 0 reader → cờ HALLU-safety vô hiệu | [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py) (SET) · [state.py:231](rag-crm/src/ragbot/orchestration/state.py) (decl) | HALLU + dữ liệu + compliance |
| B2 | Dedup entity bằng Python **`id()`** (địa chỉ object) trong `_reconcile_cross_doc` | [query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py) | billing/giá + dữ liệu + HALLU conflate |
| B3 | `_persist` **nuốt lỗi im lặng** (`except: pass`, không log) → audit thấy 0 event | [local_guardrail.py:948-964](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) | compliance/audit (silent data-loss) |
| B4 | **Citation không verify chunk tồn tại** — có `[gì đó]` là grounding pass | [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) (regex) · [:394](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) (short-circuit) | HALLU (citation giả) + compliance |

**Cảnh báo — các claim BỊ BÁC BỎ (đừng sửa nhầm):** "65 Redis call tuần tự ~975ms" (**Refuted** — thực tế 1 MGET cho 172 key), "async weak-ref memory leak trong persist.py" (**Refuted** — đã fix đúng với `_BG_CACHE_TASKS`), "bot cache hit ~5%" (**Refuted**), "RRF hardcoded không tunable" (**Refuted** — tunable qua `rag_rrf_k`/`lexical_rrf_k` ở tầng Python), "multi-query 5 gates" (**Refuted** — thực tế 9 gate), "message_id collision là CRITICAL" (**Partial** — trùng vô hại vì PK là `request_id` UUID). Chi tiết ở Mục 6.2.

**Sắc thái quan trọng về HALLU=0 (từ deep-dive).** Khung phòng thủ rất dày (9 lớp), nhưng với **cấu hình mặc định của một bot mới**, phần lớn lớp số hoá chống bịa (numeric-fidelity, brand-scope, claim-fidelity, grounding-confirmed) đều ở chế độ **OBSERVE = log-and-ship**, còn regex grounding_check mặc định **OFF**. "HALLU=0" như đang code là **một khả năng opt-in cho bot owner**, không phải bảo đảm tự động của platform. Điểm cưỡng chế cứng mặc định chỉ gồm: refuse 0-chunk và fail-closed khi grounder chết.

**Khuyến nghị 1 dòng:** *Deploy có điều kiện — code solid nhưng operationally fragile; fix ngay 5 hạng mục P0/P1 (embed_degraded, id()-dedup, guardrail log, citation validate, streaming ledger) trước/ngay sau khi lên prod, và đọc code thật để lọc bug trước khi hành động vì ~½ claim gốc là Refuted/Partial.*

---

## 2. KIẾN TRÚC TỔNG QUAN

### 2.1. Hai pipeline song song

- **Ingestion Pipeline (Upload, U1→U7):** nạp file → validate → parse → clean → chunk → enrich (Contextual Retrieval) → VN segment → narrate + embed → lưu `document_chunks`.
- **Retrieval & Serving Pipeline (Query, Q0→Q17, adaptive → Q32):** nhận query → guard input → understand/router → rewrite/decompose → retrieve (hybrid) → rerank → mmr_dedup → neighbor_expand → grade → generate → critique_parse → guard_output → reflect → persist; trả token-by-token qua SSE.

### 2.2. Mô hình phân tầng (blueprint)

| Tầng | Tên | Vai trò |
|------|-----|---------|
| Layer 1 | Ingestion & Routing | File Validator, Unified ID Generator, Extension/MIME/byte-sniff Router |
| Layer 2 | Advanced Parsing Engine | Kreuzberg/pypdfium (PDF), Openpyxl (Excel), row-as-chunk (Sheets), layout-aware/OCR |
| Tầng 4 | Rule-based Cross-check (Guardrail) | Quy tắc cứng override quyết định chọn strategy chunking |
| Tầng 5 | Chunking Executor | Thực thi HDT/SEMANTIC/PROPOSITION/HYBRID + Atomic Integrity |
| Tầng 6/7 | Narrate then Embed | Table/LaTeX/ảnh → văn xuôi để embed; tách Search vs Generation ("Swap Trick") |
| Serving | Retrieval + Generation | Hybrid (dense+BM25+RRF), Rerank, Generate, Evaluate, Guard, Reflect |

### 2.3. Stack công nghệ

- **Orchestration:** LangGraph StateGraph (~21–30 node), canonical Q0/Q0.5 + Q1→Q17.
- **Retrieval:** Hybrid = pgvector HNSW (`m=16, ef=64`) + tsvector BM25 (`ts_rank_cd`), hợp nhất bằng **RRF (Cormack 2009)**.
- **Reranking:** Cross-encoder (ZeroEntropy `zerank-2` default; swappable Cohere/Jina/Voyage/ViRanker/null).
- **Generation:** `gpt-4.1-mini` (chính) + `gpt-4.1-nano` (rẻ), cascade router; temperature `0.0`.
- **Vector DB:** pgvector (PostgreSQL) + `metadata_json` JSONB.
- **Multi-tenant:** định danh **4-key** `(record_tenant_id, workspace_id, bot_id, channel_type)` + **RLS** 20 bảng (21 policy `tenant_isolation`).
- **Kiến trúc phần mềm:** Hexagonal/DDD (Port + Adapter + Registry + Null Object + DI thủ công qua closure của `build_graph`).
- **Kênh:** HTTP + SSE (`text/event-stream`), JWT (HS256→RS256), rate-limit 3 lớp.

### 2.4. Sequence tổng (Query end-to-end)

```
CLIENT ──POST /api/ragbot/chat/stream {bot_id, channel_type, question, workspace_id, connect_id}
        │  Header: Authorization: Bearer <JWT>
        ▼
MIDDLEWARE ── (1) JWT verify HS256→RS256  (2) Extract record_tenant_id
              (3) Rate-limit 3 lớp        (4) Bind request.state
        ▼
chat_stream.py (Line 87-470) ── STEP 0 → STEP 11 (tuần tự)
        ▼
LangGraph query_graph.py ── build_graph() → graph.ainvoke() → Q0 → … → Q17
        ▼
SSE STREAM (stream_real_llm) ── event: token / TTFT / citations / sources / done
        ▼
CLIENT nhận câu trả lời token-by-token
```

### 2.5. Mức độ khớp tài liệu ↔ code (nhóm verify `architecture-flows`)

| Claim kiến trúc | Verdict | Ghi chú |
|-----------------|---------|---------|
| (a) Ingestion pipeline (router, parsers, chunking, narrate, embed, vector DB) | **Confirmed** | `parser/registry.py`, 8+ parser, `narrate_dispatch.py`, 4 embedder thật, `pgvector_store.py`; comment U2–U7 rõ |
| (b) Retrieval/Serving (hybrid, rerank, generate, eval) | **Confirmed** | `hybrid_search()`, 6 reranker, `generate.py`, `ragas_metrics.py` |
| (c) LangGraph nodes Q0–Q18 | **Partial** | 9 node khớp file thật; nhưng nhãn "Q0–Q18" SAI — canonical là **Q0/Q0.5 + Q1→Q17 (→Q32)**; **không có "Q18"** |
| (d) Multi-tenancy / RLS / 4-key | **Confirmed** | 20 bảng `FORCE ROW LEVEL SECURITY` + 21 policy `tenant_isolation` + hook `SET LOCAL app.tenant_id` |
| Kết luận tổng | **Confirmed** | Điểm lệch nhỏ: dải số Q; `query_graph.py` vẫn megafile ~143KB chưa tách hết node |

---

## 3. LUỒNG A — INGESTION PIPELINE (Nạp & Cắt khúc dữ liệu)

Ingestion là **một-luồng-canonical đa định dạng**: mọi format đi qua đúng một pipeline `DocumentService.ingest()` (U1→U7). Blueprint chia 4 luồng con 1.1→1.4; deep-dive 4 ánh xạ chính xác vào `file:line` code thật (xem Mục 7.4). Dưới đây là bản chắt lọc theo bước + bug gắn theo bước.

### 3.1. Luồng 1.1 — Tiếp nhận & Định tuyến (U1 validate, U2 parse-route)

**Mục đích:** Nhận file thô, cấp ID lineage, định tuyến parser theo MIME/ext/byte-sniff.

**Các bước:** (1) nhận raw file; (2) **Validator** — file trống/mã hóa/lỗi định dạng, tenant guard (`record_tenant_id`), source-URL allow-list (PoisonedRAG defence, [ingest_core.py:295](rag-crm/src/ragbot/application/services/document_service/ingest_core.py)); (3) **Unified ID Generator** — `document_id` + `parent_doc_id` lineage; (4) **Router 3 tầng** — `detect_parser(mime, ext)` → `detect_parser_robust(...)` byte-sniff fallback → `_sniff_mime(content)` (magic `%PDF-`, OOXML `PK\x03\x04`, kreuzberg long-tail). Đầu `ingest()` còn vá "octet-stream → 0 chunks" bằng `sniff_real_mime` ([ingest_core.py:261-272](rag-crm/src/ragbot/application/services/document_service/ingest_core.py)).

**Bug/pitfall theo bước:**
- **`parent_doc_id` tự trỏ về chính nó** (MEDIUM, blueprint): `parent_doc_id or doc_id` khiến doc gốc trỏ về chính nó → mờ ranh giới lineage. Nên là `None` cho node gốc.
- **Lỗi cú pháp `import uuidimport os`** (LOW, mã phác thảo) — `SyntaxError` nếu copy nguyên văn.
- *(Hai bug này thuộc mã blueprint trong tài liệu, không phải code production đã verify.)*

### 3.2. Luồng 1.2 — Bóc tách cấu trúc (U2 parse → Atomic Blocks)

**Mục đích:** File thô → danh sách **Atomic Blocks** theo format. Registry Port+Strategy: thêm format = thêm 1 file, không sửa orchestrator ([registry.py:8-16](rag-crm/src/ragbot/infrastructure/parser/registry.py)).

**Registry thật** ([registry.py:45-61](rag-crm/src/ragbot/infrastructure/parser/registry.py)): `null, kreuzberg_markdown, excel_openpyxl, google_sheets, pdf, docx, markdown, vlm_image`.
- `kreuzberg_markdown` (pdf/pptx/html → MARKDOWN) precedence trước `pdf` legacy; fail-soft: kreuzberg absent → NullParser → tụt xuống `pdf` (pypdfium2).
- `excel_openpyxl`/`google_sheets` = **row-as-chunk** (1 row → 1 chunk) giữ nguyên end-to-end.
- `vlm_image` KHÔNG auto-fire (no-arg probe raise TypeError → skip); worker chọn tường minh khi VLM bật.

**Kỹ thuật cốt lõi — Expert Code Chunking (blueprint):** AST/Tree-sitter; Functional Integrity (không cắt đôi hàm); Scope Enrichment (đính import/global/class signature); tách code/comment.

**Bug/pitfall theo bước:**
- **Bảng bị nhận nhầm thành Text** (HIGH) → mất cấu trúc → sai số liệu.
- **Code sai cú pháp → AST không parse** (MEDIUM) → cần fallback cắt dòng.
- **Excel đọc trúng hàng ngàn dòng trống** (MEDIUM) → cần bounding-box/table-detection.
- **Rủi ro OOM** (HIGH) — PDF scan vài trăm MB / Excel triệu dòng → cần Worker Pool + Streaming.
- *(Các "Parser chỉ là stub `pass`" là blueprint; code production đã có parser thật.)*

### 3.3. Luồng 1.3 — Guardrail & Chunking Executor (U3 clean, U4 chunk)

**Mục đích:** Chốt strategy chunking (Tầng 4) rồi cắt khúc (Tầng 5), giữ Atomic Integrity.

**Router thật = `select_strategy` deterministic weighted scorer** (KHÔNG phải Port LLM), logic [analyze.py:407-541](rag-crm/src/ragbot/shared/chunking/analyze.py):
- Fast-path CSV → `table`; Fast-path VN legal/admin (Chương/Mục/Điều/Phần promote heading) → `hdt` ([analyze.py:462-463](rag-crm/src/ragbot/shared/chunking/analyze.py)); ambiguous prose → weighted scorer HDT/Semantic/Recursive/Hybrid/Proposition; confidence < threshold → fallback `recursive`.
- Strategy names thật ([_11_table_csv_chunking_strategy.py:43-47](rag-crm/src/ragbot/shared/constants/_11_table_csv_chunking_strategy.py)): `hdt, semantic, recursive, hybrid, proposition` + `table_csv, table_dual_index, parser_preserve`.

**⚠️ Đính chính quan trọng (Tầng 4 LLM Selector):** blueprint mô tả "AdapChunk Tầng 4 — LLM Strategy Selector Port đang chạy". **Code thật: Port này TẮT hoàn toàn** (disabled-by-comment, **zero runtime caller**) — cả 4 file `infrastructure/chunking_strategy/` mở đầu `# DISABLED — UNUSED`. Routing hoàn toàn deterministic qua `select_strategy` + `apply_cross_check`. (Chi tiết Mục 7.4.3.)

**Tầng 5 cross-check (`apply_cross_check`)** — 5 rule ưu tiên ([analyze.py:576-675](rag-crm/src/ragbot/shared/chunking/analyze.py)): confidence thấp → `hybrid`; hdt thiếu heading → `semantic`; semantic block ngắn → `proposition`; proposition doc dài nhiều heading → `hdt`; mixed-content cao → warn-only.

**⚠️ Doc-vs-code (T5):** docstring ghi "default OFF" nhưng hằng số thật `DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED = True` ([_12_multi_stage_retrieval_fallba.py:148-149](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py)). **Code thắng: T5 mặc định BẬT.**

**Atomic Integrity:** `_ATOMIC_BLOCK_TYPES = {"table","formula","image","code"}` ([blocks.py:140-146](rag-crm/src/ragbot/shared/chunking/blocks.py)); atomic block "route AROUND splitter" ([__init__.py:283-342](rag-crm/src/ragbot/shared/chunking/__init__.py)). **Nhưng gate `formula_image_atomic_protect_enabled` default FALSE** ([_00_app_env_taxonomy.py:126](rag-crm/src/ragbot/shared/constants/_00_app_env_taxonomy.py)) — cơ chế bảo vệ chủ động mặc định TẮT; atomic vẫn được bảo toàn ở tầng lưu trữ qua `original_content` verbatim + test pin [test_ingest_original_content_persist.py:25-63](rag-crm/tests/unit/test_ingest_original_content_persist.py). Coverage guard OBSERVE-only ([ingest_stages.py:889-890](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py)).

**Bug/pitfall theo bước:**
- **Khối nguyên tử bị cắt đôi ở ranh giới** (HIGH) → dấu hiệu: số block trước ≠ sau.
- **PROPOSITION Token Explosion + Latency** (HIGH) trên doc dài phân cấp.
- **Chunk HDT > 1000 token tràn context window Embedding** (MEDIUM).
- **High Latency khi nạp file do gọi LLM đồng bộ** (HIGH) → cần Async/Queue.

### 3.4. Luồng 1.4 — Narrate then Embed & Storage (U5 enrich, U6 vn_segment, U7 embed_store)

**Mục đích:** Table/LaTeX/ảnh → văn xuôi để embed, tách Search vs Generation, giữ nội dung gốc trong metadata.

**Narrate dispatch** ([narrate_dispatch.py](rag-crm/src/ragbot/application/services/narrate_dispatch.py)): phân loại block-type mỗi chunk (dùng chung `_split_into_blocks_with_atomic` để đồng bộ chunker), TABLE→linearize, FORMULA→LaTeX-to-prose, IMAGE→OCR-desc; fan-out `asyncio.gather` + `Semaphore` giữ thứ tự.

**"The Swap Trick" — 3 danh sách tách biệt trong U7** ([ingest_stages_store.py](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)):
1. `texts_to_embed` = narrated (vào **encoder**, [:302](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py) + passage_prefix [:327](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)) — KHÔNG lưu vào cột `content`.
2. `persist_chunks[idx]["content"]` = text hiển thị/BM25/rerank (post-CR enriched, [:809](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)).
3. `metadata_json.original_content` = raw PRE-transform (pre-CR, pre-narrate), key `CHUNK_METADATA_KEY_ORIGINAL_CONTENT="original_content"` ([_18_admin_all_tenants_analytics_.py:209](rag-crm/src/ragbot/shared/constants/_18_admin_all_tenants_analytics_.py)); ghi JSONB qua `upsert_chunks` ([pgvector_store.py:146-159](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)).

→ **Decoupling đúng:** vector tính trên bản narrated, nhưng row lưu bản gốc → không bao giờ mất bản gốc để citation/LLM reconstruct.

**⚠️ Doc-vs-code (T7 Narrate):** flag `narrate_then_embed_enabled`. In-code default `= True` ([_20_cag_mode_cache_augmented_gen.py:81](rag-crm/src/ragbot/shared/constants/_20_cag_mode_cache_augmented_gen.py)); comment worker nói "DEFAULT OFF"; alembic 0230 seed `false` rồi 0234 re-enable `true` — **cả hai đều archive pre-squash**. Post-squash: grep `alembic/versions/` = **0 hit** → runtime rơi về in-code default `True` → **T7 mặc định BẬT trên worker**. **Cần verify runtime** (`SELECT ... FROM system_config`) trước khi chốt ON/OFF trên DB cụ thể. Degrade-safe: narrate lỗi/timeout → raw embed-target; `narrate_service is None` → identity passthrough.

**Embedders (Port+Strategy):** 4 provider thật ([embedding/registry.py:34-40](rag-crm/src/ragbot/infrastructure/embedding/registry.py)) — `LiteLLMEmbedder` (default `"litellm"`), `JinaEmbedder`, `ZeroEntropyEmbedder`, `BkaiVnEmbedder` (flag-gated); trên đĩa 6 file (thêm `openai_embedder.py` + `null_embedder.py`).

**Bug/pitfall theo bước:**
- **Non-text Embedding Blindspot** (HIGH) — embed thẳng Markdown/LaTeX → vector trượt (đã giải bằng narrate).
- **Metadata thiếu trường bắt buộc khi upsert** (MEDIUM) → phá Swap Trick + truy vết.
- **Chi phí API tăng vọt khi narrate hàng triệu trang** (HIGH) → cần cache/pacing.
- **Language embedding override không check dimension** (MEDIUM) — chỉ swap model NAME, giữ `dimension` ([__init__.py:426-462](rag-crm/src/ragbot/application/services/document_service/__init__.py)) → dim lệch, HNSW crash.

---

## 4. LUỒNG B — RETRIEVAL & SERVING PIPELINE (Truy xuất & Trả lời)

Gồm 3 luồng con 2.1→2.3.

### 4.1. Luồng 2.1 — Tiếp nhận Query & Hybrid Search

**Mục đích:** Nhận câu hỏi, rewrite (tối ưu), truy xuất bằng hybrid search.

**Các bước:** (1) **Query Rewriter** — log `Original` vs `Rewritten` để kiểm biến đổi sai ý; (2) **Hybrid Search** — Vector (Dense HNSW) ∥ BM25 (Sparse) trong **một câu SQL 3 CTE** (`dense`/`sparse`/`fused`, [pgvector_store.py:526-565](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)); (3) **Scores Discrepancy** — log Dense vs Sparse trước RRF; (4) **RRF fuse**.

**Thành phần:** `pgvector_store.py::hybrid_search()` + `pg_bm25_retrieval.py` + `lexical_registry.py`.

**⚠️ Điểm dễ hiểu sai nhất — 3 lớp RRF (chi tiết Mục 7.1):**
- RRF **dense⊕sparse trong SQL** — dùng param `rrf_k` của `hybrid_search`, node **KHÔNG truyền** → khóa cứng `DEFAULT_RRF_K=60`, **KHÔNG tunable**.
- RRF **fuse multi-query/decompose (Python)** — key `rag_rrf_k`, **tunable**.
- RRF **fuse vector-branch ⊕ lexical-branch (Python)** — key `lexical_rrf_k`, **tunable**.

**Bug/pitfall theo bước:**
- **BM25 ASCII-fold** (HIGH) — "từ"→"tu" (nhánh sparse bỏ dấu tăng recall, hạ precision ngôn ngữ nhạy-dấu). Chi tiết Mục 7.1 Điểm nóng 2.
- **Query Rewriter biến đổi sai ý** (MEDIUM) → cần log so sánh.

### 4.2. Luồng 2.2 — Reranking & Metadata Injection ("The Swap Trick" serving-side)

**Mục đích:** Lọc chunk bằng reranker và **tráo văn bản Narrate bằng dữ liệu gốc** trước khi gửi LLM.

**Các bước:** (1) **Reranker** (Cohere/BGE/ZeroEntropy) — drop chunk điểm thấp; (2) **Metadata Injection / Swap Trick** — thay `vector_text` bằng `original_content` (Bảng Markdown/LaTeX gốc) trong prompt; (3) nếu lỗi swap → LLM trả lời chung chung thay vì số chính xác.

**Kỹ thuật:** **Cliff rerank filter** (`gap_cut` + floor 0.05 + `min_keep=3`, alembic 0181 nâng 1→3) — luôn giữ top-3, không bao giờ context rỗng. Cross-encoder chấm cặp `(query, chunk)` → top-7.

**Bug/pitfall theo bước:**
- **Swap Trick thất bại** (**CRITICAL**, blueprint) — điểm dễ lỗi nhất; sai số liệu. *(Chưa có `file:line` verify cụ thể — blueprint.)*
- **Reranker under-rank vs BM25, gap 79.7%** (HIGH, real case) → cần safety-net giữ top-2 retrieval.
- **Reranker drop-rate cao** (MEDIUM).

### 4.3. Luồng 2.3 — Generation & Evaluation

**Mục đích:** LLM tạo câu trả lời + tự chấm (LLM-as-a-judge).

**Các bước:** (1) **LLM Generator** — log tổng token tránh "Lost in the Middle"; (2) **RAG Triad Evaluator** (Ragas/TruLens); (3) **Faithfulness < 0.8** → cảnh báo Hallucination; (4) **Answer Relevance < 0.7** → lạc đề.

**Thành phần:** `generate.py` (async `generate` line 175), `evaluation/ragas_metrics.py`.

**Bug/pitfall theo bước:**
- **Lost in the Middle** (HIGH) do prompt quá dài.
- **Hallucination khi Faithfulness < 0.8** (HIGH).
- **Câu trả lời lạc đề khi Answer Relevance < 0.7** (MEDIUM).

---

## 5. LUỒNG C — QUERY END-TO-END (Request → Response)

Luồng lõi, được verify nhiều nhất. Trình bày theo sequence: HTTP STEP 0–11 → LangGraph Q0–Q17.

### 5.1. STEP 0–11 trong `chat_stream.py` (kèm trạng thái verify)

| STEP | Line | Nội dung | Trạng thái verify |
|------|------|----------|-------------------|
| **STEP 0** | 87–107 | **Tenant Authority** — đọc `record_tenant` từ `request.state` (JWT, không body); None → 403 fail-closed | **Partial**: CÓ None-check (97–99), nhưng đọc thẳng `request.state.record_tenant_id` (không `getattr`) → middleware không chạy = AttributeError/500 chứ không 403 sạch; **không validate tenant tồn tại DB** |
| **STEP 1** | 108–126 | **Streaming Feature Flag Gate** — AND 2 kill-switch (`streaming_response_enabled` + legacy `streaming_enabled`), default=True | **Confirmed** (118–127). 2 `await` tuần tự = 2 Redis round-trip |
| **STEP 2–3** | 129–144 | **4-Key Bot Resolve** — `resolve_workspace_id()` + `bind_request_context()` (GUC RLS) + `registry.lookup(4-key)` Redis L1 | Claim "cache hit ~5%" → **Refuted**: Singleton warm-all-bots lúc boot, TTL 3600s, single-flight |
| **STEP 4** | 155–170 | **Load History** — `HistoryReconciler.get_messages(...)`; fail → empty list | **Confirmed (nặng hơn)**: 2 lớp swallow; lớp trong `history_reconcile.py:149-167` `except SQLAlchemyError: return []` KHÔNG log → multi-turn hỏng âm thầm |
| **STEP 5** | 189–207 | **Request Log Create** — `content_hash_required` SHA256 → `create_request_log(...)` | Confirmed |
| **STEP 6** | 208–234 | **Pipeline Config Load** — `_build_pipeline_config_for_stream()` | Claim "65 Redis call ~975ms" → **Refuted**: thực tế **172 key qua 1 MGET** (`get_many`→`_pipeline_config.py:375`→`system_config_service.py:155 mget`) — batched đúng chuẩn |
| **STEP 7–9** | 238–323 | **Graph Build + Initial State** — `StepTracker`, `get_graph()` singleton async-locked (30+ DI), `build_chat_initial_state(...)` | Graph build **không timeout** (HIGH) — treo nếu DI/lock hang |
| **STEP 10** | 335–376 | **Graph Execution** — `graph.ainvoke()` trong `asyncio.wait_for(timeout_s)`; Timeout → cancel + 504 | `ainvoke()` **in-request** → p95 ~100s chờ LLM |
| **STEP 11** | 390–467 | **SSE Stream Response** — `_on_complete()` gather `_finalize_log`+`_save_history`; `StreamingResponse`, `text/event-stream`, `Cache-Control: no-cache` + `X-Accel-Buffering: no` | SSE sink không giới hạn size động; TTFB không persist |

**`message_id`** (STEP 7–9): claim "collision là CRITICAL" → **Partial** — non-unique về giá trị (`int(time.time()*1000)`, `chat_stream.py:148`), NHƯNG **không phải khóa unique** (PK là `request_id` UUID; `message_id` chỉ index thường `ix_reqlog_tenant_message`) → **trùng vô hại**; và **không được log** → "opaque in logs" cũng sai.

### 5.2. LangGraph nodes Q0–Q17 (đánh dấu node lỗi)

> **Đính chính:** canonical là **Q0/Q0.5 + Q1→Q17 (adaptive → Q32)**; **KHÔNG có node "Q18"**. Bảng giữ đánh số tài liệu để đối chiếu.

| Node | Tên | Chức năng | Bug? |
|------|-----|-----------|------|
| **Q0** | IDENTITY_VALIDATE | Pydantic validate body + JWT | — |
| **Q1** | GUARD_INPUT | Length, injection, PII, too_short (LocalGuardrail precompiled) | Regex compile overhead/turn |
| **Q1'** | cache_check_and_understand_parallel | Song song cache + understand + speculative retrieve/MQ | 🔴 **Nhiều bug — xem 5.3** |
| **Q2** | CHECK_CACHE | L1 Redis exact-hash, L2 pgvector semantic @0.97 | Hit rate thấp; **intent cache thiếu tenant_id trong key → collision cross-tenant** (MEDIUM) |
| **Q3** | UNDERSTAND_QUERY | Intent classifier (LLM) + condense history | LLM/turn (+200ms); conf<0.7 → fallback "factoid" |
| **Q4** | REWRITE | HyDE + paraphrase fanout (speculative MQ) | 🔴 **Speculative embed thiếu đọc `embed_degraded`** |
| **Q5** | DECOMPOSE | Structured sub-query (LLM riêng, multi_hop) | Tách khỏi Q3 (+600ms); nên merge |
| **Q6** | RETRIEVE | Hybrid dense+BM25+RRF → top-20 | 🔴 **mega-node god object**; **BM25 ASCII-fold**; **`_reconcile_cross_doc` dùng `id()`** |
| **Q7** | GRAPH_RETRIEVE | Knowledge graph edges | **DISABLED** (`graph_rag_default_mode=disabled`) |
| **Q8** | FILTER_MIN_SCORE | Cliff adaptive, `min_keep=3` | — |
| **Q9** | MMR_DEDUP_PRE | Near-duplicate removal | ⚠️ **Xem đính chính 5.3** (doc nói dùng `id()`; code thật: KHÔNG có MMR node dùng `id()`) |
| **Q10** | RERANK | Cross-encoder zerank-2 → top-7 | Under-rank vs BM25 (gap 79.7%) |
| **Q11** | MMR_DEDUP_POST / neighbor_expand | Post-rerank dedup / mở rộng parent | — |
| **Q12** | GRADE | CRAG 3-state (relevant/irrelevant/ambiguous), LLM | +300ms; grade result không persist |
| **Q13** | REWRITE_RETRY | graded=[] → rewrite+retry (CRAG loop, cap 1–2) | — |
| **Q14** | GENERATE | LLM `gpt-4.1-mini` → answer (streaming) | 🔴 **Streaming token KHÔNG vào token_ledger**; **citation không verify chunk tồn tại** |
| **Q15** | GUARD_OUTPUT | Numeric-fidelity + brand/claim + grounding + PII redact | Phần lớn OBSERVE default (Mục 7.2) |
| **Q16** | REFLECT | Grounding judge sentence-level; **skip intent ngoài 4 loại** | **Skip theo intent → không chống fabrication**; grounding degrade silent |
| **Q17** | PERSIST | Ghi semantic_cache + conversations + messages + request_logs + steps + outbox (exactly-once) | Không batch → DB bloat; streaming path không ghi token_ledger |

### 5.3. Chi tiết bug node đã verify (nhóm `query_graph`)

**🔴 Q1' speculative embed KHÔNG check `embed_degraded`** → **Confirmed (CRITICAL)**: `_embed_query` (1541) chỉ GHI `state["embed_degraded"]=True` tại 1655 khi embed lỗi; `_run_speculative_retrieve` (1701–1808) chỉ check `if not raw_embed: return [],[]` (1721) — KHÔNG đọc cờ. Grep toàn `src/`: cờ SET tại 1655 nhưng **0 reader** (chỉ decl `state.py:231`) → **dead-write**.

**🔴 `_reconcile_cross_doc` dùng `id()` dedup** → **Confirmed (CRITICAL)**: 488 `anchor_ids={id(a)...}`; 491 `if id(e) in anchor_ids: continue`; 502 `absorbed.add(id(e))`; 504 `return [e for e in entities if id(e) not in absorbed]`.

**⚠️ Đính chính doc↔code (Q9 MMR):** tài liệu debug nói MMR dedup node cũng dùng `id()`. **Code thật: grep toàn `query_graph.py` cho `id(` chỉ 4 hit, TẤT CẢ ở `_reconcile_cross_doc` (488/491/502/504). KHÔNG tồn tại MMR node dùng `id()`.** Bug là thật nhưng chỉ 1 vị trí. (Chi tiết Mục 7.3 Bug #2.)

**Confirmed khác:** timeout asymmetry (cache/understand không bọc `wait_for`; speculative có, 1846–1848/1874–1877); unreachable MQ dedup khi decompose active (gate 2080–2082 trước dedup 2248–2269, cố ý).

**Refuted/Partial:** "RRF hardcoded" → **Refuted** (tunable `rag_rrf_k`/`lexical_rrf_k`); "5 gates MQ" → **Refuted** (9 điểm `return []`); "build_graph God object" → **Partial** (2048 dòng đúng, là **function/closure factory**, không phải object); "citation regex over-permissive" → **Partial** (regex lỏng thật, "tác hại" là suy luận); "asymmetric cleanup orphan" → **Partial** (asymmetric thật, KHÔNG leak).

### 5.4. Chi tiết bug guardrail/citation (nhóm `guardrail_persist`)

**✅ `grounding_check` over-permissive** → **Confirmed**: `_CITATION_MARKER_RE=r"\[[a-zA-Z0-9_\-]{1,64}\]"` (local_guardrail.py:69); Pass 1 line 394 `if _CITATION_MARKER_RE.search(answer): return None` → có marker `[xxx]` là grounded ngay, KHÔNG so `chunk_id`.

**✅ Silent guardrail persist failure** → **Confirmed**: `_persist` (948–964) `except Exception: pass` — nuốt lỗi, không log. *(Ở `local_guardrail.py`, KHÔNG phải `persist.py`.)*

**❌ Async weak-ref leak trong `persist.py`** → **Refuted**: code đã fix đúng — `_BG_CACHE_TASKS: set=set()` (46) strong-ref + `add_done_callback(_BG_CACHE_TASKS.discard)` (228). Không leak.

---

## 6. BẢN ĐỒ BUG THEO LUỒNG

### 6.1. Bảng tổng hợp (severity | luồng/node | verdict | file:line)

| # | Bug | Severity | Luồng/Node | Verdict | file:line |
|---|-----|----------|-----------|---------|-----------|
| 1 | Speculative embed thiếu đọc `embed_degraded` → HALLU (dead-write) | CRITICAL | Q1'/Q4 | ✅ **Confirmed** | [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py) · [state.py:231](rag-crm/src/ragbot/orchestration/state.py); 0 reader |
| 2 | `_reconcile_cross_doc` dùng `id()` dedup | CRITICAL/HIGH | Q6/stats | ✅ **Confirmed** | [query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py) |
| 3 | ~~MMR dedup dùng `id()`~~ | — | Q9 | ❌ **Refuted (doc sai)** | Grep chỉ 4 hit `id(`, đều ở `_reconcile_cross_doc`; KHÔNG có MMR node |
| 4 | Silent guardrail persist failure (no audit) | CRITICAL | Guardrail persist | ✅ **Confirmed** | [local_guardrail.py:948-964](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) (logger sẵn [:79](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)) |
| 5 | Streaming token KHÔNG vào `token_ledger` | CRITICAL/HIGH | Q14/Q17 | ✅ Confirmed (theo tài liệu) | Chỉ ghi `model_invocations` (thiếu bot_id) → mất 30–50% cost |
| 6 | Citation không verify chunk tồn tại (chỉ check marker) | CRITICAL/HIGH | Q14/grounding | ✅ **Confirmed** | [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) · [:394](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) |
| 7 | Swap Trick thất bại → sai số liệu | CRITICAL | Luồng 2.2 | Chưa xác minh (blueprint) | Không có line ref cụ thể |
| 8 | Citation regex `_CITATION_RE` over-permissive | HIGH/MEDIUM | Q14/328 | ⚠️ **Partial** | [query_graph.py:328](rag-crm/src/ragbot/orchestration/query_graph.py); "tác hại" là suy luận |
| 9 | Embedding model mismatch chỉ log, không chặn | HIGH | Q6/retrieval | ✅ Confirmed (theo tài liệu) | `query_graph.py:754-779`; nên raise InvariantViolation |
| 10 | Async task orphan/asymmetric cleanup | HIGH/MEDIUM | Q1' | ⚠️ **Partial** | Asymmetric thật, KHÔNG orphan/leak (merge 1938–2003) |
| 11 | Async weak-ref memory leak (semantic cache) | CRITICAL (tài liệu) | persist.py | ❌ **Refuted** | `_BG_CACHE_TASKS` (46) + `add_done_callback` (228) đã đúng |
| 12 | `message_id` timestamp collision | CRITICAL (tài liệu) | STEP 7-9 | ⚠️ **Partial** | PK là `request_id` UUID → trùng vô hại; không log |
| 13 | Pipeline config 65 Redis call tuần tự ~975ms | CRITICAL (tài liệu) | STEP 6 | ❌ **Refuted** | 172 key qua **1 MGET** (batched) |
| 14 | Bot resolve cache hit rate ~5% | HIGH (tài liệu) | STEP 2-3 | ❌ **Refuted** | Singleton warm-all, TTL 3600s, single-flight |
| 15 | History load fail silent → multi-turn hỏng | HIGH | STEP 4 | ✅ **Confirmed (nặng hơn)** | `history_reconcile.py:149-167` swallow không log |
| 16 | RRF `rrf_k=60` hardcoded | MEDIUM (tài liệu) | Q6 | ⚠️ **Partial** | Tunable ở 2 lớp Python (`rag_rrf_k`/`lexical_rrf_k`); **nhưng lớp SQL dense⊕sparse THẬT bị khóa cứng 60** (Mục 7.1) |
| 17 | Multi-query "5 gates" | HIGH (tài liệu) | Q5-Q6 | ❌ **Refuted** con số | Thực tế **9 gate** |
| 18 | build_graph "God object" 2000+ dòng | HIGH/MEDIUM | build_graph | ⚠️ **Partial** | 2048 dòng đúng; là **function**, không object |
| 19 | Tenant context không validate | MEDIUM | STEP 0 | ⚠️ **Partial** | CÓ None-check; không validate DB, đọc thẳng `request.state` |
| 20 | Streaming feature flag AND 2 key | MEDIUM | STEP 1 | ✅ **Confirmed** | 2 await tuần tự (chưa gather) |
| 21 | Graph build không timeout | HIGH | STEP 7-9 | ✅ Confirmed (theo tài liệu) | Cần `asyncio.wait_for` |
| 22 | `graph.ainvoke()` in-request, p95 ~100s | HIGH | STEP 10 | Confirmed (design debt) | Nên async worker |
| 23 | REFLECT skip intent → không chống fabrication | HIGH | Q16 | Confirmed | `DEFAULT_GROUNDING_INTENTS` 4 loại; ngoài → skip |
| 24 | BM25 ASCII-fold "từ"→"tu" | HIGH | Q6 | ✅ Confirmed | [pgvector_store.py:416](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py),[:454](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py); [vi_tokenizer.py:193-204](rag-crm/src/ragbot/shared/vi_tokenizer.py) |
| 25 | Bất đối xứng 2 đường BM25 | MEDIUM | Q6 | ✅ Confirmed | `hybrid_search` fold+segment vs `PgBM25Retrieval` không ([pg_bm25_retrieval.py:108-123](rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py)) |
| 26 | Language embedding override không check dimension | MEDIUM | Ingestion | Confirmed | `document_service/__init__.py:426-462` |
| 27 | State mutation ngầm, không schema (`total=False`) | MEDIUM/HIGH | Xuyên suốt | Confirmed | 50+ mutation ngầm |
| 28 | RLS provisioned nhưng inert (DSN superuser) | CRITICAL | Tenant isolation | ✅ **Confirmed** | 20 bảng FORCE RLS + 21 policy; hook no-op dưới superuser tới Phase 3 flip |
| 29 | 251 broad-except `# noqa BLE001` | MEDIUM | Xuyên suốt | Confirmed (baseline) | "sweep success" chỉ tắt tiếng linter |
| 30 | `embed_degraded` write không sống qua node-boundary | CRITICAL (gốc rễ #1) | embed closure | ✅ **Confirmed** | In-place mutation ngoài dict `return` → LangGraph không merge |
| 31 | Numeric-fidelity/brand/claim/grounding default OBSERVE | HIGH | Q15/guard_output | ✅ Confirmed | Mục 7.2 (default log-and-ship, không block) |
| 32 | T5 cross-check "default OFF" (comment) nhưng constant=True | LOW (doc-vs-code) | Ingestion U4 | ✅ Confirmed | [_12_multi_stage_retrieval_fallba.py:148-149](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py) |
| 33 | T7 narrate "DEFAULT OFF" (comment) nhưng post-squash không seed → BẬT | MEDIUM (cần runtime-verify) | Ingestion U7 | ✅ Confirmed code-evidence | [_20_cag_mode_cache_augmented_gen.py:81](rag-crm/src/ragbot/shared/constants/_20_cag_mode_cache_augmented_gen.py); grep alembic=0 |

### 6.2. Các claim BỊ BÁC BỎ — ĐỪNG SỬA NHẦM

Danh sách **REFUTED / PARTIAL** — nếu tin theo tài liệu gốc sẽ "tối ưu" thứ đã đúng hoặc sửa bug không tồn tại:

1. **[REFUTED] "65 Redis call tuần tự ~975ms"** — thực tế **1 MGET cho 172 key** (batched). Claim SAI cốt lõi nhất.
2. **[REFUTED] "Async weak-ref memory leak trong persist.py"** — code đã có `_BG_CACHE_TASKS` strong-ref + `add_done_callback`. Bug **không tồn tại**.
3. **[REFUTED] "Bot resolve cache hit rate ~5%"** — phỏng đoán không đo; Singleton warm-all + TTL 3600s.
4. **[REFUTED] "RRF hardcoded, không tunable"** — tunable qua `rag_rrf_k`/`lexical_rrf_k` (2 lớp Python). *(Sắc thái: lớp SQL dense⊕sparse THẬT bị khóa cứng 60 — đây là điểm cần fix, nhưng KHÔNG phải theo cách claim gốc mô tả — xem Mục 7.1.)*
5. **[REFUTED] "Multi-query 5 gates"** — thực tế **9 gate**.
6. **[REFUTED (doc sai)] "MMR dedup node dùng `id()`"** — grep chỉ 4 hit `id(`, đều ở `_reconcile_cross_doc`; KHÔNG có MMR node dùng `id()`.
7. **[PARTIAL] "message_id collision là CRITICAL"** — PK là `request_id` UUID → **trùng vô hại**.
8. **[PARTIAL] "build_graph God object"** — 2048 dòng đúng nhưng là **function**, không object.
9. **[PARTIAL] "Async task orphan"** — asymmetric thật, **không leak**.
10. **[PARTIAL] "Tenant không validate"** — CÓ None-check tường minh; chỉ thiếu validate DB.

> **Nhận định verifier:** người ra claim gốc **over-report bug**. Cần đọc code thật để lọc. Nhưng **các bug CRITICAL cốt lõi (id()-dedup, embed_degraded dead-write, silent guardrail persist, citation không verify chunk) đều Confirmed là thật.**

---

## 7. PHỤ LỤC DEEP-DIVE

> Giữ nguyên đầy đủ chi tiết & file:line. Bốn section này đọc code thật `rag-crm/src/`.

### 7.1. DEEP-DIVE 1 — RETRIEVE NODE (Q6): Mega-node truy xuất

> **Phạm vi bằng chứng:** [src/ragbot/orchestration/nodes/retrieve.py](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) (1986 dòng), [src/ragbot/orchestration/query_graph.py](rag-crm/src/ragbot/orchestration/query_graph.py) (3071 dòng), [src/ragbot/infrastructure/vector/pgvector_store.py](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py), [src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py](rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py), [src/ragbot/infrastructure/retrieval/lexical_registry.py](rag-crm/src/ragbot/infrastructure/retrieval/lexical_registry.py), [src/ragbot/application/services/multi_query_expansion.py](rag-crm/src/ragbot/application/services/multi_query_expansion.py), [src/ragbot/shared/vi_tokenizer.py](rag-crm/src/ragbot/shared/vi_tokenizer.py).

#### 0. Tổng quan — retrieve là god/mega-node

Hàm `retrieve()` khai báo tại [retrieve.py:210](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) và kết thúc ở [retrieve.py:1982](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) — **thân hàm ~1773 LOC**, trên tổng file 1986 dòng. Node đơn lớn nhất pipeline.

| Chỉ số | Giá trị đo được | Bằng chứng |
|---|---|---|
| LOC thân hàm `retrieve()` | ~1773 dòng | [retrieve.py:210](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)–[1982](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) |
| Số config-key đọc qua `_pcfg(state, ...)` | **46 key phân biệt** | `grep -oE '_pcfg\(state, "..."' \| sort -u` |
| Closure lồng bên trong node | **4** (`_race_vector`, `_embed_batch_queries`, `_run_hybrid_for_query`, `_mq_llm_complete`) | [retrieve.py:402](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [962](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1044](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1292](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) |
| Sub-step (step_tracker) mở bên trong 1 node | **5**: `retrieve`, `multi_query_fanout`, `rrf_fuse`, `retrieve_fallback`, `multistage_retrieval` | [retrieve.py:236](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1316](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1447](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1587](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1656](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) |

Số việc một node đảm nhiệm (mỗi việc là một khối `if` gate riêng): stats/structured routing + race, speculative-hit gate, VN preprocessing (abbrev expand), per-intent top_k, generic vocab expansion, metadata filter 3 tầng (LLM-intent + regex article-aware + Layer-3 LLM), multi-query fanout, decompose fan-out, batch embed, RRF fuse, metadata-relax retry, fallback-to-original, multistage fallback chain, diacritic-restore supplementary search, lexical/BM25 fuse, permission pre-filter, parent-child expansion, autocut, superlative enrichment. Đây là **anti-pattern god-object** — vi phạm Simplicity/Surgical của CLAUDE.md; sửa 1 hành vi phải đọc/kiểm cả 1773 dòng.

**Hướng xử lý:** tách các nhánh opt-in (multistage, diacritic-restore, parent-child, permission, superlative) ra collaborator theo Port+Strategy, để node chỉ điều phối — nhưng đây là T3-Refactor, defer sau T1.

#### Bước (1) — Input & DI dependencies inject qua `functools.partial`

Node được wire vào StateGraph tại [query_graph.py:2686](rag-crm/src/ragbot/orchestration/query_graph.py) `retrieve = functools.partial(_retrieve_node, ...)`. Đối chiếu chữ ký [retrieve.py:210-235](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) với khối partial [query_graph.py:2686-2710](rag-crm/src/ragbot/orchestration/query_graph.py):

- **1 positional:** `state: GraphState`.
- **22 keyword-dependency inject qua partial** (từ [query_graph.py:2688-2709](rag-crm/src/ragbot/orchestration/query_graph.py)):

| Nhóm | Dependencies | Số |
|---|---|---|
| Adapter/repo (Port) | `vector_store`, `lexical_retrieval`, `embedder`, `llm`, `model_resolver`, `redis_client`, `entity_extractor`, `metadata_filter_strategy`, `language_pack_service`, `stats_index_repo`, `doc_repo` | 11 |
| Helper closure/hàm | `_audit`, `_resolve_corpus_version`, `_embed_query`, `_prewarm_embedding_cache`, `_do_stats_lookup`, `_pcfg`, `_required_channel_type`, `_is_null_lexical`, `expand_parent_chunks`, `retry_hybrid_with_original`, `_parse_doc_type_vocabulary` | 11 |

→ **22 dependency thật** (11 adapter + 11 helper). DI thủ công qua closure-capture của `build_graph`, không qua container `providers.Singleton` trực tiếp — helper như `_do_stats_lookup` là **closure định nghĩa ngay trong `build_graph`** ([query_graph.py:2362](rag-crm/src/ragbot/orchestration/query_graph.py)), buộc thread ngược vào node vì import chéo sẽ circular import (docstring [retrieve.py:13-21](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)).

#### Bước (2)→(5) — Đường dense + sparse + RRF (2 lớp fusion khác nhau)

Điểm **dễ hiểu sai nhất** toàn pipeline: có **HAI lớp RRF ở HAI nơi**, dùng HAI config-key khác nhau, chỉ một trong hai tunable qua config.

**(2) Embed query vector:** qua closure `_run_hybrid_for_query` ([retrieve.py:1044](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)): `q_emb = ... await _embed_query(q_text, state)` ([retrieve.py:1057](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1093](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)). N>1 query → batch trước qua `_embed_batch_queries` ([retrieve.py:1416](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) — 1 round-trip HTTP, truyền `precomputed_embedding` xuống fan-out ([retrieve.py:1426](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)).

**(3)+(4)+(5-nội bộ DB) Dense (HNSW) + Sparse (BM25) + RRF ở tầng SQL:** `_run_hybrid_for_query` gọi `vector_store.hybrid_search(**_hs_kwargs)` ([retrieve.py:1166](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)). Bên trong [pgvector_store.py:357](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py) chạy **một câu SQL** 3 CTE ([pgvector_store.py:526-565](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)):
- **`dense` CTE** — HNSW cosine `ORDER BY {col} <=> CAST(:emb AS vector)` với `SET hnsw.ef_search` ([pgvector_store.py:404](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py), [531](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)); index `m=16, ef=64`.
- **`sparse` CTE** — BM25-approx `ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query), {_norm})` ([pgvector_store.py:447](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py), [541](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)).
- **`fused` CTE** — RRF trong SQL:
  ```sql
  (:vec_w / (:rrf_k + COALESCE(d.rank_d, :rrf_miss))) +
  (:bm25_w / (:rrf_k + COALESCE(s.rank_s, :rrf_miss))) AS rrf_score
  ```
  ([pgvector_store.py:556-557](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)). `:rrf_k` lấy từ **tham số hàm** `rrf_k: int = DEFAULT_RRF_K` ([pgvector_store.py:364](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)).

**(5-tầng Python) RRF ở tầng orchestration — key khác hẳn:** node fuse các nhánh query bằng `mq_rrf_merge_chunks` ([retrieve.py:1448](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)):
```python
rrf_k = int(_pcfg(state, "rag_rrf_k", DEFAULT_RRF_K))   # retrieve.py:1446
chunks = mq_rrf_merge_chunks(per_query_chunks, rrf_k=rrf_k)  # retrieve.py:1448
```
Fuse lexical/BM25-branch dùng key thứ hai:
```python
_lex_rrf_k = int(_pcfg(state, "lexical_rrf_k", DEFAULT_LEXICAL_RRF_K))  # retrieve.py:1793
chunks = mq_rrf_merge_chunks([chunks, _lex_hits], rrf_k=_lex_rrf_k)     # retrieve.py:1824-1826
```

**Bảng đối chiếu 3 điểm RRF:**

| RRF ở đâu | Config-key | Default (SSoT) | Tunable? | Bằng chứng |
|---|---|---|---|---|
| Fuse multi-query/decompose (Python) | `rag_rrf_k` | `DEFAULT_RRF_K = 60` | **CÓ** | [retrieve.py:1446](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), [1542](rag-crm/src/ragbot/orchestration/nodes/retrieve.py); [_00_app_env_taxonomy.py:224](rag-crm/src/ragbot/shared/constants/_00_app_env_taxonomy.py) |
| Fuse vector-branch ⊕ lexical-branch (Python) | `lexical_rrf_k` | `DEFAULT_LEXICAL_RRF_K = 60` | **CÓ** | [retrieve.py:1793](rag-crm/src/ragbot/orchestration/nodes/retrieve.py); [_17_pipeline_audit.py:45](rag-crm/src/ragbot/shared/constants/_17_pipeline_audit.py) |
| RRF dense⊕sparse **trong SQL** | *(param `rrf_k` của `hybrid_search`)* | `DEFAULT_RRF_K = 60` | **KHÔNG** (node không truyền) | [pgvector_store.py:364](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py), [556](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py) |

**Khẳng định/bác claim "rrf_k tunable hay hardcode":**
- Claim "tunable qua `rag_rrf_k`/`lexical_rrf_k`" — **ĐÚNG một phần**: chỉ đúng cho hai lớp fuse Python.
- **Code thật:** lớp RRF quan trọng nhất — dense⊕sparse **trong SQL** ([pgvector_store.py:556-557](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)) — **KHÔNG được node truyền `rrf_k`**. `_run_hybrid_for_query` xây `_hs_kwargs` ([retrieve.py:1100-1153](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) và `_port_kwargs` ([retrieve.py:1064-1077](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) **không có key `rrf_k`**. → tầng SQL luôn dùng default 60 cứng; muốn đổi phải sửa code, **không** flip qua `system_config`. Đây là "hardcode-de-facto" cho lớp fusion sát dữ liệu nhất.
- Lưu ý phụ: audit log [retrieve.py:1938](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) đọc `_pcfg(state, "rrf_k", ...)` (key `rrf_k`, không phải `rag_rrf_k`) — chỉ ghi log, không đi vào tính toán fusion; key "mồ côi" gây nhầm khi đọc log.

> `mq_rrf_merge_chunks` là RRF chuẩn Cormack 2009 `score(d)=Σ 1/(k+rank)`, dedup theo **`chunk_id`** (string) ([multi_query_expansion.py:565](rag-crm/src/ragbot/application/services/multi_query_expansion.py), [594](rag-crm/src/ragbot/application/services/multi_query_expansion.py)) — dedup này ĐÚNG. Đối lập với bug id() dưới.

#### Bước (6) — Stats / structured lookup (điểm vào SỚM NHẤT, trước cả vector)

Khối stats route ở đầu node [retrieve.py:261-678](rag-crm/src/ragbot/orchestration/nodes/retrieve.py), chạy **TRƯỚC** khi kiểm tra `vector_store is None` ([retrieve.py:679](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)). Trình tự parser (ưu tiên giảm dần) trên `original_query` ([retrieve.py:267](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)):
1. `_parse_range_query` (giá) → [retrieve.py:274](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)
2. `_parse_code_query` (mã spec, opt-in) → [retrieve.py:289](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)
3. `_parse_price_of_entity_query` (BUG-1 CONFLATE fix) → [retrieve.py:300](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)
4. `_parse_list_query` (liệt kê/đếm) → [retrieve.py:309](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)

Guard chặn stats route sai: structural-ref (`Điều/Khoản`) [retrieve.py:330-351](rag-crm/src/ragbot/orchestration/nodes/retrieve.py); decompose ≥2 sub → skip single-entity stats [retrieve.py:361-365](rag-crm/src/ragbot/orchestration/nodes/retrieve.py). Đủ confidence → hai chế độ:
- **Race** (opt-in `stats_index_race_enabled`): `_do_stats_lookup` ‖ `_race_vector` concurrent, `asyncio.wait(FIRST_COMPLETED)` ([retrieve.py:509-528](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)), stats thắng nếu cả hai xong ([retrieve.py:541-548](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) → return sớm ([retrieve.py:588](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)).
- **Sequential** (default): `_do_stats_lookup` → nếu có `linked_chunks` return sớm + seed `graded_chunks` để skip rerank/mmr/grade ([retrieve.py:629-670](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)); 0 chunk → fall-through xuống hybrid ([retrieve.py:642-677](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)).

`_do_stats_lookup` ([query_graph.py:2362-2684](rag-crm/src/ragbot/orchestration/query_graph.py)) route theo `operation`: `count`→`count_by_name_keyword` ([query_graph.py:2404](rag-crm/src/ragbot/orchestration/query_graph.py)), `keyword`→`query_by_name_keyword`/`list_all_entities` ([query_graph.py:2436](rag-crm/src/ragbot/orchestration/query_graph.py), [2450](rag-crm/src/ragbot/orchestration/query_graph.py)), `max/min`→`top_by_price` ([query_graph.py:2475](rag-crm/src/ragbot/orchestration/query_graph.py)), else `query_by_price_range` ([query_graph.py:2482](rag-crm/src/ragbot/orchestration/query_graph.py)). Kết quả build thành **1 synthetic chunk** `chunk_id = DEFAULT_STATS_SYNTHETIC_CHUNK_ID, score=1.0` ([query_graph.py:2630-2640](rag-crm/src/ragbot/orchestration/query_graph.py)) — **grounded DATA, không phải app-inject** (tuân QG#10, [query_graph.py:2548-2550](rag-crm/src/ragbot/orchestration/query_graph.py)).

#### Bước (7) — Entity-grounded & metadata filter

**Metadata filter — 3 tầng chồng nhau, LLM-key thắng khi trùng:**
1. **LLM-intent** (`_extract_query_intent`) — chỉ khi `metadata_aware_retrieval_enabled` AND `metadata_extraction_enabled` ([retrieve.py:836-847](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)).
2. **Regex article-aware** (`metadata_filter_strategy.extract`) — DI-inject qua Port (NullFilter mặc định), key regex chỉ thêm khi chưa có key LLM ([retrieve.py:877-899](rag-crm/src/ragbot/orchestration/nodes/retrieve.py); registry [metadata_filter/registry.py:39](rag-crm/src/ragbot/infrastructure/metadata_filter/registry.py)).
3. **Layer-3 LLM** (`_L3Extractor`) — OFF mặc định (`DEFAULT_METADATA_LAYER3_LLM_ENABLED`), chỉ fire khi tầng 1/2 rỗng ([retrieve.py:914-960](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)).

**Entity-grounded expansion:** khi `entity_extractor is not None` AND `entity_grounding_enabled` ([retrieve.py:1328-1331](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) → dùng `mq_expand_query_with_entities` thay `mq_expand_query` ([retrieve.py:1335](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)). Default `DEFAULT_ENTITY_GROUNDING_ENABLED = False`, `MAX_ENTITIES = 3` ([_11_table_csv_chunking_strategy.py:317-318](rag-crm/src/ragbot/shared/constants/_11_table_csv_chunking_strategy.py)). Extractor qua registry Port ([entity_extractor/registry.py:38](rag-crm/src/ragbot/infrastructure/entity_extractor/registry.py)) — mặc định null.

#### Bước (8) — Dedup

Dedup nhiều chỗ, tất cả (trừ 1 bug) đều **theo `chunk_id` string**:
- RRF merge: dedup theo `chunk_id` ([multi_query_expansion.py:594-599](rag-crm/src/ragbot/application/services/multi_query_expansion.py)) — ĐÚNG.
- Decompose-stats join: `_have = {chunk_id...}` ([retrieve.py:1486-1489](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) — ĐÚNG.
- Multistage merge: `_existing_ids = {chunk_id or id}` ([retrieve.py:1690-1698](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) — ĐÚNG.
- Diacritic-restore merge: `existing_ids` theo `chunk_id/id` ([retrieve.py:1761-1769](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) — ĐÚNG.
- `_stats_chunks_for_sub_queries`: `seen` theo `chunk_id` ([retrieve.py:187-192](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) — ĐÚNG.

#### ĐIỂM NÓNG 1 — Bug `id()`-dedup ở `_reconcile_cross_doc` ([query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py))

**Cơ chế sai:** hàm merge fragment price-LESS vào anchor có giá, group theo alias digit-key. Dedup KHÔNG dùng `chunk_id` mà dùng **`id()` — địa chỉ object Python:**
```python
anchor_ids = {id(a) for a, _ in anchors}     # query_graph.py:488
absorbed: set[int] = set()                    # query_graph.py:489
for e in entities:
    if id(e) in anchor_ids:                   # query_graph.py:491 — skip anchor
        continue
    ...
    absorbed.add(id(e))                        # query_graph.py:502
return [e for e in entities if id(e) not in absorbed]   # query_graph.py:504
```

**Tại sao là mùi/lỗi thiết kế:**
1. **Danh tính ngữ nghĩa bị thay bằng danh tính bộ nhớ.** Hai entity dict cùng logic (cùng `record_chunk_id`/`entity_name`) nhưng hai object khác nhau → `id()` khác → không nhận là "cùng thực thể". `id()` **KHÔNG ổn định giữa process/turn** và **có thể bị CPython tái sử dụng** sau GC. Ở đây `entities` giữ sống suốt hàm nên tạm ổn — **nhưng** hợp đồng ngầm dễ vỡ: chỉ cần refactor xây lại list (ví dụ `entities = [dict(e) for e in entities]` phía trên) là `anchor_ids` ([488](rag-crm/src/ragbot/orchestration/query_graph.py)) trỏ object CŨ, vòng lặp [490](rag-crm/src/ragbot/orchestration/query_graph.py) duyệt object MỚI → `id(e) in anchor_ids` luôn False → **anchor bị coi là fragment và có thể tự absorb/nhân đôi**.
2. **Không idempotent qua ranh giới serialize.** Entities từng qua JSON (cache/queue) rồi deserialize → object identity mất, dedup thành no-op im lặng.
3. **Rủi ro aliasing.** `_absorb_fragment_attrs(a, e)` ([query_graph.py:501](rag-crm/src/ragbot/orchestration/query_graph.py) → def [query_graph.py:331](rag-crm/src/ragbot/orchestration/query_graph.py)) **mutate tại chỗ** `anchor["attributes_json"]`.

**Hướng xử lý:** thay `id(e)` bằng khóa nội dung ổn định (ví dụ `str(e.get("record_chunk_id") or e.get("entity_name"))`) cho cả `anchor_ids`/`absorbed`, hoặc build anchor set bằng chỉ số vị trí.

#### ĐIỂM NÓNG 2 — BM25 ASCII-fold ("từ" → "tu"): mất phân biệt dấu

**Cơ chế:** nhánh sparse của `hybrid_search` tạo **thêm** biến thể query bỏ dấu:
```python
_stripped_for_sparse = strip_vn_filler_tokens(query_text) or query_text  # pgvector_store.py:415
normalized_query = remove_diacritics(_stripped_for_sparse)               # pgvector_store.py:416
```
`remove_diacritics` ([vi_tokenizer.py:193-204](rag-crm/src/ragbot/shared/vi_tokenizer.py)) chạy `unicodedata.normalize("NFKD", ...)` rồi loại combining char → **"từ"→"tu", "tài liệu"→"tai lieu"**. Biến thể đi vào predicate OR:
```sql
search_vector @@ websearch_to_tsquery('simple', :query)
OR search_vector @@ websearch_to_tsquery('simple', :query_normalized)
```
([pgvector_store.py:452-455](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)).

**Vì sao điểm nóng:** tiếng Việt dùng dấu phân biệt nghĩa — "từ" vs "tu", "má" vs "ma" vs "mà". OR-branch bỏ dấu match trên `search_vector` (index parser `'simple'`, cũng không hiểu dấu) → query có dấu vẫn kéo về chunk chỉ khớp phần **không dấu** → nhiễu ranking. Đây là đánh đổi recall-vs-precision cố ý (docstring [vi_tokenizer.py:198-200](rag-crm/src/ragbot/shared/vi_tokenizer.py)), **không phải bug ngẫu nhiên**, nhưng là **điểm nóng chất lượng** với corpus nhạy-dấu.

**Điểm nóng phụ — bất đối xứng 2 adapter BM25:**
- `hybrid_search`: CÓ nhánh bỏ dấu `:query_normalized` ([pgvector_store.py:454](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)) + tách compound `segment_vi_compounds` ([pgvector_store.py:409](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)).
- `PgBM25Retrieval.search` ([pg_bm25_retrieval.py:108-123](rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py)): **KHÔNG** bỏ dấu, **KHÔNG** segment — chỉ `websearch_to_tsquery('simple', :query)` trên query thô.

→ **Code thật:** hai đường BM25 cùng pipeline **xử lý dấu/segment KHÁC NHAU**. Query "từ chối" qua `hybrid_search` fold thành "tu choi", nhưng nhánh `lexical_retrieval.search` ([retrieve.py:1810](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) giữ dấu nguyên → hai tập ứng viên lệch nhau, rồi mới RRF-fuse ([retrieve.py:1824](rag-crm/src/ragbot/orchestration/nodes/retrieve.py)) — khó suy luận ranking cuối.

**Hướng xử lý:** thống nhất một chuẩn tiền xử lý sparse (segment + fold có kiểm soát) dùng chung cho cả `PgBM25Retrieval` và nhánh sparse của `hybrid_search`; hoặc gate biến thể bỏ dấu sau cờ per-bot để corpus nhạy-dấu tắt được.

#### Tóm tắt điểm nóng Q6

| # | Điểm nóng | Bản chất | Bằng chứng | Tunable? |
|---|---|---|---|---|
| 1 | `retrieve` mega/god-node | ~1773 LOC, 46 config-key, 4 closure, 5 sub-step, ~20 nhiệm vụ | [retrieve.py:210-1982](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) | — |
| 2 | RRF dense⊕sparse trong SQL không nhận config | node không truyền `rrf_k` → cứng 60 | [pgvector_store.py:364](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py),[556](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py); [retrieve.py:1064-1077](rag-crm/src/ragbot/orchestration/nodes/retrieve.py),[1100-1153](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) | **KHÔNG** |
| 3 | RRF fuse tầng Python (mq + lexical) | `rag_rrf_k`/`lexical_rrf_k`, default 60 | [retrieve.py:1446](rag-crm/src/ragbot/orchestration/nodes/retrieve.py),[1793](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) | **CÓ** |
| 4 | `_reconcile_cross_doc` dedup `id()` | danh tính bộ nhớ thay `chunk_id` | [query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py) | — |
| 5 | BM25 ASCII-fold "từ"→"tu" | OR-branch bỏ dấu tăng recall, hạ precision | [pgvector_store.py:416](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py),[454](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py); [vi_tokenizer.py:193-204](rag-crm/src/ragbot/shared/vi_tokenizer.py) | không |
| 6 | Bất đối xứng 2 đường BM25 | `hybrid_search` fold+segment, `PgBM25Retrieval` không | [pg_bm25_retrieval.py:108-123](rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py) vs [pgvector_store.py:409-455](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py) | — |

**Kết luận Q6:** claim "rrf_k tunable" cần đính chính — **chỉ tunable cho 2 lớp fuse Python; lớp RRF dense⊕sparse quan trọng nhất nằm trong SQL và bị khóa cứng 60 vì node không thread `rrf_k` xuống adapter**. Node là mega-node ~1773 LOC gánh ~20 nhiệm vụ với 22 dependency; hai lỗi thiết kế sát-dữ-liệu (`id()`-dedup và ASCII-fold bất đối xứng) là ứng viên T1-Smartness ưu tiên hơn tách refactor (T3).

*(CHƯA verify runtime — mọi nhận định là code-evidence tĩnh theo file:line; cần debug-trace + eval số thật để chấm coverage/faithfulness.)*

### 7.2. DEEP-DIVE 2 — CHỐNG HALLUCINATION (Hợp đồng HALLU=0)

> Phạm vi: toàn bộ chuỗi phòng thủ chống bịa số/bịa fact trên chat-graph. Wiring graph tại [query_graph.py:2903-3026](rag-crm/src/ragbot/orchestration/query_graph.py): `guard_input → understand → router → rewrite/decompose → retrieve → rerank → mmr_dedup → neighbor_expand → grade → generate → critique_parse → guard_output → reflect → persist`. Con số/mặc định trích từ `src/ragbot/shared/constants/`.

Hợp đồng "HALLU=0" ở tầng T1 (CORE MVP). Hệ dựng **9 lớp phòng thủ** nối tiếp. Đọc code cho thấy: **khung rất dày, nhưng lớp "sacred" thực sự khoá HALLU (grounding judge dạng block) lại default OFF/OBSERVE**, còn ba cơ chế quảng cáo chống-bịa thì hoặc chết (dead-write), hoặc chỉ khớp cú pháp, hoặc bị skip theo intent.

#### A. Luồng end-to-end — từng LỚP phòng thủ theo thứ tự thực thi

| # | LỚP phòng thủ | Node / file:line | Mục đích chống-HALLU | Mặc định | Đánh giá |
|---|---|---|---|---|---|
| Q1 | **Input guardrail** | [guard_input.py:38](rag-crm/src/ragbot/orchestration/nodes/guard_input.py) → [local_guardrail.py:796](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) | Chặn injection/SQLi/quá dài/rỗng trước LLM | ON | **Mạnh** injection; không trực tiếp chống bịa số. Chặn `severity=="block"` ([local_guardrail.py:845](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)) |
| Q2 | **CRAG grade** | [grade.py:88-162](rag-crm/src/ragbot/orchestration/nodes/grade.py) | Chấm relevance, loại chunk rác | ON | **Trung bình.** 3 đường skip (stats [:100](rag-crm/src/ragbot/orchestration/nodes/grade.py), high-score [:135](rag-crm/src/ragbot/orchestration/nodes/grade.py), timeout [:214+](rag-crm/src/ragbot/orchestration/nodes/grade.py)); mỗi lần tự trấn an "downstream grounding_check enforces HALLU=0" ([:98,116,208](rag-crm/src/ragbot/orchestration/nodes/grade.py)) — nhưng downstream default OBSERVE (Q13) |
| Q3 | **Refuse short-circuit (0 chunk)** | [generate.py:318-362](rag-crm/src/ragbot/orchestration/nodes/generate.py) | `graded==[]` → trả `oos_answer_template`, bỏ LLM | ON (`DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED=True`, [_04_jwt_auth.py:65](rag-crm/src/ragbot/shared/constants/_04_jwt_auth.py)) | **Mạnh** cho 0-chunk. 2 bypass: `chitchat` + `action_enabled` ([generate.py:338-343](rag-crm/src/ragbot/orchestration/nodes/generate.py)) |
| Q4 | **Temperature = 0** | [query_graph.py:1115](rag-crm/src/ragbot/orchestration/query_graph.py) đọc `DEFAULT_GENERATION_TEMPERATURE=0.0` ([_10_rbac.py:200](rag-crm/src/ragbot/shared/constants/_10_rbac.py)) | Greedy decoding giảm bịa ngẫu nhiên | 0.0 | **Mạnh (rẻ).** |
| Q5 | **Structured citation validation** | [generate.py:816-847](rag-crm/src/ragbot/orchestration/nodes/generate.py) | Drop `citations[].chunk_id` không trong `chunk_ids_allowed` ([generate.py:604-608](rag-crm/src/ragbot/orchestration/nodes/generate.py)) | ON khi structured-output bật | **Mạnh nhưng hẹp.** Nơi DUY NHẤT đối chiếu chunk_id thật, nhưng chỉ lọc **metadata citations**, KHÔNG sửa answer text |
| Q6 | **Regex citation validation (fallback)** | [generate.py:867-889](rag-crm/src/ragbot/orchestration/nodes/generate.py), `_CITATION_RE=\[chunk:([0-9a-f\-]+)\]` ([query_graph.py:328](rag-crm/src/ragbot/orchestration/query_graph.py)) | Parse `[chunk:UUID]`, giữ id trong `chunk_ids_allowed` | ON | **Trung bình.** Có đối chiếu id, nhưng chỉ đếm observability — không cắt answer text |
| Q7 | **Numeric-fidelity** | [guard_output.py:154-207](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) qua `classify_answer_numbers`+`detect_cross_row_misattribution` | Bắt số không có trong chunk / lấy nhầm hàng | **OBSERVE** ([_14:354](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)) | **Mạnh nhưng mặc định chỉ LOG.** Block khi owner set `numeric_fidelity_action="block"` ([guard_output.py:190](rag-crm/src/ragbot/orchestration/nodes/guard_output.py)). Deterministic |
| Q8 | **Brand-scope gate** | [guard_output.py:209-287](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) | Chặn phủ định phân phối sai | **OBSERVE** ([_14:363](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)) | **Log-only mặc định.** |
| Q9 | **Claim-fidelity** | [guard_output.py:289-340](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) | Bắt khẳng định mở rộng phạm vi phi số | **OBSERVE** ([_14:388](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)) + no-op nếu chưa seed phrase | **Log-only mặc định.** |
| Q10 | **Sysprompt-leak/secret scanner** | [local_guardrail.py:297-365](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py), gọi từ [guard_output.py:826](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) | Chống rò sysprompt/secret | ON (block) | Mạnh cho leak; ngoài HALLU số |
| Q11 | **Regex grounding_check** | [local_guardrail.py:367-414](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py), gate `citation_marker_required` ([guard_output.py:633,836](rag-crm/src/ragbot/orchestration/nodes/guard_output.py)) | Pass1 citation → Pass2 substring ≥20 → Pass3 numeric-overlap | **OFF** (`citation_marker_required=False`, [guard_output.py:633](rag-crm/src/ragbot/orchestration/nodes/guard_output.py)) | **Yếu bản chất** (Lỗ hổng #2). Mặc định không chạy |
| Q12 | **LLM grounding judge (sync)** | [local_guardrail.py:416-553](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py), gọi từ [guard_output.py:826-841](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) | Model chấm câu SUPPORTED/NOT vs context; ratio>threshold → hit | ON (`DEFAULT_GROUNDING_CHECK_ENABLED=True`, [_14:195](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)), threshold `0.3` ([_15:105](rag-crm/src/ragbot/shared/constants/_15_m2_neighbor_window_expansion.py)) | **"Trái tim" HALLU-net.** Nhưng (a) chỉ chạy intent trong `DEFAULT_GROUNDING_INTENTS` (Lỗ hổng #3); (b) hit chỉ tạo `severity="warn"/action="hitl"` ([:409-414,541-552](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)) — KHÔNG block trừ khi Q13 |
| Q13 | **Grounding confirmed-action (block gate)** | [guard_output.py:806-822](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) | Judge xác nhận ungrounded → thay answer bằng `oos_template` | **OBSERVE** (`DEFAULT_GROUNDING_CONFIRMED_ACTION=…OBSERVE`, [_14:327](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)) | **Mặc định KHÔNG chặn — flag-and-ship.** Block khi owner set `grounding_confirmed_action="block"` |
| Q13' | **Grounding fail-CLOSED (grounder chết)** | [guard_output.py:511-523,640-656](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) | Eligible NHƯNG `llm_fn is None` → refuse thay vì ship UNVERIFIED | **fail_closed** (`DEFAULT_GROUNDING_FAILURE_MODE=fail_closed`, [_14:314](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)) | **Mạnh (đúng hướng).** Chỉ cứu case grounder *chết* |
| Q14 | **Grounding async (ship-then-check)** | [guard_output.py:428-436,857-864](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) + [query_graph.py:845-960](rag-crm/src/ragbot/orchestration/query_graph.py) | Ship trước, chấm sau background | **OFF** (`DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED=False`, [_14:217](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)), chỉ `factoid`, top_score≥0.7 | **Đúng** khi OFF. Bật = đánh đổi HALLU lấy latency |
| Q15 | **Self-RAG reflect** | [reflect.py:41-182](rag-crm/src/ragbot/orchestration/nodes/reflect.py) | Judge keep/rewrite; thiếu-fact → rewrite retry | ON (`max_reflect_retries=1`, [_15:132](rag-crm/src/ragbot/shared/constants/_15_m2_neighbor_window_expansion.py)) | **Trung bình.** "smart-skip" bỏ retry khi đã grounded (Lỗ hổng #3b) |
| Q16 | **Persist/observability** | [persist.py], metric `grounding_fail_total` ([local_guardrail.py:540](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)) | Ghi guardrail_events, đẩy metric alerting | ON | Chỉ mạng an toàn *sau khi* user đã nhận answer |

**Tổng đánh giá luồng:** Điểm ENFORCE cứng (thay answer) chỉ tồn tại ở Q3 (0-chunk), Q7/Q8/Q9 (khi opt-in block) và Q13/Q13' (khi opt-in block / grounder chết). Với **cấu hình mặc định bot mới**, các lớp số hoá (Q7, Q13) đều **OBSERVE** — **HALLU-net mặc định phần lớn là "flag-and-ship", không phải "block"**. Đây là chủ ý thiết kế (tránh over-refuse, [guard_output.py:802-805](rag-crm/src/ragbot/orchestration/nodes/guard_output.py)), nhưng hệ quả: **"HALLU=0" chỉ đạt khi bot owner chủ động opt-in block** — không phải bảo đảm tự động.

#### B. 3 LỖ HỔNG phá vỡ HALLU=0

**Lỗ hổng #1 — `embed_degraded` là DEAD-WRITE.** Cờ được quảng cáo là chốt HALLU: embed fail → mark "degraded" để *"the answer path won't fabricate from a vector-less context"* ([query_graph.py:1650-1655](rag-crm/src/ragbot/orchestration/query_graph.py)). Thực tế **không đường answer nào đọc cờ**. Khai báo [state.py:231](rag-crm/src/ragbot/orchestration/state.py); nơi ghi DUY NHẤT [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py) `state["embed_degraded"] = True` (in-place mutation trong closure `_embed_query`); grep toàn `src/`+`tests/`: **0 reader**. Hai tầng gốc rễ: (1) không consumer; (2) ngay cả có consumer, write in-place không nằm trong dict `return` → LangGraph không merge qua node-boundary. *Lưu ý: doc cũ ghi vị trí `1500` — code thật hiện tại `1655`; bản chất dead-write không đổi.* **Hướng xử lý:** chuyển thành giá trị trong dict `return` của node embed, cho generate/guard_output đọc để ép refuse-short-circuit (hoặc siết grounding threshold) khi cờ bật — hoặc gỡ hẳn.

**Lỗ hổng #2 — Citation chỉ khớp CÚ PHÁP ngoặc vuông, KHÔNG đối chiếu `chunk_id`.** Pass 1 coi answer "grounded" ngay khi thấy bất kỳ token nào trong ngoặc. Regex [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) `_CITATION_MARKER_RE=re.compile(r"\[[a-zA-Z0-9_\-]{1,64}\]")` khớp `[abc]`,`[note]`,`[1]` … bất kỳ. Pass 1 [local_guardrail.py:393-395](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) `if _CITATION_MARKER_RE.search(answer): return None` — không so token với `retrieved_chunks[].chunk_id`, bỏ qua cả Pass 2 (substring) lẫn Pass 3 (numeric). Đối chiếu: nơi CÓ kiểm chunk_id thật là node `generate` với regex chặt hơn `_CITATION_RE=\[chunk:([0-9a-f\-]+)\]` ([query_graph.py:328](rag-crm/src/ragbot/orchestration/query_graph.py)), so `chunk_ids_allowed` ([generate.py:816-829,867-883](rag-crm/src/ragbot/orchestration/nodes/generate.py)) — nhưng chỉ lọc metadata citations, không cắt answer text. Giảm nhẹ vì `citation_marker_required=False` default. **Hướng xử lý:** Pass 1 trích token trong ngoặc, chỉ grounded khi khớp `chunk_id` thật; ngoặc rác rơi xuống Pass 2/3.

**Lỗ hổng #3 — Reflect grounding SKIP theo intent (và smart-skip theo score).**
- **(a) Intent-gating:** eligible [guard_output.py:369-390](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) `_grounding_eligible = _current_intent in DEFAULT_GROUNDING_INTENTS`; tập = `("factoid","comparison","aggregation","multi_hop")` ([_15:112-117](rag-crm/src/ragbot/shared/constants/_15_m2_neighbor_window_expansion.py)). **Mọi intent ngoài 4 loại đều SKIP** → `llm_fn=None` ([guard_output.py:438-445](rag-crm/src/ragbot/orchestration/nodes/guard_output.py)). Async chỉ áp `("factoid",)` ([_14:227](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py)). Rủi ro: intent phân loại sai (factoid→chitchat) không được grounding chạm tới.
- **(b) Reflect smart-skip** [reflect.py:148-176](rag-crm/src/ragbot/orchestration/nodes/reflect.py): bỏ rewrite nếu `reflect_skip_if_grounded` bật + `retries==0` + không có flag `llm_grounding_fail` + top score ≥ floor. Default an toàn `DEFAULT_REFLECT_SKIP_IF_GROUNDED=False` ([_15:148](rag-crm/src/ragbot/shared/constants/_15_m2_neighbor_window_expansion.py)). **Điểm yếu khi bật:** "đã grounded" dựa vào **vắng mặt flag** `llm_grounding_fail`, nhưng flag chỉ tồn tại khi judge eligible+chạy được. Intent skip (a) hoặc grounder OBSERVE (Q13) → không bao giờ có flag → smart-skip hiểu nhầm "không flag = grounded" → bỏ cơ hội rewrite dù answer có thể bịa.
**Hướng xử lý:** (1) coi grounding *không chạy* là "unknown", KHÔNG đồng nghĩa "grounded"; (2) mở rộng `DEFAULT_GROUNDING_INTENTS` hoặc fallback grounding khi retrieval có chunk; (3) với trap HALLU ép `grounding_confirmed_action="block"`.

#### C. Tổng kết hợp đồng HALLU=0 (cấu hình mặc định)

| Cơ chế | Mặc định | Chặn được answer bịa tới user? |
|---|---|---|
| Refuse short-circuit 0-chunk (Q3) | ON | **Có** — nhưng bypass chitchat/action |
| Temperature 0 (Q4) | 0.0 | Giảm xác suất, không đảm bảo |
| Citation validation (Q5/Q6) | ON | **Không** — chỉ lọc metadata, không sửa answer text |
| Numeric-fidelity (Q7) | OBSERVE | **Không** (log) trừ khi `block` |
| Brand/Claim (Q8/Q9) | OBSERVE + seed | **Không** (log) trừ khi opt-in |
| Regex grounding_check (Q11) | OFF + Pass1 lỏng | **Không** (Lỗ hổng #2) |
| LLM grounding judge (Q12) | ON nhưng gated intent | Chỉ warn-flag; skip intent ngoài 4 loại (Lỗ hổng #3a) |
| Grounding confirmed-action block (Q13) | OBSERVE | **Không** (flag-and-ship) trừ khi `block` |
| Grounding fail-closed (Q13') | fail_closed | **Có** — chỉ khi grounder *chết* |
| `embed_degraded` safety | dead-write | **Không** (Lỗ hổng #1) |

**Kết luận:** với bot mới cấu hình mặc định, "HALLU=0" **không được platform tự động cưỡng chế** ở mức "block answer bịa". Điểm cưỡng chế cứng mặc định chỉ: refuse 0-chunk (Q3) + fail-closed khi grounder chết (Q13'). Toàn bộ số-hoá chống bịa mặc định **OBSERVE = log-and-ship**, regex grounding_check **OFF** và Pass 1 lỏng. "HALLU=0" là **khả năng opt-in cho bot owner**, cộng ba lỗ hổng thu hẹp. File:line load-bearing: [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py), [:393-395](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py), [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py), [state.py:231](rag-crm/src/ragbot/orchestration/state.py), [guard_output.py:369-390](rag-crm/src/ragbot/orchestration/nodes/guard_output.py), [:806-822](rag-crm/src/ragbot/orchestration/nodes/guard_output.py), [reflect.py:148-176](rag-crm/src/ragbot/orchestration/nodes/reflect.py), [generate.py:816-889](rag-crm/src/ragbot/orchestration/nodes/generate.py).

### 7.3. DEEP-DIVE 3 — ROOT CAUSE 4 BUG CRITICAL ĐÃ XÁC NHẬN

> 4 bug xác nhận bằng đọc code thật `rag-crm/src/`. Mỗi bug kèm vị trí thật, trích code, cơ chế, kịch bản kích hoạt, blast radius, hướng xử lý. Nơi doc lệch code đều ghi rõ.

#### BUG #1 — `embed_degraded` dead-write

**Vị trí:** SET [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py) · decl [state.py:231](rag-crm/src/ragbot/orchestration/state.py).
```python
# query_graph.py:1649-1664 — trong _embed_query, nhánh except khi embed LỖI
except (TimeoutError, EmbeddingError, OSError, RuntimeError, ValueError, AttributeError) as _emb_exc:
    # Embed FAILED (≠ "no vector by design"): fail LOUD — flag the turn degraded ...
    try:
        state["embed_degraded"] = True      # ← SET duy nhất
    except (TypeError, KeyError):
        pass
    logger.error("embed_query_error_degraded", ...)
    return []
```
```python
# state.py:231 — chỉ khai báo TypedDict
embed_degraded: bool    # embed → answer path (HALLU-safety flag)
```
**Cơ chế:** grep toàn `src/`+`tests/` CHỈ 2 dòng chạm tên (decl + SET), **0 reader**. Cơ chế "chặn LLM bịa khi embed hỏng" chưa từng nối dây (dead-write).

**Kịch bản:** (1) embedding `TimeoutError`/`EmbeddingError`; (2) `_embed_query` SET cờ, log ERROR, `return []`; (3) retrieve fallback lexical-only, context ≈ rỗng/lệch; (4) generate chạy tiếp **không đọc `embed_degraded`**; (5) LLM nhận context nghèo nhưng vẫn trả lời → **bịa** trong khi hệ tưởng "fail loud & safe".

**Blast radius:** **HALLU (nghiêm trọng nhất — chạm rule sacred)** + dữ liệu + compliance. Kích hoạt đúng lúc hạ tầng embedding sự cố — thời điểm rủi ro HALLU cao nhất.
**Hướng xử lý:** nối `embed_degraded` vào answer-path — cờ bật → ép refuse/HITL hoặc chèn tín hiệu "context suy giảm".

#### BUG #2 — Dedup entity bằng `id()`

**Vị trí:** [query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py) trong `_reconcile_cross_doc` (def [:441](rag-crm/src/ragbot/orchestration/query_graph.py), gọi [:2564](rag-crm/src/ragbot/orchestration/query_graph.py)).

**Ghi chú doc-vs-code:** doc nói bug gồm 2 chỗ (`_reconcile_cross_doc` + "MMR dedup node dùng `id()`"). **Code thật: grep `id(` toàn `query_graph.py` chỉ 4 hit — tất cả ở `_reconcile_cross_doc` (488/491/502/504). KHÔNG tồn tại MMR node dùng `id()`.** Bug thật nhưng chỉ 1 vị trí.
```python
# query_graph.py:488-504
anchor_ids = {id(a) for a, _ in anchors}
absorbed: set[int] = set()
for e in entities:
    if id(e) in anchor_ids:
        continue
    if e.get("price_primary") is not None or e.get("price_secondary") is not None:
        continue
    dk = _spec_key(e)
    if not dk:
        continue
    for a, aks in anchors:
        if dk in aks:
            _absorb_fragment_attrs(a, e)
            absorbed.add(id(e))
            break
return [e for e in entities if id(e) not in absorbed]
```
**Cơ chế:** `id()` = địa chỉ bộ nhớ, không phải danh tính logic. Hai dict cùng sản phẩm nhưng khác object → `id()` khác → không "trùng". Hàm chỉ absorb fragment price-LESS vào anchor priced; hai bản đều-có-giá bị `continue` (494) → **không bao giờ reconcile** (docstring tự nhận "two priced anchors are NEVER merged").

**Kịch bản:** (1) sản phẩm tách 2 sheet (catalog + giá); (2) drift chính tả (ZR18 vs R18) hoặc cả hai đều có giá → 2 dict cùng sản phẩm; (3) hàm chỉ absorb fragment KHÔNG-giá → hai bản đều-có-giá giữ nguyên; (4) `id()` khác → không vào `absorbed`; (5) cả hai vào context; (6) LLM thấy 2 giá mâu thuẫn → conflate/mập mờ.

**Blast radius:** **billing/giá** + dữ liệu + HALLU conflate. Chạm Anti-HALLU "conflate".
**Hướng xử lý:** dedup theo khóa nội dung ổn định (digit/spec-key có sẵn), định nghĩa quy tắc hòa giải khi hai bản khác giá.

#### BUG #3 — `_persist` nuốt lỗi im lặng

**Vị trí:** [local_guardrail.py:948-964](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) (hàm `_persist` từ [:937](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)).
```python
# local_guardrail.py:948-964
for h in hits:
    try:
        await self._repo.insert({
            "message_id": message_id, "tenant_id": tenant_id, "request_id": request_id,
            "guardrail_type": guardrail_type, "rule_id": h.rule_id,
            "severity": h.severity, "action_taken": h.action, "details": h.details,
        })
    except Exception:  # noqa: BLE001 — logging best-effort
        # Never let logging block the pipeline; upstream has metrics.
        pass
```
**Cơ chế:** đường ghi `guardrail_events` — bằng chứng compliance mỗi lần rule fire. `insert` ném lỗi (DB down, RLS reject vì thiếu tenant scope, pool cạn, constraint) → `except` bắt và `pass` trần — không `_logger`, không metric, không re-raise. Module **đã có `_logger` structlog** ([:79](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)) dùng ở chỗ khác ([:571,608](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)); chỗ này cố tình không gọi. Vi phạm "Broad-except sweep policy": `except Exception` chỉ được phép khi có `exc_info=True` + `error_type` + structured event.

**Kịch bản:** (1) answer vi phạm rule → `_persist(hits)`; (2) DB `guardrail_events` sự cố tạm thời (failover/RLS reject/timeout); (3) `insert` ném; (4) `except: pass` nuốt trọn; (5) pipeline tiếp tục như ghi thành công; (6) audit sau đó query → **0 event** → kết luận sai "không phát hiện vi phạm".

**Blast radius:** **compliance/audit (nghiêm trọng — mất bằng chứng)** + observability (silent data-loss). Không chạm answer nên user không thấy.
**Hướng xử lý:** log insert-fail với `_logger` + `exc_info` + `rule_id`/`tenant_id`/`message_id`; cân nhắc retry/outbox thay `pass` trần.

#### BUG #4 — Citation không verify chunk tồn tại

**Vị trí:** regex [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) · Pass 1 [:394](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py).
```python
# local_guardrail.py:69
_CITATION_MARKER_RE = re.compile(r"\[[a-zA-Z0-9_\-]{1,64}\]")
```
```python
# local_guardrail.py:390-395 — grounding_check, Pass 1
if not retrieved_chunks:
    return None
answer = answer or ""
if _CITATION_MARKER_RE.search(answer):
    return None      # ← có MARKER là grounded, KHÔNG so chunk_id
```
**Cơ chế:** Pass 1 chỉ `.search()` **sự hiện diện** token khớp mẫu rồi `return None`. Text trong ngoặc không hề `.group()`/`.findall()`, không đối chiếu ID thật của `retrieved_chunks`. Bất kỳ bracket bịa nào — `[chunk_99]`,`[abc]`,`[ref1]`,`[note]` — làm short-circuit "grounded". Pass 1 TRƯỚC Pass 2 (substring) và Pass 3 (numeric) → citation giả **bỏ qua toàn bộ kiểm tra thực chất**. Check chỉ chạy khi `citation_marker_required=True` ([:897-898](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py)) — các bot audit-heavy (legal/medical) bật cờ → nhóm cần grounding chặt nhất lại dễ bị citation giả lọt.

**Kịch bản:** (1) bot bật `citation_marker_required=True`; (2) LLM sinh "mức phạt là 50 triệu `[doc_7]`" với `doc_7` không tồn tại; (3) `retrieved_chunks` không rỗng → qua guard đầu; (4) Pass 1 khớp `[doc_7]` → `return None`; (5) không tới Pass 2/3; (6) answer citation bịa đi qua guardrail sạch tới user.

**Blast radius:** **HALLU (citation giả — có vỏ "đã dẫn nguồn")** + compliance (bot legal/medical) + dữ liệu. False-negative của chính guardrail chống-bịa.
**Hướng xử lý:** bắt token trong `[...]` đối chiếu tập id thực; chỉ grounded khi trỏ chunk có thật, marker lạ rớt xuống Pass 2/3 hoặc fire grounding_fail.

#### BẢNG TỔNG HỢP

| # | Bug | Severity | File:line | Trigger | Blast radius | Verdict |
|---|-----|----------|-----------|---------|--------------|---------|
| 1 | `embed_degraded` dead-write | **CRITICAL** | [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py) (SET) · [state.py:231](rag-crm/src/ragbot/orchestration/state.py) · 0 reader | Embed provider lỗi | HALLU + dữ liệu + compliance | REJECTED — phải nối vào answer-path |
| 2 | Dedup entity bằng `id()` | **HIGH** | [query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py) — *doc sai: KHÔNG có MMR node dùng id(); grep chỉ 4 hit, đều ở đây* | Sản phẩm tách 2 sheet / drift | billing/giá + dữ liệu + HALLU conflate | REJECTED — dedup theo nội dung |
| 3 | `_persist` nuốt lỗi im lặng | **CRITICAL** | [local_guardrail.py:948-964](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) — `_logger` sẵn [:79](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) không gọi | Insert `guardrail_events` fail | compliance/audit + observability | REJECTED — phải log exc_info |
| 4 | Citation không verify chunk | **CRITICAL** | [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) · [:394](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) | Bot bật `citation_marker_required`, LLM bracket bịa | HALLU (citation giả) + compliance | REJECTED — phải so token với id chunk |

**Ghi chú lệch doc↔code:** Chỉ 1 điểm — Bug #2 doc nói thêm "MMR dedup node dùng `id()`"; **code thật không có**, toàn bộ 4 hit `id(` đều ở `_reconcile_cross_doc`. Ba bug còn lại khớp chính xác vị trí (1655 / 948-964 / 69+394).

### 7.4. DEEP-DIVE 4 — INGESTION PIPELINE & EXPERT CHUNKING

> Phạm vi: `POST /api/ragbot/documents/create` → worker → `DocumentService.ingest()` (U1→U7) → embed + store `document_chunks`. Ingest **đa định dạng một-luồng-canonical** (PDF/DOCX/XLSX/CSV/Sheets/PPTX/HTML/MD).

#### 4.0. Bản đồ tổng thể (U1→U7)

Skeleton U1→U7 ở [ingest_core.py](rag-crm/src/ragbot/application/services/document_service/ingest_core.py); stage U3–U7 ở các file `ingest_stages*.py` sibling (docstring [ingest_core.py:147-174](rag-crm/src/ragbot/application/services/document_service/ingest_core.py)).

| Stage | Tên | File:line | Việc thật làm |
|---|---|---|---|
| **U1** | `ingest_validate` | [ingest_core.py:274](rag-crm/src/ragbot/application/services/document_service/ingest_core.py) | Tenant guard, sanity metadata, source-URL allow-list (PoisonedRAG, [:295](rag-crm/src/ragbot/application/services/document_service/ingest_core.py)) |
| **U2** | `ingest_parse` | [ingest_core.py:304](rag-crm/src/ragbot/application/services/document_service/ingest_core.py) | `_route_through_parser` → `DocumentParserPort` registry; hit → `content` = markdown-có-cấu-trúc; giữ `parser_row_chunks` cho Excel/Sheets |
| **U3** | `ingest_clean` | [ingest_stages.py:278](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py) | CleanBase Tier-0 + legacy cleaner |
| **U4** | `ingest_chunk` | [ingest_stages.py:403](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py) | Expert Chunking: `analyze_document`→`select_strategy`→`apply_cross_check`→`smart_chunk` |
| **U5** | `ingest_enrich` | [ingest_stages_enrich.py:131](rag-crm/src/ragbot/application/services/document_service/ingest_stages_enrich.py) | Contextual-Retrieval prefix + chunk-quality; U5∥U6 gather ([:321](rag-crm/src/ragbot/application/services/document_service/ingest_stages_enrich.py)) |
| **U6** | `ingest_vn_segment` | [ingest_stages.py:964](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py) | VN compound segmentation (`content_segmented` cho BM25) |
| **U7** | `ingest_embed_store` | [ingest_stages_store.py:154](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py) | Narrate-then-Embed → embed → bulk-insert `document_chunks` |

Mixin: `_IngestMixin(_StageChunkMixin, _StageEnrichMixin, _StageStoreMixin, _StageFinalizeMixin)` [ingest_core.py:168-174](rag-crm/src/ragbot/application/services/document_service/ingest_core.py). Context truyền qua `_IngestCtx` dataclass ([ingest_stages.py:207-268](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py)).

#### 4.1. Parser Layer (U2) — registry + robust type-detection

Registry Port+Strategy+Registry: thêm format = thêm 1 file ([registry.py:8-16](rag-crm/src/ragbot/infrastructure/parser/registry.py)). `_REGISTRY` ([registry.py:45-61](rag-crm/src/ragbot/infrastructure/parser/registry.py)): `null, kreuzberg_markdown, excel_openpyxl, google_sheets, pdf, docx, markdown, vlm_image`.
- **`kreuzberg_markdown`** (pdf/pptx/html → MARKDOWN) precedence trước `pdf` legacy; fail-soft → NullParser → `pdf` (pypdfium2). `supports()` khớp `_KREUZBERG_MIMES/EXTS` ([kreuzberg_markdown_parser.py:107-111](rag-crm/src/ragbot/infrastructure/parser/kreuzberg_markdown_parser.py)); parse `extract_bytes_sync` qua `asyncio.to_thread` ([:134](rag-crm/src/ragbot/infrastructure/parser/kreuzberg_markdown_parser.py)).
- **`excel_openpyxl`/`google_sheets`** = row-as-chunk (comment [registry.py:12-16](rag-crm/src/ragbot/infrastructure/parser/registry.py)).
- **`vlm_image`** KHÔNG auto-fire (no-arg probe raise TypeError → skip); worker chọn tường minh khi VLM bật ([registry.py:56-60](rag-crm/src/ragbot/infrastructure/parser/registry.py)).

**3 tầng detection (mime → ext → byte-sniff):**
1. `detect_parser(mime, ext)` — first non-null `supports()` wins ([registry.py:97-120](rag-crm/src/ragbot/infrastructure/parser/registry.py)).
2. `detect_parser_robust(mime, ext, content, detector)` — trust `(mime,ext)` trước, sniff body khi miss; `None` chỉ khi genuine OCR-fallback ([registry.py:153-179](rag-crm/src/ragbot/infrastructure/parser/registry.py)).
3. `_sniff_mime(content)` — magic `%PDF-` → OOXML `PK\x03\x04` → `kreuzberg.detect_mime_type_from_bytes` ([registry.py:123-150](rag-crm/src/ragbot/infrastructure/parser/registry.py)).

Đầu `ingest()`: lớp sniff phòng thủ `sniff_real_mime(raw_bytes, file_name, mime)` sửa mime khi `octet-stream`/rỗng ([ingest_core.py:261-272](rag-crm/src/ragbot/application/services/document_service/ingest_core.py)) — vá "octet-stream → 0 chunks".

#### 4.2. Expert Chunking (U4) — strategy THẬT

Tên strategy ([_11_table_csv_chunking_strategy.py:43-47](rag-crm/src/ragbot/shared/constants/_11_table_csv_chunking_strategy.py)): `CHUNK_STRATEGY_HDT="hdt"`, `SEMANTIC="semantic"`, `RECURSIVE="recursive"`, `HYBRID="hybrid"`, `PROPOSITION="proposition"`. → **HDT/SEMANTIC/PROPOSITION/HYBRID đều tên thật** + `recursive` (baseline) + `table_csv, table_dual_index, parser_preserve` ([_02_per_intent_rerank_skip_gate_.py:115,129](rag-crm/src/ragbot/shared/constants/_02_per_intent_rerank_skip_gate_.py)).

**Router thật = `select_strategy` deterministic weighted scorer** (KHÔNG Port-based), [analyze.py:407-541](rag-crm/src/ragbot/shared/chunking/analyze.py):
- **Fast-path 1 — CSV → table**: `is_csv AND total_headings==0 AND vn_markers==0` → `(table_strategy, 1.0)` ([analyze.py:454-455](rag-crm/src/ragbot/shared/chunking/analyze.py)).
- **Fast-path 2 — VN legal/admin → HDT**: `(total_headings + vn_markers) >= DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES` → `("hdt", 1.0)` ([analyze.py:462-463](rag-crm/src/ragbot/shared/chunking/analyze.py)). Marker "Chương/Mục/Điều/Phần" promote heading qua `promote_vn_hierarchical_headings` ([ingest_stages.py:604,661](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py); [vn_structural.py](rag-crm/src/ragbot/shared/chunking/vn_structural.py)).
- **Ambiguous prose** → weighted scorer ([analyze.py:489-534](rag-crm/src/ragbot/shared/chunking/analyze.py)); confidence < `DEFAULT_STRATEGY_MIN_CONFIDENCE` → fallback `recursive` ([analyze.py:538-539](rag-crm/src/ragbot/shared/chunking/analyze.py)).

#### 4.3. Tầng 4 — LLM Selector: ĐÃ TẮT (disabled-by-comment, zero caller)

Blueprint mô tả "AdapChunk Tầng 4 — LLM Strategy Selector Port". **Code thật: Port vô hiệu hoá hoàn toàn.** Cả 4 file `infrastructure/chunking_strategy/` mở đầu:
```
# DISABLED — UNUSED (commented-marker, NOT deleted)
# The LLM/rule chunking-strategy SELECTOR (AdapChunk Tang-4 Port) has ZERO runtime callers ...
```
([registry.py:1-10](rag-crm/src/ragbot/infrastructure/chunking_strategy/registry.py), [llm_resolver.py:1-10](rag-crm/src/ragbot/infrastructure/chunking_strategy/llm_resolver.py), [rule_resolver.py:1-10](rag-crm/src/ragbot/infrastructure/chunking_strategy/rule_resolver.py), [__init__.py:1-10](rag-crm/src/ragbot/infrastructure/chunking_strategy/__init__.py)).

Grep xác nhận zero caller: `chunking_strategy_provider` chỉ trong 4 file đó; `build_chunking_resolver`/`LLMChunkingStrategyResolver.resolve_strategy` không gọi từ đâu (chỉ def Port [strategy_ports.py:78](rag-crm/src/ragbot/application/ports/strategy_ports.py)). → **`llm_resolver` vs `rule_resolver` đều DEAD CODE runtime**. Nếu bật lại, registry default `"rule"` ([registry.py:34-38](rag-crm/src/ragbot/infrastructure/chunking_strategy/registry.py)). Hiện tại chọn strategy hoàn toàn deterministic qua `select_strategy`.

> Nếu doc nói "Tầng 4 LLM selector đang chạy" → **code thật: Tầng 4 Port ĐÃ TẮT, routing là rule deterministic** (`shared/chunking/analyze.select_strategy` + `apply_cross_check`).

#### 4.4. Tầng 5 — Rule Cross-check & Atomic Integrity

**Tầng 5 (`apply_cross_check`)** — pure function trả `(strategy, confidence, override_reason)`, 5 rule ưu tiên ([analyze.py:576-675](rag-crm/src/ragbot/shared/chunking/analyze.py)): (1) confidence thấp → `hybrid`; (2) hdt thiếu heading → `semantic`; (3) semantic block ngắn → `proposition`; (4) proposition doc dài nhiều heading → `hdt`; (5) mixed-content cao → warn-only.

**Trạng thái BẬT/TẮT theo config thật:**

| Tầng | Flag | Default constant | file:line | Mặc định |
|---|---|---|---|---|
| **T4 LLM selector** | `chunking_strategy_provider` | — (disabled-by-comment) | [registry.py:1-10](rag-crm/src/ragbot/infrastructure/chunking_strategy/registry.py) | **TẮT** (zero caller) |
| **T5 cross-check** | `adapchunk_layer5_cross_check_enabled` | `= True` | [_12_multi_stage_retrieval_fallba.py:149](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py) | **BẬT** |
| Block pipeline (L2) | `adapchunk_block_pipeline_enabled` | `= True` | [_12_multi_stage_retrieval_fallba.py:185](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py) | **BẬT** |
| Layer-3 DocProfile | `adapchunk_layer3_doc_profile_enabled` | `= False` | [_18_admin_all_tenants_analytics_.py:73](rag-crm/src/ragbot/shared/constants/_18_admin_all_tenants_analytics_.py) | **TẮT** (telemetry) |
| Ekimetrics 5-metric | `ekimetrics_5metric_selector_enabled` | `False` (inline) | [ingest_stages.py:574-580](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py) | **TẮT** |
| Atomic-protect | `formula_image_atomic_protect_enabled` | `= False` | [_00_app_env_taxonomy.py:126](rag-crm/src/ragbot/shared/constants/_00_app_env_taxonomy.py) | **TẮT** |

> ⚠️ **Doc-vs-code (T5)**: docstring [analyze.py:567](rag-crm/src/ragbot/shared/chunking/analyze.py) và [__init__.py:456-457](rag-crm/src/ragbot/shared/chunking/__init__.py) ghi *"Feature flag … (default OFF)"*, nhưng hằng số THẬT `DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED: Final[bool] = True` ([_12_multi_stage_retrieval_fallba.py:148-149](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py) — dòng comment `# … = False` đã bị dòng code `= True` ghi đè). **Code thật: T5 mặc định BẬT.** Comment "OFF" sai.

**Atomic Integrity:** `_ATOMIC_BLOCK_TYPES = {"table","formula","image","code"}` ([blocks.py:140-146](rag-crm/src/ragbot/shared/chunking/blocks.py)); atomic block "MUST be preserved whole … cuts forbidden" ([blocks.py:184-189](rag-crm/src/ragbot/shared/chunking/blocks.py)). "Route AROUND splitter": `_smart_chunk_with_atomic_protect` ([__init__.py:283-342](rag-crm/src/ragbot/shared/chunking/__init__.py)) — atomic block `_emit_atomic_block` nguyên khối. **Nhưng gate `_atomic_protect_enabled()` default FALSE** ([blocks.py:288-307](rag-crm/src/ragbot/shared/chunking/blocks.py) + [_00_app_env_taxonomy.py:126](rag-crm/src/ragbot/shared/constants/_00_app_env_taxonomy.py)) → **bảo vệ chủ động mặc định TẮT**. Bù lại: atomic bảo toàn qua `original_content` verbatim (4.5) + test pin [test_ingest_original_content_persist.py:25-63](rag-crm/tests/unit/test_ingest_original_content_persist.py). Coverage guard OBSERVE-only `check_chunk_gaps` ([ingest_stages.py:889-890](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py); [coverage.py:141-215](rag-crm/src/ragbot/shared/chunking/coverage.py)).

#### 4.5. Narrate-then-Embed (Tầng 7) + "The Swap Trick"

**Narrate dispatch** ([narrate_dispatch.py](rag-crm/src/ragbot/application/services/narrate_dispatch.py)): `narrate_chunks_for_embed` phân loại block-type (`classify_chunk_block_type` dùng `_split_into_blocks_with_atomic` để đồng bộ chunker, [:70-104](rag-crm/src/ragbot/application/services/narrate_dispatch.py)), TABLE→linearize, FORMULA→LaTeX-to-prose, IMAGE→OCR-desc; fan-out `asyncio.gather` + `Semaphore(DEFAULT_NARRATE_MAX_CONCURRENCY)` giữ thứ tự ([:152-167](rag-crm/src/ragbot/application/services/narrate_dispatch.py)). Narrator con: `narrate_table` ([table_narrator.py:24-64](rag-crm/src/ragbot/application/services/narrate/table_narrator.py)), `narrate_formula` ([formula_narrator.py:70](rag-crm/src/ragbot/application/services/narrate/formula_narrator.py)).

**"The Swap Trick" — 3 danh sách riêng trong U7** ([ingest_stages_store.py](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)):
1. `texts_to_embed` — vào **encoder**. Sau narrate `= narrated_texts` ([:302](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)) + passage_prefix ([:327](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)). "vector_text", KHÔNG lưu vào `content`.
2. `persist_chunks[idx]` — ghi cột `content` (`"content": persisted_text`, [:809](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)). Text hiển thị/BM25/rerank (post-CR).
3. `original_content` (metadata) — `chunk_text` PRE-transform (pre-CR, pre-narrate), ghi `metadata_json` qua `_atomic_original_meta(chunk_text)` ([:798,908,1017](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py); helper [:121-147](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)).

→ **Decoupling:** `narrated_texts` chỉ gán `texts_to_embed` (grep 1 điểm gán [:302]); KHÔNG đẩy vào `persist_chunks`/`content`. Vector tính trên narrated, row lưu bản gốc — HALLU=0: không mất bản gốc để citation/LLM reconstruct. original_content lưu `document_chunks.metadata_json` key `CHUNK_METADATA_KEY_ORIGINAL_CONTENT="original_content"` ([_18_admin_all_tenants_analytics_.py:209](rag-crm/src/ragbot/shared/constants/_18_admin_all_tenants_analytics_.py)); JSONB qua `upsert_chunks` ([pgvector_store.py:146-159](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)). Test pin [test_ingest_original_content_persist.py:25-45](rag-crm/tests/unit/test_ingest_original_content_persist.py).

**Trạng thái T7 Narrate:** flag `narrate_then_embed_enabled`.
- In-code default `= True` ([_20_cag_mode_cache_augmented_gen.py:81](rag-crm/src/ragbot/shared/constants/_20_cag_mode_cache_augmented_gen.py)).
- Ý định lịch sử OFF: comment "NANO-IN-INGEST PATH #3 — DEFAULT OFF" ([document_worker.py:535-546](rag-crm/src/ragbot/interfaces/workers/document_worker.py)). Alembic 0230 seed `false` ([20260617_0230…py:36-41](rag-crm/alembic/_archive_pre_squash_20260618/20260617_0230_disable_remaining_nano_ingest_paths.py)); 0234 re-enable `true` ([20260617_0234…py:25](rag-crm/alembic/_archive_pre_squash_20260618/20260617_0234_reenable_narrate_paced_table_desc.py)) — cả hai archive **pre-squash**.
- **THẬT hiện tại (post-squash 20260618)**: grep `narrate_then_embed_enabled` trong `alembic/versions/` = **0 hit** → không seed → runtime rơi về default `True` → **T7 mặc định BẬT** trên worker (`_narrate_svc` wired `enabled=_narrate_enabled`, [document_worker.py:552-572,608](rag-crm/src/ragbot/interfaces/workers/document_worker.py)).

> ⚠️ **Discrepancy**: comment "DEFAULT OFF" nhưng post-squash không migration seed OFF + constant=True → **T7 BẬT nếu operator không seed `narrate_then_embed_enabled=false`**. **Cần verify runtime** (query `system_config`) — CHƯA verify psql. Degrade-safe: narrate lỗi/timeout `DEFAULT_NARRATE_TIMEOUT_S` → raw embed-target ([ingest_stages_store.py:310-317](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)); `narrate_service is None` → identity ([narrate_dispatch.py:144-145](rag-crm/src/ragbot/application/services/narrate_dispatch.py)).

#### 4.6. Embedders + Vector Store payload

**Embedder (Port+Strategy):** `_REGISTRY` ([embedding/registry.py:34-40](rag-crm/src/ragbot/infrastructure/embedding/registry.py)) **4 provider thật** — `LiteLLMEmbedder` (`"litellm"`, default), `JinaEmbedder` (`"jina"`+alias `"jina_ai"`), `ZeroEntropyEmbedder` (`"zeroentropy"`), `BkaiVnEmbedder` (`"bkai_vn"`, flag-gated). Trên đĩa 6 file (4 thật + `openai_embedder.py` + `null_embedder.py`). Default `"litellm"` ([registry.py:42](rag-crm/src/ragbot/infrastructure/embedding/registry.py)).

**Payload upsert `document_chunks`** ([pgvector_store.py:140-162](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py)): cột `record_document_id, chunk_index, content, content_hash, <embedding col>, metadata_json`. `original_content` KHÔNG cột riêng — trong `metadata_json` JSONB. `content_segmented` (BM25) ghi ở U7 bulk-insert ([ingest_stages_store.py:810](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)).

#### 4.7. Pitfalls production (có thật)

| Pitfall | Bằng chứng | Hướng xử lý |
|---|---|---|
| **Dimension mismatch (language override)** | `_apply_language_embedding_override` chỉ swap model NAME, giữ `dimension` ([__init__.py:426-462](rag-crm/src/ragbot/application/services/document_service/__init__.py) — 440-442) → vector lệch chiều | Validate dim-compatibility tại config-load, reject nếu `native_dim != spec.dimension` |
| **Dimension mismatch (config drift)** | `spec.dimension` từ `system_config.embedding_dimension` ([__init__.py:405-419](rag-crm/src/ragbot/application/services/document_service/__init__.py)); đổi sau ingest → cột `vector(N)` cũ mismatch | Đổi dimension = re-ingest toàn corpus; gate migration bằng check row |
| **OOM/ingest storm (spreadsheet)** | Narrate 1 nano-call/TABLE block; "tabular sheet = hundreds of blocks" ([document_worker.py:537-539](rag-crm/src/ragbot/interfaces/workers/document_worker.py)); row-as-chunk bùng nổ | Giữ narrate bounded O(tables) + paced; hoặc TẮT narrate khi có Jina late_chunking |
| **Latency LLM đồng bộ (narrate)** | Narrate gọi LLM đồng bộ hot path; hard-timeout `DEFAULT_NARRATE_TIMEOUT_S` ([ingest_stages_store.py:289,310-316](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)) | Bounded concurrency + timeout + circuit-breaker; TẮT narrate khi encoder context-aware |
| **Empty-input embedder 422** | table_dual_index linearize whitespace → Jina v3 reject cả batch; substitute `DEFAULT_EMPTY_EMBED_FALLBACK_TEXT` ([ingest_stages_store.py:335-346](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py)) | Giữ empty-input guard |
| **Partial-embed coverage** | Leaf null embedding → doc `failed` khi coverage < `ingest_min_leaf_embed_coverage` ([ingest_stages_final.py:193-215,288-316](rag-crm/src/ragbot/application/services/document_service/ingest_stages_final.py)) | Serve degraded khi ≥ floor; re-ingest khi dưới |

#### 4.8. Tóm tắt trạng thái tầng (SSoT nhanh)

- **T4 LLM chunking selector** → **TẮT** (disabled-by-comment, zero caller). Routing = `select_strategy` deterministic.
- **T5 cross-check** → **BẬT** theo constant `= True` (docstring ghi "OFF" — code thắng).
- **Atomic-protect splitter (chủ động)** → **TẮT** default; atomic vẫn bảo toàn qua `original_content` verbatim + test pin.
- **T7 Narrate-then-Embed** → in-code default **BẬT** (`= True`), không migration seed OFF post-squash → **cần verify runtime** trước khi chốt; degrade-safe khi None/timeout.
- **Block pipeline L2 BẬT**, **L3 DocProfile/Ekimetrics TẮT**.
- **Swap Trick** đúng: `texts_to_embed` (vector) ≠ `content` (persist) ≠ `metadata_json.original_content` (raw).

> Các nhận định flag chấm theo **code-evidence (constant + gate)**, chưa **runtime-verified** trên DB cụ thể — muốn chốt ON/OFF phải `SELECT key,value FROM system_config WHERE key IN ('narrate_then_embed_enabled','adapchunk_layer5_cross_check_enabled','formula_image_atomic_protect_enabled')`.

---

## 8. ACTION PLAN CHO TEAM

> Ưu tiên: **an toàn dữ liệu / chống hallucination / compliance / billing** trước; **resilience / trust** kế; **refactor / perf** sau. Mọi hạng mục mô tả **hướng xử lý bằng lời** — team tự hiện thực.

### 8.1. Bảng backlog

| ID | Hạng mục | Bug xử lý | Luồng | Ưu tiên | Effort | Tiêu chí nghiệm thu (acceptance) | Rủi ro |
|----|----------|-----------|-------|---------|--------|----------------------------------|--------|
| **A1** | Nối `embed_degraded` vào answer-path | #1, #30 (Confirmed) | Q1'/Q4/generate | **P0** | ~0.5–1h | Cờ trả trong dict `return` của node embed (không in-place); generate/guard_output đọc cờ → khi bật ép refuse-short-circuit hoặc siết grounding threshold; test: embed fail → answer là refuse/degraded, không bịa | Over-refuse nếu embed fail thoáng qua → canary + metric `embed_degraded_total` |
| **A2** | Đổi dedup `id()` → stable key | #2 (Confirmed) | Q6/`_reconcile_cross_doc` | **P0** | ~0.5h | `anchor_ids`/`absorbed` khóa theo `record_chunk_id`/`entity_name` chuẩn hóa (hoặc chỉ số vị trí); thêm quy tắc hòa giải khi 2 bản khác giá; test: 2 dict cùng sản phẩm khác object → dedup đúng | Đổi behavior merge → pin golden-set entity trước/sau |
| **A3** | Log lỗi trong guardrail `_persist` | #3 (Confirmed) | Guardrail persist | **P0** | ~10–15 phút | Thay `pass` bằng `_logger.warning(..., exc_info=True, rule_id, tenant_id, message_id)`; cân nhắc retry/outbox; test: mock `insert` raise → có log + metric `guardrail_persist_fail_total` | Rất thấp — chỉ thêm observability |
| **A4** | Validate citation chunk tồn tại (grounding Pass 1) | #4, #6 (Confirmed) | Q14/grounding | **P0** | ~0.5–1h | Pass 1 trích token trong `[...]`, chỉ grounded khi khớp `chunk_id` thật (giao `chunk_ids_allowed`); marker lạ rớt Pass 2/3 hoặc fire `grounding_fail`; test: answer `[doc_bia]` không tồn tại → KHÔNG pass | Có thể tăng grounding_fail rate → theo dõi `grounding_fail_total`, canary |
| **A5** | Ghi streaming token vào `token_ledger` + bot_id | #5 (Confirmed) | Q14/Q17 | **P1** | ~1–2h | Callback capture usage khi streaming, put vào `token_ledger` (non-blocking, drop-old khi QueueFull), kèm `bot_id`; test: streaming turn → có row ledger với bot_id + token count | Backpressure ledger → non-blocking + drop-old |
| **A6** | Graph build timeout guard | #21 (Confirmed) | STEP 7-9 | **P1** | ~10–15 phút | Bọc `get_graph()` bằng `asyncio.wait_for(timeout)`; Timeout → HTTP 503 sạch; test: mock DI hang → 503 chứ không treo | Timeout quá thấp → false 503; chọn ngưỡng có margin |
| **A7** | History load hardening (timeout + cap + phân loại lỗi + log) | #15 (Confirmed) | STEP 4 | **P1** | ~1h | `wait_for(timeout)`, `limit` cap; ConnectionError → log CRITICAL (không silent) + degrade rõ; lớp trong `history_reconcile.py:149-167` phải log; test: DB down → log + `history_degraded=True` | Đổi hành vi multi-turn khi DB chậm → giám sát degrade rate |
| **A8** | Grounding confirmed-action + numeric-fidelity: opt-in block cho bot nhạy cảm | #23, #31 (Confirmed) | Q13/Q15/reflect | **P1** | ~1–2h | Với bot audit/legal/medical hoặc intent trap: cấu hình `grounding_confirmed_action="block"` + `numeric_fidelity_action="block"`; coi grounding *không chạy* là "unknown" (không suy ra "grounded") trong reflect smart-skip; test: answer ungrounded ở bot block → thay `oos_template` | Over-refuse → chỉ bật cho bot cấu hình rõ; A/B refuse rate |
| **A9** | REFLECT: không coi tín hiệu grounding khuyết là "grounded" + mở rộng intent | #23 (Confirmed) + Lỗ hổng #3 | Q16 | **P1** | ~1h | Smart-skip không dựa vắng-mặt-flag khi judge không eligible; mở rộng `DEFAULT_GROUNDING_INTENTS` hoặc fallback grounding khi retrieval có chunk; log `grounding_degraded_total` | Tăng latency reflect → giữ cap retries=1 |
| **A10** | Raise InvariantViolation khi embedding model mismatch | #9 | Q6/retrieval | **P2** | ~0.5h | Thay log+continue bằng short-circuit/raise khi `resolved_model != expected_model`; test: mismatch → error rõ, không serve vector lệch | Có thể chặn serve khi config drift → cần alert kèm |
| **A11** | Language embedding override check dimension | #26 | Ingestion | **P2** | ~0.5h | Trong `_apply_language_embedding_override` check `mapped.dimension == spec.dimension` trước swap; reject tại config-load nếu lệch | Reject mapping hợp lệ nếu metadata dim sai → validate kỹ |
| **A12** | Intent cache key thêm tenant_id + language | #16-Q2 collision | Q2 | **P2** | ~0.5h | Key `understand_query_cache` chứa tenant_id + language; test: 2 tenant cùng câu hỏi → không collision | Cache hit giảm nhẹ (đúng) — chấp nhận |
| **A13** | Streaming feature flag: batch + registry Enum | #20 (Confirmed) | STEP 1 | **P2** | ~1h | Gộp 2 flag bằng `get_many` (1 round-trip); `FeatureFlagKey` Enum tập trung; log lý do disable | Thấp |
| **A14** | Thống nhất tiền xử lý BM25 (segment + fold có kiểm soát) + gate per-bot | #24, #25 (Confirmed) | Q6/BM25 | **P2** | ~medium | `hybrid_search` và `PgBM25Retrieval` dùng chung chuẩn segment/fold; biến thể bỏ dấu gate sau cờ per-bot để corpus nhạy-dấu tắt; eval precision/recall trước-sau | Đổi ranking BM25 → golden-set retrieval A/B |
| **A15** | RRF dense⊕sparse trong SQL: thread `rrf_k` xuống adapter | #16 (Partial — lớp SQL khóa cứng) | Q6/hybrid_search | **P2** | ~0.5h | Node truyền `rrf_k` (đọc từ `_pcfg`) vào `_hs_kwargs`/`_port_kwargs`; test: đổi config → SQL RRF đổi hành vi | Đổi ranking → eval; giữ default 60 nếu không set |
| **A16** | RLS Phase 3 flip (DSN NOBYPASSRLS) | #28 (Confirmed) | Tenant isolation | **P2** | ~2–4h (cần ops) | Flip DSN app sang role NOBYPASSRLS; `DATABASE_URL_SYSTEM` cho worker (BYPASSRLS); chạy isolation probe → cross-tenant read = 0 | Nếu policy thiếu → app gãy; chạy probe staging trước |
| **A17** | Verify runtime flags Ingestion (T5/T7/atomic-protect) | #32, #33 (cần runtime-verify) | Ingestion | **P2** | ~0.5h | `SELECT key,value FROM system_config WHERE key IN (...)`; chốt ON/OFF thực tế; cập nhật tài liệu vận hành | Thấp — chỉ đọc |
| **A18** | Tách `retrieve()` mega-node thành collaborator | Điểm nóng Q6 #1 | Q6 | **P3** | ~medium-large | Tách nhánh opt-in (multistage, diacritic-restore, parent-child, permission, superlative) ra Port+Strategy; node chỉ điều phối; test regression retrieval không đổi kết quả | Refactor lớn → chỉ làm sau khi P0/P1 xong, có test bọc |
| **A19** | Tách `build_graph()` thành node/edge factory | #18 (Partial) | build_graph | **P3** | ~4h | Split `build_graph_nodes()`/`build_graph_edges()`/`build_graph_helpers()`; extract `orchestration/node_factory.py` | Rủi ro wiring → test graph build |
| **A20** | State mutation tường minh + typed schema | #27 | Xuyên suốt | **P3** | ~medium | Node trả `(result, side_effects)`; strict TypedDict thay `total=False` | Refactor rộng → làm dần |
| **A21** | Async worker pattern (202 + job_id) | #22 | STEP 10 | **P3** | ~40h | Handler trả 202 `{job_id}`; `chat_worker` chạy graph; client poll → p95 100s→~50ms | Thay đổi contract API → phối hợp FE |
| **A22** | Async-mindset CI gate + broad-except policy | #29 | Xuyên suốt | **P3** | ~2h | Đưa audit script vào CI hard gate; mọi `except Exception` phải kèm `exc_info`+`error_type`+structured event | Thấp |

### 8.2. Nhóm theo giai đoạn

**P0/P1 — Critical, 0–2 tuần** (A1–A9): sửa 4 bug CRITICAL đã Confirmed (embed_degraded dead-write, id()-dedup, guardrail silent persist, citation không verify) + billing visibility (streaming ledger) + resilience cơ bản (graph timeout, history hardening) + siết HALLU-net cho bot nhạy cảm (grounding block opt-in, reflect signal). **Đa số P0 trivial (10 phút–1h)** — vấn đề cốt lõi là **thiếu cơ chế phát hiện**, không phải khó sửa. Tổng P0/P1 ước ~8–12h code + canary.

**P2 — High, 2–4 tuần** (A10–A17): embedding mismatch guard, dimension check, intent cache key, feature-flag batch, thống nhất BM25 + gate per-bot, thread rrf_k xuống SQL, RLS Phase 3 flip, verify runtime flags Ingestion.

**P3 — Design debt / Refactor, 4–8 tuần** (A18–A22): tách retrieve mega-node, tách build_graph, state schema, async worker, CI gate.

### 8.3. Roadmap 4 tuần

```
TUẦN 1 ── P0 (A1–A4) + A5: embed_degraded nối answer-path, id()-dedup stable key,
          guardrail persist log, citation validate chunk, streaming token_ledger
          → Canary 5% → monitor: embed_degraded_total, grounding_fail_total,
            guardrail_persist_fail_total, token_ledger rows → rollout 100%
          GATE: HALLU-safety + compliance visible + billing transparency

TUẦN 2 ── P1 (A6–A9): graph timeout, history hardening, grounding block opt-in
          (bot nhạy cảm), reflect signal fix
          Chốt cấu hình OBSERVE→block cho bot audit/legal/medical
          GATE: resilience + HALLU enforce cho bot cần chặt

TUẦN 3 ── P2 (A10–A15): embed mismatch guard, dimension check, intent cache key,
          feature-flag batch, BM25 thống nhất + gate, rrf_k xuống SQL
          Eval retrieval A/B (golden-set) trước-sau BM25/RRF change
          GATE: retrieval quality + multi-tenant cache trust

TUẦN 4 ── P2 (A16–A17) + P3 khởi động (A18–A19): RLS Phase 3 flip + isolation probe,
          verify runtime flags Ingestion, bắt đầu tách retrieve/build_graph
          Chuẩn bị async worker (A21) cho quý sau
          GATE: tenant isolation live + maintainability
```

### 8.4. Mục "KHÔNG làm" (ứng với claim Refuted — tránh phí công / gây hại)

| KHÔNG làm | Lý do (verdict) |
|-----------|-----------------|
| **KHÔNG** "tối ưu 65 Redis call tuần tự" | **Refuted** — thực tế 1 MGET cho 172 key, đã batched đúng chuẩn |
| **KHÔNG** sửa "async weak-ref memory leak trong persist.py" | **Refuted** — đã fix đúng với `_BG_CACHE_TASKS` + `add_done_callback`; sửa thêm = rủi ro regression |
| **KHÔNG** tối ưu "bot cache hit rate 5%" | **Refuted** — không có evidence; Singleton warm-all + TTL 3600s + single-flight |
| **KHÔNG** "config hóa RRF" toàn bộ theo claim gốc | **Partial** — 2 lớp Python đã tunable; chỉ cần thread `rrf_k` xuống lớp SQL (A15), không phải viết lại toàn bộ |
| **KHÔNG** sửa "MMR dedup node dùng id()" | **Refuted (doc sai)** — KHÔNG tồn tại MMR node dùng `id()`; chỉ `_reconcile_cross_doc` (A2) |
| **KHÔNG** coi "message_id collision" là CRITICAL cần fix gấp | **Partial** — PK là `request_id` UUID, trùng vô hại; hạ ưu tiên |
| **KHÔNG** refactor "build_graph God object" như class | **Partial** — là function/closure factory; refactor là P3 tách factory (A19), không phải "phá class" |
| **KHÔNG** vá "async task orphan" ở Q1' | **Partial** — asymmetric thật nhưng KHÔNG leak (mọi task cancel/await ở merge) |

> **Nguyên tắc vàng trước khi hành động:** đọc code thật để lọc — ~½ claim gốc là Refuted/Partial. Ưu tiên tuyệt đối 4 bug CRITICAL Confirmed vì chúng đe dọa trực tiếp hợp đồng HALLU=0 và toàn vẹn dữ liệu.

---

## 9. PHỤ LỤC — TRA CỨU NHANH & DANH MỤC FILE

### 9.1. Bảng tra cứu triệu chứng → nguyên nhân → vị trí

| Triệu chứng | Nguyên nhân | Vị trí (file:line) |
|-------------|-------------|--------------------|
| Bot trả lời bịa khi hạ tầng embedding lỗi | `embed_degraded` dead-write, generate không biết context degraded | [query_graph.py:1655](rag-crm/src/ragbot/orchestration/query_graph.py) · [state.py:231](rag-crm/src/ragbot/orchestration/state.py) |
| Cùng sản phẩm hiện 2 giá mâu thuẫn | Dedup entity bằng `id()` không hợp nhất 2 bản đều-có-giá | [query_graph.py:488-504](rag-crm/src/ragbot/orchestration/query_graph.py) |
| Audit compliance thấy 0 guardrail_event dù rule đã fire | `_persist` nuốt lỗi insert im lặng | [local_guardrail.py:948-964](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) |
| Answer có `[citation]` nhưng nội dung bịa qua được grounding | Pass 1 chỉ khớp cú pháp ngoặc, không so chunk_id | [local_guardrail.py:69](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) · [:394](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) |
| Bịa số/số sai vẫn tới user | Numeric-fidelity/grounding-confirmed default OBSERVE (log-and-ship) | [guard_output.py:190](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) · [:806-822](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) |
| Câu factoid bị gán intent lạ không được grounding | Intent-gating `DEFAULT_GROUNDING_INTENTS` 4 loại | [guard_output.py:369-390](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) · [_15:112-117](rag-crm/src/ragbot/shared/constants/_15_m2_neighbor_window_expansion.py) |
| Sai số liệu bảng Excel | Header tách data / chưa swap `original_content` | Luồng 1.2 (parser) & 2.2 (Swap Trick); [ingest_stages_store.py:302,809](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py) |
| Query tiếng Việt "từ chối" ra kết quả lệch | BM25 ASCII-fold "từ"→"tu" + bất đối xứng 2 đường BM25 | [pgvector_store.py:416,454](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py) · [pg_bm25_retrieval.py:108-123](rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py) |
| Đổi `rag_rrf_k` không thay đổi ranking dense/sparse | Lớp RRF SQL không nhận config (node không truyền `rrf_k`) | [pgvector_store.py:364,556](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py) · [retrieve.py:1100-1153](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) |
| Multi-turn mất ngữ cảnh khi DB chậm | History load 2 lớp swallow, lớp trong không log | STEP 4; `history_reconcile.py:149-167` |
| Request treo khi DI/lock hang | Graph build không timeout | STEP 7-9 ([chat_stream.py:238-323]) |
| p95 ~100s | `graph.ainvoke()` in-request, không async worker | STEP 10 ([chat_stream.py:335-376]) |
| Mất 30–50% cost visibility per-tenant | Streaming token không vào `token_ledger` (thiếu bot_id) | Q14/Q17 |
| Cross-tenant read vẫn được | RLS inert (DSN superuser bypass) tới Phase 3 flip | 20 bảng FORCE RLS + 21 policy `tenant_isolation` |
| Vector ghi lệch chiều, HNSW crash | Language embedding override chỉ swap NAME, giữ dimension | [document_service/__init__.py:426-462](rag-crm/src/ragbot/application/services/document_service/__init__.py) |
| Cấu hình chunk khác kỳ vọng | T4 LLM selector TẮT (deterministic); T5 comment "OFF" nhưng constant True | [chunking_strategy/registry.py:1-10](rag-crm/src/ragbot/infrastructure/chunking_strategy/registry.py) · [_12_multi_stage_retrieval_fallba.py:148-149](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py) |
| Ingest spreadsheet chậm/OOM | Narrate 1 nano-call/TABLE block, row-as-chunk bùng nổ | [document_worker.py:537-539](rag-crm/src/ragbot/interfaces/workers/document_worker.py) |

### 9.2. Danh mục file quan trọng (đường dẫn thật)

**Orchestration / graph:**
- [rag-crm/src/ragbot/orchestration/query_graph.py](rag-crm/src/ragbot/orchestration/query_graph.py) — megafile ~3071 dòng; `build_graph` (981–3028), `_reconcile_cross_doc` (441–504), `_embed_query`/`embed_degraded` SET (1655), `_do_stats_lookup` (2362–2684), `_CITATION_RE` (328).
- [rag-crm/src/ragbot/orchestration/state.py](rag-crm/src/ragbot/orchestration/state.py) — GraphState TypedDict; `embed_degraded` decl (231).
- [rag-crm/src/ragbot/orchestration/nodes/retrieve.py](rag-crm/src/ragbot/orchestration/nodes/retrieve.py) — mega-node retrieve (210–1982); RRF Python (1446/1793).
- [rag-crm/src/ragbot/orchestration/nodes/generate.py](rag-crm/src/ragbot/orchestration/nodes/generate.py) — refuse short-circuit (318–362), citation validation (816–889).
- [rag-crm/src/ragbot/orchestration/nodes/guard_output.py](rag-crm/src/ragbot/orchestration/nodes/guard_output.py) — numeric/brand/claim/grounding gates.
- [rag-crm/src/ragbot/orchestration/nodes/grade.py](rag-crm/src/ragbot/orchestration/nodes/grade.py) · [reflect.py](rag-crm/src/ragbot/orchestration/nodes/reflect.py) · [guard_input.py](rag-crm/src/ragbot/orchestration/nodes/guard_input.py).

**Guardrails:**
- [rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py](rag-crm/src/ragbot/infrastructure/guardrails/local_guardrail.py) — `_CITATION_MARKER_RE` (69), grounding_check Pass1 (394), grounding judge (416–553), `_persist` silent (948–964).

**Retrieval / vector:**
- [rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py](rag-crm/src/ragbot/infrastructure/vector/pgvector_store.py) — `hybrid_search` (357), SQL RRF (556–557), ASCII-fold (416/454), upsert (140–162).
- [rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py](rag-crm/src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py) · [lexical_registry.py](rag-crm/src/ragbot/infrastructure/retrieval/lexical_registry.py).
- [rag-crm/src/ragbot/application/services/multi_query_expansion.py](rag-crm/src/ragbot/application/services/multi_query_expansion.py) — `mq_rrf_merge_chunks` (565/594).
- [rag-crm/src/ragbot/shared/vi_tokenizer.py](rag-crm/src/ragbot/shared/vi_tokenizer.py) — `remove_diacritics` (193–204).

**Ingestion / chunking:**
- [rag-crm/src/ragbot/application/services/document_service/ingest_core.py](rag-crm/src/ragbot/application/services/document_service/ingest_core.py) · [ingest_stages.py](rag-crm/src/ragbot/application/services/document_service/ingest_stages.py) · [ingest_stages_enrich.py](rag-crm/src/ragbot/application/services/document_service/ingest_stages_enrich.py) · [ingest_stages_store.py](rag-crm/src/ragbot/application/services/document_service/ingest_stages_store.py) · [ingest_stages_final.py](rag-crm/src/ragbot/application/services/document_service/ingest_stages_final.py).
- [rag-crm/src/ragbot/shared/chunking/analyze.py](rag-crm/src/ragbot/shared/chunking/analyze.py) (`select_strategy` 407–541, `apply_cross_check` 576–675) · [blocks.py](rag-crm/src/ragbot/shared/chunking/blocks.py) · [__init__.py](rag-crm/src/ragbot/shared/chunking/__init__.py) · [vn_structural.py](rag-crm/src/ragbot/shared/chunking/vn_structural.py) · [coverage.py](rag-crm/src/ragbot/shared/chunking/coverage.py).
- [rag-crm/src/ragbot/infrastructure/parser/registry.py](rag-crm/src/ragbot/infrastructure/parser/registry.py) · [kreuzberg_markdown_parser.py](rag-crm/src/ragbot/infrastructure/parser/kreuzberg_markdown_parser.py).
- [rag-crm/src/ragbot/infrastructure/chunking_strategy/registry.py](rag-crm/src/ragbot/infrastructure/chunking_strategy/registry.py) (DISABLED) · [llm_resolver.py](rag-crm/src/ragbot/infrastructure/chunking_strategy/llm_resolver.py) · [rule_resolver.py](rag-crm/src/ragbot/infrastructure/chunking_strategy/rule_resolver.py).
- [rag-crm/src/ragbot/application/services/narrate_dispatch.py](rag-crm/src/ragbot/application/services/narrate_dispatch.py) · [narrate/table_narrator.py](rag-crm/src/ragbot/application/services/narrate/table_narrator.py) · [narrate/formula_narrator.py](rag-crm/src/ragbot/application/services/narrate/formula_narrator.py).
- [rag-crm/src/ragbot/infrastructure/embedding/registry.py](rag-crm/src/ragbot/infrastructure/embedding/registry.py) (4 embedder thật).
- [rag-crm/src/ragbot/interfaces/workers/document_worker.py](rag-crm/src/ragbot/interfaces/workers/document_worker.py) — narrate wiring (535–608).

**Constants (SSoT flags):**
- [_00_app_env_taxonomy.py](rag-crm/src/ragbot/shared/constants/_00_app_env_taxonomy.py) (atomic-protect 126, rag_rrf_k 224) · [_11_table_csv_chunking_strategy.py](rag-crm/src/ragbot/shared/constants/_11_table_csv_chunking_strategy.py) · [_12_multi_stage_retrieval_fallba.py](rag-crm/src/ragbot/shared/constants/_12_multi_stage_retrieval_fallba.py) (T5 148-149) · [_14_anti_abuse_ip_rate_limit_hon.py](rag-crm/src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py) (grounding actions) · [_15_m2_neighbor_window_expansion.py](rag-crm/src/ragbot/shared/constants/_15_m2_neighbor_window_expansion.py) (grounding intents/threshold) · [_20_cag_mode_cache_augmented_gen.py](rag-crm/src/ragbot/shared/constants/_20_cag_mode_cache_augmented_gen.py) (narrate 81).

**Interfaces:**
- `rag-crm/src/ragbot/interfaces/.../chat_stream.py` — STEP 0–11 (87–470).

**Tests pin:**
- [rag-crm/tests/unit/test_ingest_original_content_persist.py](rag-crm/tests/unit/test_ingest_original_content_persist.py) — original_content verbatim (25–63).

---

*Hết tài liệu bàn giao. Tài liệu nguồn: [PHAN-TICH-LUONG-RAGBOT.md](PHAN-TICH-LUONG-RAGBOT.md). Mọi nhận định là code-evidence tĩnh theo file:line; các điểm đánh dấu "cần verify runtime" phải query `system_config` / debug-trace trước khi chốt.*
