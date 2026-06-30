# DEEP-DIVE: ROOT CAUSE + FLOW + PHƯƠNG ÁN — 2026-06-30

> 3-agent Opus flow-trace (ingest→chunk · retrieve→prompt · purge-bug) + data evidence (spa/xe chunks) + 35-question live re-ask. Mọi hop có file:line. Branch `fix-260623-ingest-expert`.

---

## 0. KẾT LUẬN 1 DÒNG

**Tất cả lỗi answer-quality quy về MỘT gốc rễ: ingest/chunking KHÔNG tạo record per-row sạch-nhãn cho data dạng bảng.** spa và xe fail vì **cùng** lý do cấu trúc (khác data-shape). **Sys-prompt KHÔNG cứu được cái nào** (số đúng không nằm trong chunk bot thấy). Fix đúng = **CODE ingest/chunking + DATA restructure + re-ingest**, KHÔNG phải sys-prompt.

---

## 1. BỐN VẤN ĐỀ — root cause + luồng

### VĐ-1 — LLM bind sai VALUE trên bảng (spa giá sai 5/5 · xe tồn/date sai) ⭐ lỗi chính

**Luồng ingest (agent trace, file:line):**
```
Sheet CSV
 └─ google_sheets_parser.py:81  rows_to_structured_markdown(rows) → MỘT markdown blob (KHÔNG row-as-chunk)
 └─ tabular_markdown.py:266-267 DATA row serialize MỌI cột → cột Aliases đổ verbatim vào content
 └─ tabular_markdown.py:207/230 thêm "## section" → total_headings > 0
 └─ analyze.py:454  fast-path table_csv (row-as-chunk) YÊU CẦU headings==0 → BỊ LOẠI
                    (+ _is_csv_format False vì content là pipe, không phải comma)
 └─ strategies.py:142,157-166  _chunk_recursive_with_tables: giữ bảng nguyên nếu < size*3,
                    else PACK NHIỀU ROW / 1 chunk theo char-budget → value của row khác lẫn nhau
 └─ document_stats.py:476  role-binding = VOCAB token match; KHÔNG có date-role token
                    → date1/date2/"ngày về" rớt xuống generic positional attributes → mơ hồ
```

**3 root cause:**
- **(a) Alias-flood** — cột Aliases (`document_stats.py:195-203`) đổ nguyên `155/80R13, 155 80 13, 155 80R13...` (mấy chục biến thể) vào chunk được embed → chunk thật bị nhiễu, signal sản phẩm bị loãng. (Evidence: xe-3 chunk[142] = 5311 ký tự gần như toàn alias.)
- **(b) Row-mixing** — chunker size-based gộp nhiều row/chunk → bot nhận đúng sản phẩm nhưng nhặt **tồn kho/giá của row bên cạnh** (780→751, 134→120).
- **(c) col_N + date-ambiguity** — header ô rỗng → `col_N`; cột date không có role → bot lẫn `date1` (SX) với "ngày về". (Evidence: spa bảng giá `| col1 | col2 | Thời gian | Giá/buổi |` — col2 = tên dịch vụ KHÔNG nhãn.)

**spa thêm 1 lớp:** mỗi dịch vụ tồn tại ở **2 chunk** — (1) chunk **script** hội thoại (KHÔNG có số: *"Nếu khách hỏi giá thì trả lời: có ưu đãi đặc biệt..."*) và (2) chunk **bảng giá** (có số nhưng col_N + nhiều row). Retrieval ưu tiên chunk script (match tên dịch vụ giàu hơn) → bot không thấy số → trả nhầm giá dịch vụ gần tên.

→ **Sys-prompt bất lực**: số 550k không nằm trong chunk bot nhận.

### VĐ-2 — Câu "liệt kê" → innocom 500 (xe)

**Luồng (agent trace):**
```
"liệt kê/tất cả/bao nhiêu" → aggregation keyword (retrieve.py:686) → top_k 15→40
 └─ multi-query fanout ×3 (retrieve.py:1109-1182) + decompose sub-queries, mỗi cái pull 40 → RRF merge
 └─ rerank top_n=20 cho aggregation; count-cap rerank_max_chunks_to_llm=5 BỊ EXEMPT cho aggregation (rerank.py:396)
 └─ prompt char-cap 5500 (chỉ giới hạn KÝ TỰ, KHÔNG giới hạn TOKEN); `or not _kept` luôn giữ ≥1 chunk
 └─ _invoke_llm_node → complete_runtime → litellm.acompletion (router.py:652): KHÔNG check input/context-length
 └─ innocom trả 500 → retry 3× → LLMError → http_status=500 (errors.py:197) → KHÔNG graceful-degradation
```
**Root cause**: pipeline aggregation phình prompt (top_k 40 × MQ×3 + count-cap exempt + chỉ cap ký-tự) → prompt vượt context innocom → provider 500 → propagate thành pipeline 500, **không có prompt-shrink-retry**.

### VĐ-3 — legal miss clause cụ thể (100 triệu MFA · nguồn điện)

**Luồng:** query factoid (không phải aggregation) → clause chunk chịu **cliff gap-cut** (gap_ratio 0.35, floor 0.05) + **rerank min-score gate 0.30** (`rerank.py:255-364`) → chỉ được cứu nếu nằm trong **top-2 retrieval safety-net** (`rerank.py:457-493`). Clause bị cross-encoder dìm quá rank 2 → rớt → 0 graded → **refuse short-circuit** (`generate.py:335-359`).
**Root cause**: cliff + threshold 0.30 + safety-net chỉ top-2 → rớt clause liên quan nhưng rerank thấp (với câu factoid).

### VĐ-4 — Re-sync KHÔNG purge chunk cũ → duplication (chặn re-ingest sạch)

**Luồng (agent trace, đã verify):**
```
replace_documents_for_bot (__init__.py:910-917): soft-delete doc cũ theo source_url, KHÔNG xóa chunk (dựa FK-cascade KHÔNG tồn tại)
 └─ ingest dedup SELECT (ingest_core.py:402): WHERE ... deleted_at IS NULL → KHÔNG thấy row vừa soft-delete → is_reindex=False → uuid mới
 └─ INSERT đụng uq_doc_tool (UNIQUE tenant+bot+tool_name, KHÔNG gồm deleted_at — models.py:315) → ON CONFLICT DO UPDATE → nhận LẠI doc_id CŨ
 └─ vì is_reindex=False → existing_hashes={} → 2 DELETE purge (ingest_stages_store.py:688,:698) BỊ SKIP → pure INSERT → 97+222=319 (dup)
```
**Root cause**: filter `deleted_at IS NULL` ở dedup SELECT **mâu thuẫn** với unique constraint `uq_doc_tool` (bỏ qua deleted_at).
**Fix 1 dòng**: bỏ `deleted_at IS NULL` ở `ingest_core.py:402` → is_reindex=True → purge chạy. (HOẶC hard-DELETE chunk trong `replace_documents_for_bot:910`.)

---

## 2. SUY LUẬN — tại sao 1 fix giải nhiều bug

- VĐ-1 (spa giá + xe tồn/date + col_N + date-ambiguity) **đều** do chunker không giữ **1 row = 1 record sạch-nhãn**. Sửa ingest thành **row-as-chunk table-aware** giải **đồng thời** cả spa lẫn xe — đúng "late-binding table" (S1-A) plan đã chỉ.
- VĐ-4 (purge) là **nút chặn**: không fix thì re-ingest = duplication, không đo được VĐ-1.
- VĐ-2, VĐ-3 độc lập (retrieval/provider tầng khác), fix riêng.
- **Sys-prompt**: 0/4 fix được — vì lỗi nằm ở chunk bot THẤY (thiếu số / lẫn row / rớt top-K), không phải ở cách LLM diễn đạt. Khớp sacred CLAUDE.md: cấm patch retrieval/data-bug bằng sys-prompt.

---

## 3. PHƯƠNG ÁN (đúng tầng, có thứ tự)

### Bước 1 — FIX BUG PURGE (1 dòng, unblock mọi thứ) · ~1h · offline test
`ingest_core.py:402` bỏ `AND deleted_at IS NULL` (HOẶC hard-delete chunk ở `__init__.py:910`). Test: re-ingest 1 doc → chunk count giữ nguyên (không cộng dồn).

### Bước 2 — FIX GỐC: table-aware ROW-AS-CHUNK ingest (giải spa+xe cùng lúc) · ~6-10h
1. `analyze.py:454` — cho Sheets/CSV đi **row-as-chunk path KỂ CẢ khi có `##` heading** (bind heading làm context, đừng loại fast-path).
2. Mỗi row = **1 chunk atomic** (header + row đi cùng) → hết row-mixing.
3. Cột **Aliases → metadata** (searchable) thay vì đổ vào content embed → hết alias-flood.
4. col_N converter-merge (ĐÃ ship `9009eac`) + structural **date-role** → date1/date2/ngày-về phân biệt.
- N+1-proof: canary property-test domain bất kỳ (đã có `test_multibot_ingest_canary.py`).

### Bước 3 — RE-INGEST sạch 3 bot (sau Bước 1+2) · cost embed
Re-ingest qua sync API (đã fix purge) → đo: col_N→~0, spa giá đúng, xe tồn/date đúng.

### Bước 4 — FIX list-500 (graceful degradation) · ~3h · offline test
- Thêm **input/context-token guard** trước `litellm.acompletion` (`router.py:652`) → prompt vượt ngưỡng thì **shrink + retry**, KHÔNG 500 cả pipeline.
- Bỏ exempt aggregation khỏi `rerank_max_chunks_to_llm` (`rerank.py:396`) HOẶC bound prompt theo TOKEN (không chỉ char-cap 5500).

### Bước 5 — FIX legal clause-miss (retrieval tuning) · ~2h
- Nâng `DEFAULT_RERANK_RETRIEVAL_SAFETY_N` (`_01:177`) 2→5, HOẶC thêm intent của clause vào `DEFAULT_RERANK_CLIFF_SKIP_INTENTS` (`_01:218`).

### Bước 6 — VERIFY (rule#0)
- Load-test parallel 3 bot (bypass_cache) → Coverage ≥0.95, HALLU=0, đo từng câu trong 35 câu lỗi.
- Backward-verify: chunk ingest→retrieve→topK→prompt→answer cho 5 câu mẫu.

### Bonus (audit khác, offline, độc lập)
- **ING-7** inject `stats_index_repo` ở `bootstrap.py:804` (1 dòng DI).
- **PERSIST-CACHE** strong-ref task `persist.py:197` (mirror `ingest_stages_final.py:421`).

---

## 4. ƯU TIÊN
| Bước | Giải vấn đề | Risk | Cần load-test? |
|---|---|---|---|
| 1 purge | unblock re-ingest | thấp | không (offline) |
| 2 row-as-chunk | spa giá + xe tồn/date + col_N | trung (parser-adapter, vùng được phép rewrite) | không (unit canary) |
| 3 re-ingest | đo lift thật | thấp | có (đo) |
| 4 list-500 | provider crash | thấp | không (offline) |
| 5 clause-miss | legal miss | thấp | có (đo) |

**Đề xuất chạy: Bước 1 → 2 → 4 (offline, ship + unit test) → 3 re-ingest → 6 verify → 5.**
Bước 1/2/4 an toàn, test offline, KHÔNG cần load-test gate. Bước 3/6 cần API + đo.
