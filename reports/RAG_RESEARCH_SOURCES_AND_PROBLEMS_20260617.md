# RAG Research Sources + Problem Tracker (2026-06-17)

> Nguồn tài liệu đã đọc/đối chiếu phiên này + DANH SÁCH ĐẦY ĐỦ vấn đề lôi ra debug +
> trạng thái fix. Dùng cho continuity + audit.

---

## A. NGUỒN TÀI LIỆU (verified vs unverified)

### A.1 Local refs (đọc code/spec thật)
- `_external_refs/adaptive-chunking/` — LREC 2026 paper. Public API = recursive + gap-repair + `titles_context`; "adaptive" = chunk N-method → chấm 5 intrinsic metric → chọn best/doc. **English-only** (spaCy/coref/langdetect skip non-en). 5 metric: SizeCompliance/IntrachunkCohesion/ContextualCoherence/BlockIntegrity/MissingRefError.
- `_external_refs/RAG-Anything/` — per-table LLM description + KG.
- AdapChunk spec (user-pasted, v1.0 03/2026) — 7 tầng: Mistral OCR → Block Detection → Feature Extraction → LLM Selector → Rule Cross-check → Chunking Executor (atomic-block protect) → Narrate-then-Embed + metadata.

### A.2 Web (agent-fetched, verified)
- **Firecrawl** best-chunking-2026 — fixed/recursive/semantic/page/structural/LLM/**late-chunking**/hierarchical.
- **Vision-Guided Chunking** arXiv 2506.16035v2 — *"separate chunk EACH ROW, include headers"*, multi-page merge, **+11pp (0.78→0.89)**. ✅ xác nhận row-as-record.
- **Late chunking** Jina (jina.ai/news/late-chunking) + Elastic + arXiv 2409.04701 — embed full-doc → pool token theo boundary. BEIR +0.6→+6.5pt. **Phải gửi full-doc 1 call; pre-split = no-op.** Limit = 8192-token window.
- **EvidentlyAI** rag-evaluation — tách retrieval (Precision/Recall/HitRate@k) vs generation (Faithfulness/Correctness); gold-chunk map; custom-calibrated LLM judge; golden chunk-first.
- **Unstructured.io** — `by_title` chunking, `Table` element isolated + `text_as_html`, element metadata.
- **PMC12649634** clinical RAG — adaptive chunk F1 0.64 vs fixed 0.24; **semantic chunking over-hyped** (win không significant); lift đến từ structure+micro-header.
- **VN: arXiv 2409.13699** (legal IR) — section-based Điều/Khoản + metadata, R@10=0.98; **PhoBERT/BKAI 256-token** vs Jina 8k (giữ Jina). arXiv 2503.07470 (ViRetrieve/ViRerank bench). PhoBERT repo.
- **Jina Semantic Chunking Regex v1** (ảnh) — 54-dòng regex cắt theo structure (heading→list→quote→code→**table**→sentence→...). Xác nhận structure-first.

### A.3 Unverified (paywall/blocked — KHÔNG dẫn)
- Studocu (paywall), Reddit "7 strategies" + Docling-thread (blocked), W09-3402 PDF (2009, unparseable).
- 43-citation list trong `z-luannt-new-feature.txt` (file đã tổng hợp sẵn; gitignored vì PII).

### A.4 Internal reports phiên này
`reports/DEEPDIVE_{CHUNKING,RETRIEVAL,COMPLIANCE}_20260617.md`, `N8N_PROMPT_CULTURE_20260617.md`, `PROJECT_STATE_EXPERT_RAG_20260617.md`, `docs/dev/N8N_TO_RAGBOT_PROMPT_MINDSET.md`.

---

## B. DANH SÁCH VẤN ĐỀ (debug) + TRẠNG THÁI FIX

| # | Vấn đề | Tầng | Root cause (file:line) | Trạng thái |
|---|---|---|---|---|
| P1 | spa "dưới 500k" FLAKY | retrieve | condense ghi đè state["query"] (query_graph.py:1957) | ✅ FIXED (parse original_query) |
| P2 | late-chunking có vô hiệu? | ingest | embed_batch window 7800tok (jina_embedder.py:342) | ✅ VERIFIED OK (không vô hiệu) |
| P3 | xe manifest NÁT | csv_chunker | split("\n") + header=dòng-phẩy-đầu (csv_chunker.py:42,236) | 🔧 FIXING |
| P4 | structured field thiếu (answer/quantity/date) | stats route | synthetic chunk chỉ name:price (query_graph.py:2844) | 🔧 FIXING |
| P5 | record_chunk_id NULL ở ingest | stats repo | bulk_insert thiếu FK (stats_index_repository.py:115) | 🔧 FIXING |
| P6 | spa "rẻ nhất"→700k | generate | entity-name rác + LLM lấy từ full-table | 🟡 P4 giúp 1 phần |
| P7 | narrate-then-embed OFF | csv_chunker | DEFAULT_NARRATE=False | ⏸ defer (đo lift trước) |

**Tổng: 7 vấn đề.** P1,P2 xong. P3,P4,P5 đang fix. P6 phụ thuộc P4. P7 defer (rule#0 đo trước).

---

## C. CLAUDE.md compliance (mỗi fix tự audit)
- Domain-neutral: header-signature lexicon generic, 0 tên bot.
- Zero-hardcode: threshold ở constants.
- No app-inject: chunk = grounded data.
- Measure-first: reingest + load-test 3 bot trước/sau, HALLU=0 gate.
- EVOLVE-not-REWRITE: sửa csv_chunker tại chỗ, không viết lại.
