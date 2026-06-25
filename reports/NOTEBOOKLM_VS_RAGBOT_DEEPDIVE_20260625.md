# Deep-Dive Report — `chinh-sach-xe` bot (44%) vs NotebookLM (~95%): vì sao cùng là RAG mà chênh lệch?

> **Loại**: Research + diagnosis (READ-ONLY — 0 dòng code bị sửa trong phiên này).
> **Ngày**: 2026-06-25 · **Branch**: `fix-260623-ingest-expert`
> **Nguồn dữ liệu**: `z-luannt-test-chinh-sach-xe.txt` (head-to-head 40 câu: NotebookLM gold vs bot answer) + code-trace live DB + 6 agent (2 code-deepdive Opus + 4 web-research) + deep-research skill.
> **Tier**: `[T1-Smartness]` — chẩn đoán coverage gap, không refactor.
> **Nhãn evidence**: SỰ THẬT = có log/DB/file:line · GIẢ THUYẾT = chưa kiểm chứng (theo CLAUDE.md rule #0).

---

## 0. TL;DR (1 đoạn)

Bot `chinh-sach-xe` **không yếu ở LLM** — nó yếu ở **tầng retrieval**. Mọi câu hỏi đều bị ghim về **đúng 1 chunk** (`stats_in`, score gán cứng 1.000) qua một đường tắt "stats-index" → bỏ qua toàn bộ rerank/CRAG-grade/hybrid fallback. NotebookLM "mạnh" **không phải vì thuật toán retrieval giỏi hơn**, mà vì nó **nhồi cả tài liệu vào context 1 triệu token của Gemini** nên gần như **không có bước retrieval để fail** (needle-recall >99.7%). Toàn bộ 23 lỗi của bot đều là lỗi tầng retrieval — đúng failure-mode mà long-context xóa sổ.

**Số liệu nền (SỰ THẬT, từ chính file test)**: 18/41 đúng (**44%**), 23 sai (**56%**), **100% câu trả lời đều `Chunks: 1`**.

---

## 1. Bối cảnh test

- **Bot**: `chinh-sach-xe` / web — `record_bot_id = c6e1fc56-d070-439d-99a6-c8b4964b4d2d`
- **Corpus**: 4 nguồn Google Sheets/Docs (catalog lốp xe) — xe-1.csv (440 chunks), xe-2.csv (132), xe-3.csv (500), xe-4-baohanh.docx (8). Tất cả đã embed.
- **Phương pháp test**: cùng 40 câu hỏi NotebookLM đã trả lời đúng (gold) → hỏi lại bot → file tự verify đúng/sai.
- **5 nhóm câu**: (1) thông tin sản phẩm/mã hàng, (2) tồn kho + giá, (3) chính sách bảo hành, (4) hàng đang về, (5) hình ảnh + date sản xuất.

---

## 2. ĐỌC HẾT LUỒNG — Kiến trúc thật của bot (code-verified, file:line)

### 2.1 Luồng INGEST (Google Sheet → DB) — phần CHẠY ĐÚNG
```
U0 validate → U2 parse (google_sheets/openpyxl) → U3 clean →
U4 chunk: table_csv (1 row = 1 chunk, header prepend)  ── csv_chunker.py:22-57 ✅
→ U5 enrich (SKIP cho table row — ingest_stages_enrich.py:180) → U6 vn_segment →
U7 embed jina-embeddings-v3 1024-dim → store document_chunks →
finalize: build STATS-INDEX vào bảng document_service_index  ── document_stats.py:561
         (regex thuần, 0 LLM → HALLU=0 by construction)
```
Phần này đúng: mỗi dòng sheet thành 1 chunk, header được prepend, orphan-merge bị skip cho table strategy (tránh trộn dòng).

### 2.2 Luồng QUERY (21 node) — chỗ bị "đoản mạch"
```
guard_input → cache+understand (parallel) → router →
retrieve.py:199  ── parse câu hỏi tìm code/range/list ──► CODE QUERY phát hiện
   │  (gần như mọi câu test có mã: 2-R13 155/80 LPD, 195/65R15, 2-ZR17 215/45 LPD...)
   ▼  query_range_parser.py:478-513 → RangeFilter(operation="keyword", keyword=<code>)
query_graph.py:2240  ── StatsIndexRepository.query_by_name_keyword(code)
   ▼
query_graph.py:2416-2429  ── build 1 SYNTHETIC CHUNK:
   │     chunk_id = DEFAULT_STATS_SYNTHETIC_CHUNK_ID ("stats_index_synthetic")
   │     score   = 1.0   ◀── GÁN CỨNG, KHÔNG phải cosine similarity
   │     source  = "stats_index"   ◀── UI hiển thị "stats_in"
   ▼
retrieve.py:537-577  ── return NGAY: seed graded_chunks = [synthetic]
   │     → BỎ QUA rerank, mmr_dedup, neighbor_expand, CRAG grade
   ▼
routing.py:232  "if retrieve_mode.startswith('stats') → generate"
   ▼
generate.py:616-627  ── LLM chỉ thấy ĐÚNG 1 chunk trong <documents>
```

**Đây là cơ chế khiến `Chunks: 1` xuất hiện ở 100% câu trả lời và score luôn 1.000** — score 1.0 là hằng số gán cứng (`query_graph.py:2425`), không phản ánh độ liên quan. Raw per-row chunks **bị cố tình suppress** (`query_graph.py:2456-2467`).

> **Gap README vs code (code wins)**: không có chunk nào tên literal `stats_in`; đó là `source="stats_index"` + chunk_id `"stats_index_synthetic"`. `documents.summary_json` được ghi lúc ingest **nhưng KHÔNG bao giờ đọc ở query path** (`grep summary_json orchestration/ = 0 hit`) — "summary-doc" thật chạy retrieval là bảng `document_service_index`.

---

## 3. 23 CÂU SAI — phân rã root-cause (có DB + file:line)

### Nhóm A — "không tìm thấy" dù data có (lỗi phổ biến nhất)
Các mã/giá/tồn báo "không có": `2-R13 155/80 LPD`, `2-ZR17 215/45 LPD`, `2-ZR18 225/40 LPD`, giá `2-ZR19 255/35 LPD`, tồn `2-R16 205/55 LPD`=780, `2-R13 175/70 LPD`=23, toàn bộ link ảnh + date (Q36-41).

**Root cause (SỰ THẬT — DB-verified)**: 4 nguồn là **4 bảng RỜI, mỗi bảng 1 thuộc tính**:
| Sheet | Cột có | Thiếu |
|---|---|---|
| xe-1.csv | `Tên, Nhóm, Mã, Kho, Aliases` | giá, tồn, date |
| xe-2.csv | `Tên, Nhóm, Ngày về, Aliases` | mã, giá |
| xe-3.csv | `Tên, Nhóm, Giá, Aliases` | mã, tồn, date |
| xe-4-baohanh.docx | warranty prose | — |

→ **KHÔNG dòng nào có đủ code+giá+tồn+date**. Stats-index key theo `entity_name` (cột Tên), nên cùng 1 product thành 3-4 entity nửa-rỗng không join được. **Cột Tồn kho chưa từng được ingest** (DB: `chunks chứa 'Tồn' = 0`) → câu hỏi tồn 780/404/23 **bất khả thi**. Bot match được 1 dòng "anh em" nhưng dòng đó thiếu thuộc tính được hỏi → LLM trung thực báo "không có trong dữ liệu".

### Nhóm B — sai số (HALLU THẬT)
- Tồn `2-R14 165/65 LPD`: bot nói **26**, đúng **404**.
- Ngày về lô Landspider: bot nói **"26"** (đó là `date1`=năm SX), đúng **28/11**.

**Root cause (SỰ THẬT)**: parser **role-vocab chỉ biết** `name/category/price/aliases` (`document_stats.py:135-164`) → cột `Mã/Kho/Ngày về/Tồn/Date/Link` **không nhận diện** → rớt thành `attributes` hoặc `col_N` không nhãn. Synthetic chunk builder emit `col_2: 28-thg 11` **không nhãn ngữ nghĩa** (`query_graph.py:2401-2408`) → LLM không phân biệt được "ngày về" vs "ngày trong tháng" → vớ nhầm số bên cạnh. **"26" là số bịa** — corpus không có cột tồn. Đây là HALLU-misinterpret/conflate **do application đưa số không nhãn**, không phải lỗi LLM thuần.

### Nhóm C — trả lời chung chung (warranty)
Thiếu: 5 năm, ≥1.6mm, 70%→đổi 100%, 7 ngày giám định, 72h ưu tiên, hotline 0988 771 310, địa chỉ Hà Nội.

**Root cause (SỰ THẬT)**: câu warranty **không có mã** → không vào đường stats → rơi xuống hybrid retrieve. Nhưng bot **KHÔNG bind reranker** (DB bindings = embedding/enrich/llm only) → 8 chunk warranty bị **~1072 chunk catalog** nhấn chìm → top-1 trả về **nhầm 1 chunk CSV giá** (`019ef903` = xe-3 chunk 8, score 0.000), **không phải** chunk warranty. Doc warranty CÓ đáp án nhưng không lọt top-1.

> **Nuance trung thực (SỰ THẬT)**: chạy lại `query_by_name_keyword` BÂY GIỜ thì vài mã từng báo "không tìm thấy" đã ra kết quả → index đã rebuild sau lần test (nhờ parser/alembic đang sửa dở trên branch này: `20260624_stats_index_entity_synonyms.py`). Nhưng **lỗi cấu trúc** (Tồn=0, date/link không nhãn, no reranker) **vẫn nguyên**.

### Tổng hợp nhóm lỗi
| Nhóm | Triệu chứng | Tầng gốc rễ | Evidence |
|---|---|---|---|
| A | "không tìm thấy" dù có | **DATA** (4 bảng rời, thiếu cột Tồn) | DB query; document_stats.py:135-164 |
| B | sai số / lạc cột (HALLU) | **PARSER** (cột không gán nhãn role) | query_graph.py:2401-2408 |
| C | warranty chung chung | **RETRIEVE** (no reranker, warranty chìm) | DB bindings; chunk 019ef903 |
| (tất cả) | `Chunks:1` topK=1 | **ARCHITECTURE** (stats short-circuit) | retrieve.py:537-577; routing.py:232 |

---

## 4. Vì sao NotebookLM mạnh? (web-research, có citation)

NotebookLM **cố tình không gọi mình là RAG** — Google gọi là **"source grounding"** (Steven Johnson, Hard Fork 2025).

| Yếu tố | NotebookLM | Tác động |
|---|---|---|
| **Long-context** | Gemini 2.5 Flash, **1M token**, nhồi NGUYÊN source vào context | **>99.7% recall** needle-in-haystack → **không có topK để miss** |
| **Không chunk-retrieve** (corpus vừa) | cả spreadsheet trong context | header + mọi dòng cùng lúc → **không lạc cột, không lạc dòng** |
| **Closed corpus** | chỉ trả lời từ nguồn upload | xóa hallucination từ trí nhớ tham số model |
| **Citation bắt buộc** | mỗi câu phải `[n]` trỏ nguồn | hallu **13%** vs ChatGPT/Gemini **40%** (study arxiv 2509.25498), **không bao giờ bịa số/tên** |
| **Thinking model** | CoT nội bộ | suy luận multi-doc trong 1 lượt |

**Điểm cốt lõi**: "magic" của NotebookLM **không nằm ở retrieval algorithm** — nó nằm ở chỗ **context đủ lớn để retrieval failure hiếm khi xảy ra**.

**Trade-off / giới hạn NotebookLM**: tối đa 50–600 nguồn/notebook, ≤500K từ/nguồn, **không có API public**, không real-time, **không scale tới 100K+ docs** như platform multi-tenant. Bot anh scale tốt hơn — nhưng phải làm đúng tầng retrieval. Long-context **không** làm RAG lỗi thời: chi phí "full doc in context" đắt gấp ~100× full-RAG (đó là lý do RAG vẫn là default cho enterprise).

---

## 5. Khác biệt kiến trúc gốc

| Chiều | Bot (pgvector chunk-RAG) | NotebookLM (long-context) |
|---|---|---|
| Cách thấy tài liệu | top-K chunk (ở đây bị ghim **K=1**) | **cả nguồn** trong 1M token |
| Failure chính | answer-trong-corpus-nhưng-không-vào-topK | hiếm (recall >99.7%) |
| Bảng/spreadsheet | mỗi dòng 1 chunk, dễ lạc cột + topK starvation | cả bảng trong context, attend toàn cục |
| "Liệt kê tất cả" / aggregation | topK không đủ → starve | nhìn hết → liệt kê đủ |
| Hallucination | có (Nhóm B số bịa) | chỉ "interpretive", **không bịa số/tên** |
| Scale | **100K+ docs, multi-tenant** ✅ | trần 600 nguồn ❌ |

---

## 6. Best-practice & Pain-points (4 web-research agent) ánh xạ vào bot

Nguồn 2024–2026 **đồng thuận** với chẩn đoán code:

| # | Best practice (SOTA + benchmark) | Bot đang | Fix nhóm |
|---|---|---|---|
| 1 | **Row-as-Key-Value + re-prepend header** (STC: BM25 Recall@1 **+105%**, arxiv 2605.00318) | có table_csv nhưng **cột không gán nhãn role** | B |
| 2 | **Hybrid BM25+dense+RRF** cho mã/SKU (dense **luôn fail exact code**) | có hybrid **nhưng bị stats short-circuit nuốt** | A |
| 3 | **Route exact-lookup + "liệt kê" sang SQL** (TableRAG **+136%** vs naive, arxiv 2506.10380) | stats-index đúng hướng nhưng **chỉ 1 chunk + thiếu cột** | A, list |
| 4 | **Metadata filter / self-query** (hard pre-filter, 0 miss) | `entity_category` rỗng 152/163 dòng → rơi về cosine | A |
| 5 | **Đừng topK=1** + **bind reranker** (Anthropic: rerank -67% fail) | **no reranker** → warranty chìm | C |
| 6 | **Contextual Retrieval** (prepend ngữ cảnh, -35%~-67% fail) | enrich SKIP cho table row | A, C |
| 7 | **Nguồn phải 1 bảng WIDE** (mỗi product 1 dòng đủ cột) | **4 bảng rời, thiếu cột Tồn** | A (gốc rễ nhất) |

**8 pain-point RAG production** (xếp theo tần suất gây fail): chunking boundary loss · embedding yếu trên code/số · semantic-lexical gap · language mismatch (VN) · **bảng bị tách cột** · hallucination/grounding · lost-in-the-middle/topK · stale index. **Meta-lesson**: *"bug luôn ở tầng TRÊN nơi nó hiện ra"* — hallucination ← retrieval ← chunking ← **dữ liệu nguồn**. Forage.ai: *"đa số production failure truy về DATA, không phải model."*

**Quy trình build RAG 12 bước** (zero→prod): parse → chunk → embed → vector store → query-process → hybrid retrieve → rerank → context-assemble → generate+grounding → eval harness → observability → iteration flywheel. Mỗi bước có "key decision + common mistake" (chi tiết trong phần phụ lục agent).

**Observability lesson trùng khớp**: "Reranker silently bypassed → NullReranker fallback" (= Nhóm C) cần *fail-loud*; "HTTP 200 ≠ answered correctly" (= bẫy `status=success`, phải đo **Coverage** không chỉ Faithfulness).

---

## 7. Đề xuất fix theo tầng (ưu tiên — CHƯA thực hiện, chờ anh quyết)

Theo CLAUDE.md tier T1-Smartness + "fix đúng tầng gốc rễ", rẻ→đắt:

1. **T1 DATA (đòn bẩy cao nhất)**: gộp 4 sheet thành **1 catalog WIDE** — mỗi product 1 dòng đủ `mã | tên | giá | tồn | ngày về | date | link`. Thêm **cột Tồn kho** (hiện thiếu hẳn). Khớp mandate happy-case "sửa NGUỒN, đừng phình parser".
2. **T1 PARSER**: mở rộng role-vocab nhận `Mã/Kho/Ngày về/Tồn/Date/Link` → synthetic chunk có **nhãn ngữ nghĩa** (diệt Nhóm B). Domain-neutral, config-driven (header tokens trong system_config).
3. **T1 RETRIEVE**: khi entity thiếu thuộc tính được hỏi, **đừng ghim topK=1** — cho fallback hybrid; **bind reranker** cho bot này để cứu warranty (Nhóm C).
4. **T2**: cân nhắc **route "liệt kê tất cả" sang SQL** thay vì synthetic chunk (đúng SOTA TableRAG).

> CẢNH BÁO compliance (CLAUDE.md): mọi thay đổi DB content (`bots.*`, binding) **chỉ qua alembic/admin-audit**, KHÔNG psql hot-fix. Parser/role-vocab phải domain-neutral (không hardcode tên brand/cột riêng cho bot này).

---

## 8. CLAUDE.md compliance self-audit của phiên phân tích

- ✅ Rule #0 CẤM ĐOÁN: mọi claim gắn nhãn SỰ THẬT (DB/file:line) vs GIẢ THUYẾT.
- ✅ Sacred #10 (no app-inject/override): chẩn đoán, không sửa answer-path; chỉ rõ "26" là HALLU do app đưa số không nhãn.
- ✅ Domain-neutral: đề xuất role-vocab config-driven, không hardcode brand.
- ✅ Model tier: deepdive = Opus main session (không delegate so-sánh sang Sonnet); 2 code agent Opus, 4 web agent Sonnet (subagent read-only).
- ✅ READ-ONLY: 0 dòng `src/` bị sửa.
- ✅ Coverage metric: nhấn mạnh Faithfulness 1.0 + Coverage 0.44 = vẫn FAIL UX.

---

## 9. Output 6 agent (đã chạy, read-only)

| Agent | Model | Kết quả |
|---|---|---|
| Debug | Opus | root-cause `stats_in` short-circuit + 4-bảng-rời + no-reranker, file:line đầy đủ + live DB |
| Phân tích kiến trúc | Opus | map ingest U0-U7 + query 21-node + stats-index, 5 gap README≠code |
| Best-practice | Sonnet | STC row-KV, hybrid RRF, SQL-routing, contextual retrieval (benchmark+URL) |
| Pain-points + build | Sonnet | 8 nỗi đau RAG + quy trình 12 bước |
| NotebookLM internals | Sonnet | long-context source-grounding, Gemini 2.5 Flash 1M, hallu 13% vs 40% |
| Observability/cost | Sonnet | NullReranker fail-loud, infra-metrics-lie, Coverage vs Faithfulness, data flywheel |
| So sánh (synthesis) | Opus main | chính báo cáo này |

---

*Báo cáo lập bởi Claude Opus 4.8 (1M context). Mọi số liệu kiểm chứng được tại file:line / DB row đã dẫn. Code wins nếu mâu thuẫn với README.*
