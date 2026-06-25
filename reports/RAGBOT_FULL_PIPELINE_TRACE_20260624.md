# RAGBOT — Full Pipeline Trace (ingest → retrieve → answer) + Root-Cause của bug "trả lời sai nhiều"

> **Mục đích**: tài liệu này trace TOÀN BỘ luồng đang chạy thật trong code (file:line), từ lúc nhận link →
> lấy data → chunking → embed → lưu → và từ câu hỏi → retrieve → rerank → LLM → câu trả lời. Viết để đưa
> NotebookLM (hoặc bất kỳ ai) đọc + debug hộ. Mọi bước đều dẫn `file:line` thật (commit `d42bace`,
> 2026-06-24). Đối chiếu với case test `z-luannt-test-chinh-sac-xe.txt` (bot `chinh-sach-xe`, 41 câu,
> NotebookLM chấm đúng 18/41 = 44%).

---

## PHẦN 0 — TL;DR: tại sao bot trả lời sai 56% (5 nguyên nhân gốc, đã verify bằng DB)

1. **Stats-index chỉ index 4 vai trò cột** (`name / category / price / aliases`). Mọi cột khác — **Mã hàng,
   Tồn kho, Date sản xuất, Link ảnh, Ngày về, Kho** — bị đổ vào `attributes_json` **KHÔNG search được**.
2. **Phân mảnh đa-sheet**: cùng 1 sản phẩm bị tách thành 4–6 entity rời (mỗi sheet một mảnh, mỗi mảnh giữ
   1 field), **không join**. Query khớp 1 mảnh → mảnh đó thiếu field hỏi → "không có thông tin".
3. **File `xe-4.md` (chính sách bảo hành) = 0 chunk** → ingest thất bại → bot KHÔNG có nội dung bảo hành.
4. **Query path chỉ trả 1 entity (`stats_in`, score 1.000)**, không fallback full-table/semantic khi entity
   thiếu field → field ngoài entity = "không tìm thấy".
5. **Tồn kho không vào field search được + giá rỗng trên nhiều mảnh** → sai số/thiếu số.

NotebookLM đúng vì nó **nạp cả 4 file vào context khổng lồ (Gemini 1–2M token)** → đọc thẳng mọi cột/dòng,
0 mất mát do chunk/retrieval. Nó chuẩn vì **corpus nhỏ**, không phải retrieval giỏi hơn.

---

## PHẦN 1 — TẠI SAO stats-index chỉ có 4 vai trò? (design + giới hạn)

### 1.1. 4 token-set đóng (closed-vocab) — `src/ragbot/shared/document_stats.py:135-154`
```python
_NAME_COL_TOKENS      = {"ten","name","dich vu","service","san pham","goi","combo",
                         "ten dich vu","ten san pham","ten goi","product","item"}          # :135
_CATEGORY_COL_TOKENS  = {"nhom","danh muc","category","loai","vung","type","khu vuc"}        # :139
_PRICE_COL_TOKENS     = {"gia","price","phi","amount","cost","don gia","gia le","gia goc",
                         "gia sale","gia ban","thanh tien","unit price"}                     # :142
_ALIASES_COL_TOKENS   = {"aliases","synonym","synonyms","tu khoa","keyword","keywords",
                         "bien the","variant","variants"}                                    # :151
```

### 1.2. Gán vai trò = **exact-match** — `_column_roles()` `document_stats.py:323-353`
- Mỗi header normalize (bỏ dấu, lowercase) rồi check `token in _NAME_COL_TOKENS` (… category/price/aliases).
- **Cột header KHÔNG nằm trong 4 set → KHÔNG có role.**

### 1.3. Cột không match → đổ vào `attributes_json` — `_extract_entity_from_row()` `document_stats.py:460-462`
```python
else:
    label = header[idx] if idx < len(header) else f"col_{idx}"
    attributes[label] = col      # ← Mã hàng / Tồn kho / Date / Link ảnh rơi vào đây
```

### 1.4. `ParsedEntity` — field nào search được — `document_stats.py:218-242`
| Field | Lưu cột DB | Search được? |
|---|---|---|
| `name` | `entity_name` | ✅ ILIKE + fold |
| `category` | `entity_category` | ✅ ILIKE |
| `price_primary/secondary` | numeric | ✅ range |
| `aliases` | `entity_synonyms` | ✅ GIN trigram + fold |
| `attributes` | `attributes_json` (JSONB) | ❌ **KHÔNG nằm trong WHERE** |

### 1.5. Ý đồ thiết kế (trích docstring thật)
- `document_stats.py:1-17`: *"industry-verified Stats Index (Pinecone / AI21 metadata filter). HALLU=0 vì
  pure Python regex, no LLM."*
- `stats_index_repository.py:448-459` (`query_by_name_keyword`): *"Powers list/count/category queries …
  vector/BM25 chỉ surface top-k nên LLM không thể liệt kê/đếm HẾT. Cái này trả về MỌI record khớp từ index
  cấu trúc sạch, deterministic + complete."*

➡️ **Kết luận**: stats-index **CỐ Ý** chỉ làm "price catalog factoid + liệt kê/đếm" cho **happy-case bảng
giá** (Tên | Nhóm | Giá | Aliases). Nó **KHÔNG** thiết kế để tra Mã hàng / Tồn kho / Date / Link ảnh — các
cột này là "noise" với nó, đổ vào `attributes_json` rồi **không bao giờ query**. Đây chính là gốc của lỗi
"không tìm thấy" với bot xe (corpus có 6+ cột, stats-index chỉ hiểu 4).

---

## PHẦN 2 — LUỒNG INGEST (nhận link → lưu chunk + stats-index)

| # | Bước | file:line | Làm gì | In → Out |
|---|---|---|---|---|
| 1 | HTTP POST | `interfaces/http/routes/documents.py:91-189` | Nhận `/api/ragbot/documents/create`, idempotency, quota, trả **202** + job_id | Request → DocumentUploaded event |
| 2 | Use-case enqueue | `application/use_cases/ingest_document.py:54-152` | Tạo row `documents` (state=draft), job, publish outbox event | Command → outbox |
| 3 | Worker pickup | `interfaces/workers/document_worker.py:97-687` | Consume `SUBJECT_DOCUMENT_UPLOADED` (Redis Stream), gọi `DocumentService.ingest()` | event → ingest |
| 4 | **Fetch link** | `document_worker.py:281-460` | URL Google **Docs→export docx**, **Sheets→export CSV** (`google_link_service.to_export_url`), set mime/name; raw bytes thì passthrough | URL → `full_text` + bytes |
| 5 | **Detect format** | `infrastructure/parser/registry.py:97-179` | mime → ext → **byte-sniff** (`_sniff_mime`: %PDF, OOXML zip, kreuzberg); chọn parser registry | bytes → parser |
| 6 | **Parse → markdown** | parser adapters: `excel_openpyxl_parser`, `google_sheets_parser`, `docx_parser`, `kreuzberg_markdown_parser`, `markdown_parser` | Excel/Sheets → **row-as-chunk** (mỗi dòng 1 chunk, sheet = `# <sheet>`); pdf/html/pptx → kreuzberg markdown | bytes → `parser_row_chunks` |
| 7 | Ingest orchestrator | `application/services/document_service/ingest_core.py:177-427` | Vào `ingest()`, dựng `_IngestCtx`, sniff mime correction, source allow-list guard | content → ctx |
| 8 | U1 validate | `ingest_core.py:275-286` | Tenant guard, sanity | — |
| 9 | U2 parse | `ingest_core.py:305-344` (`_route_through_parser` `__init__.py:710-779`) | Route bytes qua parser, join `content`; markdown-normalizer (default OFF) | bytes → `parser_row_chunks` + content |
| 10 | U3 clean | `ingest_stages.py:222-338` | CleanBase Tier-0 (HTML strip, NFC, anti-injection) + legacy clean; metadata-extract (LLM, default OFF) | content → cleaned |
| 11 | **U4 chunk** | `ingest_stages.py:340-856` | Resolve `chunking_policy` (per-bot→system→const); **whole-doc** / **parent-child** / **smart_chunk**; với Excel/Sheets → `parser_preserve` (giữ 1-dòng-1-chunk); AdapChunk L2-L5; **tenant style_profile** (P3) | cleaned → `chunks[]` + strategy |
| 12 | U5 enrich | `ingest_stages_enrich.py:120-500+` | Contextual-Retrieval (LLM, gắn context), VN segment; **row-gate** skip enrich cho table rows | chunks → enriched |
| 13 | U6 VN segment | `ingest_stages.py:858-920` | Tách từ ghép tiếng Việt (default OFF) | chunks → segmented |
| 14 | **U7 embed+store** | `ingest_stages_store.py:121-450+` | Resolve embedding-spec (per-bot Jina v3 1024-dim), narrate-then-embed (table→câu), dedup re-index theo content-hash, **embed batch** (Jina), **bulk insert** `document_chunks` + pgvector | chunks → DB rows + vectors |
| 15 | **Finalize + stats** | `ingest_stages_final.py:219-530` | Đếm coverage embed → state flip **active/failed** (clear `deleted_at` khi active); **stats-index extract**: `parse_table_chunks(rows)` → `_dedup_stats_entities` → `delete_by_document` → `_insert_stats_index`; `aggregate_summary` → `documents.summary_json` | rows → active + stats entities |

**Table path (Excel/Sheets — đúng cái bot xe dùng)**: fetch CSV (4) → parser row-as-chunk (6) → U4 giữ
`parser_preserve` (11) → U5 row-gate skip (12) → U7 narrate+embed+store (14) → Finalize chạy
`parse_table_chunks` trên các row → ra `ParsedEntity` **chỉ với name/category/price/aliases**, phần còn lại
(Mã/Tồn/Date/Ảnh) vào `attributes_json` (15 + PHẦN 1).

---

## PHẦN 3 — LUỒNG QUERY → ANSWER (~25 node LangGraph)

Entry: `interfaces/http/routes/test_chat/chat_routes.py:100` (`POST /api/ragbot/test/chat`, sync, trả answer
inline) hoặc `/api/ragbot/chat` (async job). Graph build: `orchestration/query_graph.py:898` (`build_graph`)
/ `:2834` (`get_graph`).

| Node | file:line | Làm gì | Routing/Config |
|---|---|---|---|
| guard_input | `nodes/guard_input.py` | Guardrail input (chặn malicious) | blocked → persist |
| cache+understand // | `query_graph.py:1708` | Cache check + understand song song | hit → persist |
| understand_query | `nodes/understand.py:55` | Condense lịch sử + phân loại **intent** (factoid/multi_hop/comparison/chitchat) | route theo intent |
| router | `nodes/routing.py:100` | Chọn chiến lược: decompose / rewrite / retrieve | `decompose_enabled` |
| query_complexity (opt) | `nodes/query_complexity_node.py` | L1 phát hiện multi-entity | complex → adaptive_decompose |
| adaptive_decompose (opt) | `nodes/adaptive_decompose.py` | Tách 2-4 sub-query | → retrieve |
| rewrite+MQ // | `query_graph.py:2154` | Paraphrase + multi-query fanout | → retrieve |
| **retrieve** ⭐ | `nodes/retrieve.py:148` | **QUYẾT ĐỊNH retrieval path** (xem 3.1) | → rerank / generate |
| rerank | `nodes/rerank.py:55` | Jina rerank + **cliff filter** + top-n cap; **SKIP nếu stats route** | → mmr_dedup |
| mmr_dedup | `nodes/mmr_dedup.py` | Bỏ chunk trùng | → neighbor_expand |
| neighbor_expand (opt) | `nodes/neighbor_expand.py` | Mở rộng chunk lân cận (default OFF) | → grade |
| grade (CRAG) | `nodes/grade.py:60` | Chấm relevance, drop irrelevant; **SKIP nếu stats route** (`:99-111`) | inadequate → rewrite_retry |
| generate | `nodes/generate.py:112` | Dựng prompt **top-5 graded chunk** + history + system_prompt → gọi LLM (gpt-4.1-mini) → answer + citations | → critique_parse |
| critique_parse (opt) | `nodes/critique_parse.py` | Self-RAG (default OFF) | → guard_output |
| guard_output | `nodes/guard_output.py:49` | Guardrail output + **grounding check** (skip cho stats route `:96-100`) | blocked → persist |
| reflect (opt) | `nodes/reflect.py` | Tự phản tỉnh (default OFF) | → persist |
| persist | `nodes/persist.py:38` | Audit + ghi semantic cache (background) | → END |

### 3.1. ⭐ Retrieval path — chỗ quyết định stats vs vector (`nodes/retrieve.py:176-585`)
Khi `stats_index_repo` có (factoid/lookup), code thử lần lượt:
- `_parse_range_query` (khoảng giá) `:206`
- `_parse_code_query` (mã/kích thước, vd "195/65R15") `:221` — `DEFAULT_STATS_CODE_LOOKUP_ENABLED=True`
- `_parse_price_of_entity_query` (giá của X) `:232`
- `_parse_list_query` (liệt kê/đếm) `:239`

Nếu confidence ≥ `RANGE_QUERY_MIN_CONFIDENCE` (0.5) `:282` → **route STATS**:
- `query_graph.py:2264` gọi `stats_index_repo.query_by_name_keyword(...)`
- `:2417-2429` dựng **1 synthetic chunk** `stats_in` **score=1.000** từ các field của entity (name|price|…)
- `retrieve.py:568-577` return `retrieve_mode="stats_index"`, **graded_chunks = chunk này luôn**
- `_retrieve_route` (`query_graph.py:2778`): `retrieve_mode.startswith("stats")` → **đi thẳng generate, BỎ QUA
  rerank + mmr + grade**.

➡️ **Hệ quả** (khớp log test: mọi câu `Chunks: 1, stats_in, score 1.000`): bot **chỉ thấy đúng 1 entity**.
Nếu entity thiếu field được hỏi (tồn kho, date, link, mã ở attributes_json) → synthetic chunk không có dữ
liệu đó → LLM trả "không tìm thấy". **Không có fallback sang full-table/semantic** trong nhánh stats khi
entity-có-nhưng-thiếu-field.

### 3.2. `query_by_name_keyword` search những cột nào? (`stats_index_repository.py:494-521`)
WHERE chỉ OR trên: `unaccent(entity_name) ILIKE` + `entity_category ILIKE` + `entity_synonyms ILIKE` + fold.
**`attributes_json` KHÔNG nằm trong WHERE** → Mã hàng/Tồn/Date trong attributes_json = **không tra được**.

---

## PHẦN 4 — ĐỐI CHIẾU TEST `chinh-sach-xe` (evidence DB thật)

### 4.1. Phân mảnh đa-sheet (1 sản phẩm = 6 entity)
`165/65R14 79H CITYTRAXX G/P` trong `document_service_index`:
```
[165/65R14 79H CITYTRAXX G/P]            price=∅  attrs={"col_2":"28-thg 11"}            ← sheet hàng về
[Lốp xe LANDSPIDER 165/65R14...]         price=∅  attrs={"Kho":..,"Mã":"2-R14 165/65 LPD"} ← sheet catalog (Mã, KHÔNG giá)
[Lốp LANDSPIDER 165/65R14...]            price=∅  attrs={"Ngày về":"28-thg 11"}         ← sheet hàng về
[Lốp xe LANDSPIDER 165/65R14...]         price=702000 attrs={"Giá":702000}              ← sheet giá
```
→ Tồn kho (404) **không có trong field nào**. Mã hàng nằm ở `attributes_json["Mã"]` (không search được).

### 4.2. File ingest ra 0 chunk
```
xe-1.csv  → 440 chunks   xe-2.csv → 132   xe-3.csv → 500
xe-4.md   → 0 chunks  ⚠ (chính sách bảo hành)
xe-00-summary.md → 0 chunks ⚠
```
→ Toàn bộ câu bảo hành (5 năm, 7 ngày giám định, 72h, hotline, địa chỉ kho) thất bại vì **không có chunk**.

### 4.3. Map 3 nhóm lỗi NotebookLM ↔ nguyên nhân code
| Nhóm lỗi (NotebookLM) | Câu ví dụ | Nguyên nhân code |
|---|---|---|
| A. "Không tìm thấy" mã/tồn/date/ảnh | "Mã 2-R13 155/80 LPD?", "tồn kho?", "link ảnh?" | PHẦN 1: cột ở `attributes_json`, `query_by_name_keyword` không search (§3.2) |
| B. Sai số / nhầm field | tồn kho "26" (đúng 404); ngày về "26" (đúng 28/11) | §4.1 phân mảnh: khớp nhầm mảnh date1=26; tồn kho không có field |
| C. Bảo hành mơ hồ/thiếu | "đổi mới 100%", "7 ngày", hotline | §4.2 `xe-4.md` = 0 chunk → bot không có dữ liệu |

---

## PHẦN 5 — Tại sao NotebookLM nhanh + chuẩn, và hướng fix

### 5.1. NotebookLM = long-context, KHÔNG phải retrieval-RAG
- Nạp **toàn bộ 4 file** (full bảng thô + text bảo hành) vào context Gemini 1–2M token → LLM đọc thẳng mọi
  cột/dòng → **0 mất mát** do chunk/column-role/phân mảnh.
- Nhanh = 1 call LLM trên context cache. Chuẩn = **vì corpus nhỏ vừa context**, không phải retrieval giỏi hơn.
  Ở scale 1000s file thì stuffing per-query không khả thi (cost/latency).

### 5.2. Hướng fix (ưu tiên theo tác động)
| Fix | Đóng nhóm lỗi | Tầng | Trạng thái |
|---|---|---|---|
| **Fix `xe-4.md`/summary ingest 0 chunk** | C (≈10 câu) | ingest bug | cần đào riêng |
| **P9: capture đủ cột + search `attributes_json`** (stock/code/date/image thành field tra được, hoặc query_by_name_keyword OR attributes_json) | A (≈14 câu) | đã có plan `plans/20260624-P9-locale-column-role-cascade/` |
| **Entity-join đa-sheet** (gộp mảnh theo product-key → 1 entity đủ field) | A+B (phân mảnh + sai field) | mới (cần ADR) |
| **Stats fallback sang full-table/semantic** khi entity thiếu field hỏi | A | `retrieve.py` nhánh stats |
| **Long-context mode cho bot corpus-nhỏ** (stuff cả catalog vào context như NotebookLM) | tất cả | T2 option |

> Riêng **fix xe-4 (0 chunk)** + **P9** vớt lại phần lớn 23 câu sai. Entity-join + stats-fallback đưa lên
> gần NotebookLM. Long-context mode = ngang NotebookLM cho bot nhỏ.

---

## PHẦN 6 — Cách dùng file này với NotebookLM
1. Đưa file này + `z-luannt-test-chinh-sac-xe.txt` (Q&A vàng + đáp án bot) cho NotebookLM.
2. Hỏi: *"Dựa trên pipeline trace + kết quả test, xác nhận 5 nguyên nhân gốc ở PHẦN 0, và đề xuất thứ tự fix
   tối ưu cho corpus nhỏ (4 file) vs corpus lớn (multi-tenant)."*
3. Đối chiếu đề xuất của NotebookLM với bảng fix §5.2 — cái nào trùng = ưu tiên làm trước.

*(Trace theo code tại commit `d42bace` 2026-06-24. Mọi file:line verify được bằng cách mở đúng dòng.)*
