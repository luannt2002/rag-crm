> # ⚠️ ĐÃ BỊ THAY THẾ — KHÔNG DÙNG LÀM NGUỒN SỰ THẬT
> Nhiều claim trong file này đã bị bác ở tầng L5 (đọc code thật).
> Nguồn sự thật hiện tại: [reports/L5_CODE_TRUTH_20260714.md](L5_CODE_TRUTH_20260714.md)
> Giữ file này làm lịch sử điều tra, không phải kết luận.

---

# MASTER FLOW DEBUG — TOÀN BỘ LUỒNG, TỪNG BƯỚC, CÓ SỐ THẬT

**Ngày**: 2026-07-14 · HEAD `71682a2` · nhánh `fix-260623-ingest-expert`
**Nguồn số**: `request_steps` (n=741 request · 2026-07-01→07-13) · `system_config` (264 row) · `document_chunks` (906 chunk) · `pg_stat_user_indexes` · `EXPLAIN ANALYZE` · `git blame` / `git log -S` · gọi thật gateway LLM
**Chi tiết đầy đủ**: `reports/APPENDIX_FULL_LEDGERS_20260714.md`
**Kế hoạch sửa**: `plans/260714-expert-gap-remediation/plan-v2.md`

---

## KÝ HIỆU

| | |
|---|---|
| ✅ | EXPERT — chạy đúng, có bằng chứng |
| 🟡 | YẾU — chạy nhưng có khiếm khuyết đo được |
| 🔴 | SAI / HỎNG — có bug xác nhận |
| 💀 | CHẾT — không bao giờ chạy, hoặc chạy mà vô tác dụng |
| ☠️ | TRƠ — flag/config được set nhưng **code không đọc** |

**Nguồn gốc**: `KẾ THỪA` = `git blame` → `cd08119` (git **im lặng**, repo re-init 17/06) · `CÓ CHỦ ĐÍCH` = commit sau đó, **có message giải thích**

---
---

# PHẦN I — LUỒNG UPLOAD (ingest)

## Sơ đồ luồng THẬT (không phải luồng trong tài liệu)

```
POST /api/ragbot/documents/create        ← API CANONICAL (B2B)
  │
  ├─ U0  idempotency 3 lớp + outbox              ✅
  │
  └─▶ outbox event DocumentUploaded
        │
        ▼
   document_worker  ─────────────────────────────────────────┐
        │                                                    │
        ├─ fetch bytes                                       │
        ├─ TỰ PARSE bằng parser riêng của worker             │ 🔴 VỠ KIẾN TRÚC
        ├─ full_text = "\n\n".join(chunks)   ← LÀM PHẲNG     │
        └─ doc_service.ingest(content=full_text)             │
                    ▲                                        │
                    │  KHÔNG TRUYỀN raw_bytes  ──────────────┘
                    │
        ┌───────────┴─────────────────────────────────┐
        │  ingest_core.py:317                          │
        │    if raw_bytes is not None:   ← LUÔN FALSE  │  💀 U2 CHẾT
        │        _route_through_parser(...)            │
        └──────────────────────────────────────────────┘
                    │
                    ▼
        U1 validate ✅ → U3 clean ✅ → U4 chunk 🔴 → U5 enrich 🟡
                    → U6 vn_segment 🔴 → U7 embed+store 🟡 → finalize ✅
```

> ⚠️ **ĐƯỜNG TEST NỘI BỘ (`/test_chat/.../documents/upload`) CÓ TRUYỀN `raw_bytes`.**
> **ĐƯỜNG PRODUCTION B2B THÌ KHÔNG.**
> → **Dev test thấy đúng. Khách hàng nhận đường khác.** Đây là **pattern lặp lại 3 lần** trong codebase (xem §III.7).

---

## U0 — HTTP accept + idempotency + outbox ✅ **EXPERT**

| | |
|---|---|
| **Code** | `interfaces/http/routes/documents.py` · `application/use_cases/ingest_document.py:130-144` |
| **Cơ chế** | **3 lớp idempotency race-safe**: (1) header `X-Idempotency-Key` unique ở PG · (2) natural-key · (3) content-hash. `ON CONFLICT … RETURNING id` |
| **Verify** | `IdempotencyService` — **6 call site thật** (`self._idem.is_duplicate/register/get_prior_result_ref` ở `answer_question.py:68,69,139` + `ingest_document.py:78,79,147`). DI live ở `bootstrap.py:542`. Còn có `IngestIdempotencyService` (DB-backed) ở `routes/documents.py:133,140` + `document_worker.py:724,795` |
| ⚠️ **Audit trước SAI** | Em từng báo *"IdempotencyService có 0 caller"* → **grep sai tên thuộc tính** (`_idempotency` thay vì `_idem` thật). **Xóa nó = phá retry-safety BE-to-BE.** |

---

## WORKER — fetch + parse 🔴 **VỠ KIẾN TRÚC** — **BUG #1 TOÀN HỆ THỐNG**

| | |
|---|---|
| **Code** | `interfaces/workers/document_worker.py:514` → `full_text = "\n\n".join(c["content"] for c in _chunks ...)` · `:668-681` → `doc_service.ingest(... content=full_text ...)` |
| **Bug** | **Worker TỰ PARSE rồi LÀM PHẲNG**, truyền `content=text`. **KHÔNG truyền `raw_bytes`** |
| **Bằng chứng** | `grep -c "raw_bytes" document_worker.py` → **0**. `grep -rn "raw_bytes=" src/ragbot/` → chỉ `ingest_core.py:566` (đệ quy), `routes/sync.py:566` (route CŨ), `test_chat/document_routes.py:521` (**UI test nội bộ**) |
| **Hệ quả** | `parser_row_chunks = None` **mãi mãi** → gate `ingest_core.py:317` **không bao giờ mở** → `ingest_stages.py:763` `if parser_row_chunks and _parser_is_row_shaped` **luôn false** → **LUÔN rơi vào `smart_chunk`** |
| **Runtime** | **0/583 chunk** được row-parse. 5 doc CSV live **đều `recursive`**, dù `GoogleSheetsParser.supports("text/csv") = True` và `google_sheets ∈ _ROW_PRESERVE_PROVIDERS` |
| **Nguồn gốc** | **KẾ THỪA** (`cd08119`). `git log -S'raw_bytes' -- document_worker.py` → **RỖNG**. Worker **CHƯA BAO GIỜ** truyền. Không commit nào gỡ wiring — **nó chưa từng tồn tại** |
| 🔴 **ĐÃ TỪNG FIX — VÀ BỊ CHÍNH BUG NÀY NUỐT** | `de89da8` (07-01) *"fix(ingest): P2 whole-doc must yield to row-shaped parser (**col_N on small sheets**)"* — commit body: *"Live bug 07-01: một Google-Sheet markdown 3077 ký tự … gộp 63 chunk một-hàng-một-chunk thành MỘT chunk. Stats extractor **mất binding header per-row → mọi cột rơi về `col_N`**"*. Fix đó gate trên `_parser_row_shaped(parser_row_chunks)` — **LUÔN `None` trên worker → FIX ĐÓ CŨNG LÀ CODE CHẾT** |
| **Bằng chứng đóng đinh** | 3 doc ingest **2026-07-06 — 5 NGÀY SAU khi `de89da8` ship** — vẫn `recursive`. Doc `22112` (1 chunk / 3077 ký tự) **CHÍNH LÀ doc được nêu đích danh trong commit message đó**, **đến giờ vẫn chưa fix** |
| **Tác động T1** | `col_N` corruption → mất binding header → **CHÍNH LÀ lớp bug bịa số** mà cả chương trình ADR-0008 đang đuổi theo |
| **Fix** | Worker truyền `raw_bytes=_raw`, bỏ `"\n\n".join(...)`. ⚠️ **CẦN RE-INGEST** (5 doc / 583 chunk) |

---

## PRE-SNIFF mime 🟡

| | |
|---|---|
| **Code** | `ingest_core.py:256` |
| **Vấn đề** | Sniff **CHỈ chạy khi mime/ext KHÔNG match** → là **cứu hộ**, không phải **cross-check** |
| 🛡️ **Comment phải bảo vệ** | *"**2026-05-27** — sniff real MIME when declared is ambiguous. Closes **silent-fail bug where the parser registry returned None for octet-stream uploads → 0 chunks ingested**."* ← **Sự cố khai sinh** ra tầng byte-sniff, giờ là **sacred ingest rule trong CLAUDE.md** |

---

## U1 — validate ✅ **EXPERT**
Tenant guard · **allow-list URL** (chống PoisonedRAG) · quota.

---

## U2 — parse registry (detect_parser) 💀 **CHẾT TRÊN LUỒNG PRODUCTION**

```python
ingest_core.py:317    if raw_bytes is not None:        ← GATE, LUÔN FALSE trên worker
ingest_core.py:320        _route_through_parser(...)   ← nguồn DUY NHẤT của parser_row_chunks
```
→ **Row-as-chunk cho Excel/Sheets/CSV BẤT KHẢ ĐẠT trên `POST /documents/create`.**
→ Xem WORKER ở trên.

---

## PII REDACT (boundary) ✅ — **fix hôm qua**

`infrastructure/guardrails/local_guardrail.py::redact_pii()` + `nodes/guard_input.py` thực thi action `redact` (trước đó chỉ **gắn cờ rồi ship raw PII**).

🛡️ **Comment phải bảo vệ** (`_06_llm_defaults.py:131`):
> *"`pii_vi_cmnd` is **deliberately EXCLUDED**: its pattern is ANY bare 9- or 12-digit number — which in a catalog corpus includes **PRICES** (150000000 = 150 triệu is 9 digits) and SKUs."*

⚠️ **CẢNH BÁO SEED**: 3 rule PII (`pii_vi_phone`, `pii_vi_email`, `pii_en_ssn`) **KHÔNG được seed bởi chain alembic active** — chúng đến từ archive `20260516_010f`. **DB fresh → redact không có rule để chạy.**

---

## DEDUP + UPSERT ✅ **EXPERT**
content-hash + `source_url` + FK-safe UPSERT, race-safe.

---

## U3 — clean ✅
CleanBase tier-0.
🛡️ **Comment bảo vệ** (`ingest_stages.py:304`): *"**Production bug 2026-05-18**: `_sanitizer` không phải lúc nào cũng init… AttributeError trên **4/4 ingest_clean row**"* ← giải thích `getattr(self, "_sanitizer", None)` mà mọi linter sẽ gắn cờ.

---

## U4 — chunk 🔴 **NHIỀU BUG**

### U4.a — Seam `0.45 < 0.6` 🟡 PARTIAL

```python
analyze.py:538  if confidence < DEFAULT_STRATEGY_MIN_CONFIDENCE:      # 0.45
analyze.py:539      return (CHUNK_STRATEGY_RECURSIVE, 0.45)           # ← phát ra ĐÚNG 0.45

analyze.py:661  if confidence < conf_threshold:                        # 0.6  (L5 rule 1)
analyze.py:662      overrides.append((CHUNK_STRATEGY_HYBRID, ...))
```
`0.45 < 0.6` **luôn đúng** → nhánh **fallback** recursive phát ra confidence mà **stage ngay sau CHẮC CHẮN từ chối**.

| | |
|---|---|
| 🔴 **2 vế audit cũ SAI** | (a) *"recursive unreachable"* — **SAI**, nó vẫn thắng bằng max-score khi conf ≥ 0.6 (`test_adapchunk_l5_crosscheck.py:127-128`). (b) *"default = hybrid→proposition"* — **SAI**, rule 1 append **đầu tiên** → rơi vào **`hybrid` rồi DỪNG** |
| **Xác nhận DB** | strategy live: **`recursive` 689 chunk · `hdt` 217 chunk**. **`proposition` KHÔNG LIVE** |
| **Gốc rễ** | `0.6` **CÓ NGUỒN** (comment `analyze.py:557`: *"Databricks AI-Driven Chunking blog (2024)"*). `0.45` **KHÔNG có bất kỳ lời giải thích nào** — không comment, không ADR, không plan. **2 số từ 2 nguồn, CHƯA BAO GIỜ đối chiếu** |
| **Chain resolve** | `DEFAULT_STRATEGY_MIN_CONFIDENCE` đọc **THẲNG từ constant** (`analyze.py:538`) — không DB → **sửa constants.py CÓ tác dụng** (hiếm) |
| ⚠️ **CHƯA VERIFY — CHẶN SHIP** | **Không đo được seam bắn bao nhiêu lần.** Strategy **chỉ log structlog**: `document_chunks.metadata_json` null 902 row · `audit_log` **0 event `adapchunk%`** → **PHẢI persist `chunk_strategy` TRƯỚC** |
| ☠️ **`adapchunk_layer5_cross_check_enabled` TRƠ** | `apply_cross_check` gọi **VÔ ĐIỀU KIỆN** (`ingest_stages.py:632-637`); flag chỉ đọc khi `strategy is None`. Docstring `analyze.py:567` còn ghi *"default OFF"* trong khi **ON cả code lẫn DB** |

### U4.b — Coverage gate **MÙ** 🔴 (thủ phạm em đổ SAI)

```python
coverage.py:203   pos = norm_source.find(norm_chunk, cursor)
                  if pos == -1: pos = norm_source.find(norm_chunk)
                  if pos == -1: unlocated += 1; continue    ← KHÔNG đóng góp interval
```
**Chunk mang text KHÔNG có trong source → không định vị được → cả đoạn đọc thành gap.**

🔴 **Thủ phạm KHÔNG PHẢI `proposition`** (không live). Là **`_chunk_hdt` — 217 chunk LIVE** — prepend `[path]\n` **lúc CHUNK**:
```
strategies.py:351   prefix = f"[{path_info['full']}]\n"     # trong _chunk_hdt
strategies.py:754   prefix = f"[{path_info['full']}]\n"     # trong _chunk_hybrid
```

**Repro xác định (chạy `check_chunk_gaps` thật, tách đúng 1 biến):**
| hình dạng chunk | `ok` | `coverage_ratio` | `unlocated` |
|---|---|---|---|
| có path-prefix (`hdt`/`hybrid`) | False | **0.0000** | 1 |
| verbatim (`recursive`) | False | 0.8462 | 0 |

> **0.0000 trong khi KHÔNG MẤT GÌ CẢ.** Gate **không phân biệt được** *"mất sạch dữ liệu"* với *"chunker thêm 1 dòng header"*.

**Gate còn KHÔNG RĂNG**: `ingest_stages.py:890` chỉ `if not _cov.ok: logger.warning(...)`. Comment `:886-888`: *"NEVER raises — pure observability"*.

⚠️ **FIX-REFIX**: `75f5c96` ship → mất → `d7bd5ac` *"**salvaged from Wave-1**"*.
⚠️ **CẤM tune `DEFAULT_COVERAGE_TOL`** — ratio **vô nghĩa VỀ CẤU TRÚC**, không số `tol` nào sửa được `find() == -1`.

### U4.c — Atomic protect **OFF** 🟡

`_00_app_env_taxonomy.py:126` → `DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED = False`. Key **vắng khỏi `system_config`**, **0 bot override**.
**Sắc thái**: flag gate `_split_into_blocks_with_atomic` (`chunking/__init__.py:329`) — table fast-path **return TRƯỚC** → chỉ áp cho strategy **KHÔNG-phải-bảng**.
⚠️ **CHƯA VERIFY hậu quả** — chưa ai chạy chunker trên doc formula/code để **quan sát** một lần cắt giữa block. **Rule#0: cần failing test TRƯỚC.**

### U4.d — Intro/footer bảng bị VỨT 🔴 — **flag LIVE-TRUE nhưng TRƠ**

| | |
|---|---|
| **Feature CÓ và ĐANG BẬT** | `csv_chunker.py:250-355` `_chunk_table_csv_with_context` phát chunk header (`region.pre`) + footer (`region.post`). LIVE: `table_csv_emit_header_footer_chunks_enabled = **true**` |
| 🔴 **NHƯNG strategy live KHÔNG PHẢI `table_csv`** | LIVE: `chunking_policy = {"table_strategy": "**table_dual_index**"}`. `_chunk_table_dual_index` (`csv_chunker.py:357+`) **KHÔNG NHẬN** param `header_footer_enabled` và cắt `lines[header_idx : last_data_idx+1]` → **`pre`/`post` bị LOẠI TRỪ VỀ CẤU TRÚC**. `chunking/__init__.py:515` gọi nó **không kèm flag** |
| ⚠️ **FIX-REFIX** | `20260612_0209` **CỐ Ý** lật `table_csv` → `table_dual_index` để **fix aggregation recall** (*"'liệt kê dịch vụ' → đáp án ở rank 21, ngoài top-20"*) — **QUÊN PORT logic pre/post** |
| 🔴 **TEST TẠO NIỀM TIN GIẢ** | `test_chunk_table_csv_header_footer.py` gọi **TRỰC TIẾP** `_chunk_table_csv_with_context` (`:60,73,84,96,113`), **KHÔNG qua dispatch live** → **6/6 TEST XANH trong khi PROD VỨT intro/footer** |
| 🔴🔴 **DRIFT PROD vs FRESH DB** | **KHÔNG migration ACTIVE nào seed `chunking_policy`.** Seed chỉ nằm trong archive; `20260618_squash_baseline.py` **không mang theo** → **DB fresh rơi về `table_csv`** → **header/footer CHẠY trên dev, HỎNG trên prod** |

---

## U5 — enrich (contextual retrieval) 🟡 **OFF CÓ LÝ DO**

**KHÔNG được bật lại.** 🛡️ Comment bảo vệ (`ingest_stages_enrich.py:232`, `:445`):
> *"per-chunk nano with full-doc context = **19k tokens/call = O(n²) storm**, chunks=0 until it finished… **Two CR impls existed — disabling #1 alone left this one firing** (the 'whack-a-mole' root cause)."*
> *"**Do NOT re-enable expecting 'more context'** — re-enabling brings back the O(n²) storm."*

🛡️ (`ingest_stages_enrich.py:375`): *"burst rộng-bằng-Semaphore chạy đua **trước khi response đầu tiên kịp seed cache** — đợt mở màn cache **~26-54% vs ~97% khi ấm**. **Seed MỘT enrich TUẦN TỰ, rồi mới fan out**"* ← lời gọi tuần tự cố ý trông như `gather()` bị bỏ sót.

---

## U6 — vn_segment 🔴 **ĐANG PHÁ HỦY RECALL**

### 🔴 Audit cũ SAI HOÀN TOÀN — và sai theo hướng nguy hiểm

Em từng nói: *"index lưu `chăm_sóc`, query tìm `chăm AND sóc` → miss. Fix = segment ở query side."*
**Nếu ship theo đó → RETRIEVAL TỆ ĐI.**

**Postgres coi `_` là `blank` token = DẤU PHÂN CÁCH → nó XÓA underscore:**
```sql
ts_debug('simple','cham_soc dr._medispa lop_xe')
→ asciiword 'cham' | blank '_' | asciiword 'soc'      ← TÁCH, underscore XÓA
→ file 'dr._medispa'                                   ← SỐNG NGUYÊN, vì có DẤU CHẤM
→ asciiword 'lop' | blank '_' | asciiword 'xe'         ← TÁCH

to_tsvector('simple','chăm_sóc da mặt')  →  'chăm':1 'da':3 'mặt':4 'sóc':2
```
→ **Underscore CHỈ sống khi token có kèm `.` hoặc `/`** (Postgres đổi `asciiword` → `file`/`url`).
→ **Từ ghép VN thuần LUÔN bị tách.** **ZERO lexeme từ ghép VN tồn tại trong index.**
→ **Query hiện tại ĐANG ĐÚNG.** Segment query sẽ biến `&` → `<->` (**phrase-adjacency, HẸP HƠN**) → **GIẢM recall.**

### 🔥 Bug THẬT — ở chiều NGƯỢC LẠI

Trigger: `to_tsvector('simple', COALESCE(NEW.content_segmented, NEW.content, ''))`
→ index **text ĐÃ SEGMENT** → underthesea nối `<Prefix>._<Brand>` → parser **NUỐT THÀNH 1 TOKEN `file`**.

| Đo trên corpus live | |
|---|---|
| chunk chứa 1 brand token | **28** |
| index **HIỆN TẠI** tìm ra | **4** |
| nếu **KHÔNG segment** | **28** |
| chunk mà segment **GIÚP** tìm ra (không segment thì mất) | **0** ← **đối chứng ngược** |
| chunk lệch tsvector (`to_tsvector(content) ≠ search_vector`) | **436 / 906** |
| lexeme phân biệt mang underscore | **737** (URL + token có `.` hoặc `/`) |

> 🔴 **24/28 chunk BẤT KHẢ TRUY CẬP cho query tên thương hiệu.**
> 🔴 **Segmentation = LỖ RÒNG TUYỆT ĐỐI. KHÔNG MỘT CHUNK NÀO ĐƯỢC LỢI.**
> Token bị mất là loại đắt nhất: **tên thương hiệu** và **token đơn-giá** (`/45`, `/55`, `/65`).

### Bất đối xứng gate (C3)

```python
ingest_stages.py:996    elif vi_seg_enabled and _vi_seg_lang_eligible:      # gate 2 điều kiện
pgvector_store.py:409   tokenized_query = segment_vi_compounds(query_text)  # VÔ ĐIỀU KIỆN
pgvector_store.py:417   tokenized_normalized = segment_vi_compounds(...)    # VÔ ĐIỀU KIỆN
```
→ Bot **thuần tiếng Anh** vẫn bị segment query dù corpus **chưa bao giờ** được segment.
Đường thứ 3: `PgBM25Retrieval` **không segment, không gate** → **hành vi THỨ BA**.

### Comment NÓI DỐI + Test GHIM BUG

- `pgvector_store.py:406-408`: *"ingest indexes content_segmented (compound joined via `_`); query side must mirror that…"* → **SAI SỰ THẬT, parser xóa `_`**
- `test_bm25_symmetric_segment.py` **assert query phải segment giống ingest** → **test đang BẢO VỆ bug**

### ⚠️ Fix đã có, MẮC KẸT
`be94f58` ("expert remediation Wave2") thêm language gate + test → **CHƯA MERGE**, kẹt trên `integ-260624-wave1`.
⚠️ **KHÔNG merge gate đó** — gate cho một call **sắp bị xóa** là vô nghĩa.

### FIX ĐÚNG (ngược 180° so với audit cũ)
1. Trigger index `NEW.content` thay vì `COALESCE(NEW.content_segmented, …)`
2. **XÓA** 2 call `segment_vi_compounds` query-side (`pgvector_store.py:409,417`)
3. **Nghỉ hưu** `test_bm25_symmetric_segment.py`
⚠️ **CẦN REINDEX tsvector toàn corpus.** **KHÔNG** cần re-ingest (source text không đổi).

---

## U7 — embed + store 🟡 **THIẾU DIM-GUARD**

| | Trạng thái |
|---|---|
| **COUNT guard** | ✅ `ingest_stages_store.py:521` — `len(embed_results) != len(_chunks_needing_embed)` → soft-delete doc + `raise ExternalServiceError`. **Fail loud, đúng** |
| **DIM guard per-vector** | 🔴 **KHÔNG CÓ.** Chỗ duy nhất có `len(vector)` (`:555-560`) **chỉ đọc `embed_results[0]`**, nuốt `TypeError`, và **chỉ là metadata audit**. Vector `[1:]` **không bao giờ được kiểm** |
| **Dim check THẬT ở đâu?** | Chỉ trong **`health_check`** (`zeroentropy_embedder.py:163`) = **warmup probe**, KHÔNG ở hot path. Comment `:158-161` cho thấy **tác giả BIẾT** và **chọn gác warmup THAY VÌ hot path** → **provider flip giữa chừng KHÔNG được bảo vệ** |
| **Wire dim** | 🔴 **HARDCODE ctor** — `zeroentropy_embedder.py:77` `dimensions: int = DEFAULT_ZEROENTROPY_EMBEDDING_DIM` (1280); `:229` gửi `self._dimensions`. **`spec.dimension` KHÔNG BAO GIỜ được đọc** |
| **2 default XUNG KHẮC** | `DEFAULT_EMBEDDING_DIM = 1024` (`_00_*:146`) vs `DEFAULT_ZEROENTROPY_EMBEDDING_DIM = 1280` (`_02_*:68`). Cột DB = `vector(1280)`. **Bug đang TIỀM ẨN chỉ vì cột tình cờ khớp ctor default** |

🛡️ **Comment bảo vệ** (`_02_*:66,94`): *"matryoshka 1280 vì **pgvector HNSW trần 2000 dim** (full 2560 cần halfvec)"* + *"**BẮT BUỘC re-embed sau khi toggle**"*

---

## SWAP EMBEDDER CÙNG DIM 🟡 — cơ chế thật, thiệt hại **REFUTED**

| | |
|---|---|
| **Cơ chế** | `alembic/versions/20260626_embed_swap_to_openai.py` — toàn bộ `upgrade()` là **3 câu `UPDATE system_config`**. **KHÔNG re-embed / null / xóa MỘT vector nào.** Docstring **tự thú**: *"**REQUIRES re-embedding the corpus**: existing vectors are Jina-1024 (different space)"* → **cưỡng chế bởi KHÔNG GÌ CẢ** |
| **Guard** | `_check_embed_model_consistency` (`query_graph.py:754`) — docstring: *"**Detection-only, never raises**"*. Tại call site (`:1635`) **giá trị trả về BỊ VỨT** |
| 🔴 **Lỗ hổng SÂU HƠN** | Nó so `_pcfg(state,"embedding_model")` (config **HIỆN TẠI**) với `spec.model_name` (resolve **lúc query**) — **CẢ HAI dẫn xuất từ CÙNG config** → sau swap **chúng KHỚP** → **BẤT LỰC VỀ CẤU TRÚC** trong việc phát hiện vector cũ |
| **Không có provenance** | `document_chunks` có 16 cột, **không cột nào** ghi embedding model/provider/version/dim |
| 🟢 **Thiệt hại live REFUTED** | **906/906 chunk đều SAU ngày swap 06-26.** Vector đều **1280**. Không drift dim ở đâu |
| **Thứ đã cứu họ** | Swap sang ZE đổi width **1024→1280** → pgvector **hard-fail** → **tự phát hiện**. Chỉ bước **jina-1024 → OpenAI-1024** là cùng width nên **im lặng** |
| 🔴 **Phát hiện thật** | Corpus sạch **CHỈ NHỜ MAY MẮN** (`created_at` tình cờ sau swap). **KHÔNG CÓ CÁCH NÀO chứng minh provenance của MỘT vector cụ thể.** Nếu 1 bot không được re-ingest → **không gì bắt được**, retrieval "chạy bình thường" trên **không gian nhúng ngoại lai** |

---

## FINALIZE ✅ **EXPERT**

soft-fail sentinel có floor · stats rebuild từ **full chunk set** (*fix hôm qua* `ad82511` — trước đó re-ingest 1 phần **xóa sạch stats index của mọi entity KHÔNG đổi**).

🛡️ **3 comment bảo vệ**:
- `:263` — *"2 bug gây **4 doc stuck DRAFT 25+ phút trong prod 2026-05-13**"*
- `:275` — *"Parent-child **cố ý KHÔNG embed** parent chunk… Đếm parent NULL là fail là SAI (regression 2026-05-13)"*
- `:403` — *"log này trước đây bắn **vô điều kiện** — phát `document_ingested` cho cả doc THẤT BẠI… **một lời nói dối observability**"*

---
---

# PHẦN II — LUỒNG QUERY (21 node, live 14)

## Runtime tổng — `request_steps`, n=741 request

```
step              |  n   | avg_ms | in_tok     | cost_usd | qua router?
generate          | 1751 | 16505  | 10,892,822 | $4.6088  | ✅
understand_query  | 1530 | 10314  |          0 | $0.0000  | ❌ BYPASS
grade             |  741 |   911  |          0 | $0.0000  | ❌ BYPASS
rerank            |  741 |  1769  |          0 | $0.0000  | ❌ BYPASS
```

---

## 1 — `guard_input` ✅ (fix hôm qua)

PII redact **thực thi thật** (trước đó chỉ gắn cờ rồi ship raw PII). Allow-list `pii_vi_phone`/`pii_vi_email`/`pii_en_ssn`; **`pii_vi_cmnd` cố ý loại** (pattern khớp GIÁ 9 chữ số).
Còn 1 missed-gather.

---

## 2 — `cache_check_parallel` 🔴 **LỖ HỔNG AN NINH LIVE**

### Nửa 1 — cache hit **BỎ QUA HOÀN TOÀN `guard_output`** (chứng minh bằng TOPOLOGY)

```python
routing.py:56-59      def _cache_route(state):
                          if state.get("cache_status") == "hit" and state.get("answer"):
                              return "persist"                      ← THẲNG TỚI persist

query_graph.py:3038   graph.add_edge("persist", END)
query_graph.py:3027   graph.add_edge("critique_parse", "guard_output")   ← guard_output CHỈ Ở NHÁNH KIA
```
→ **Cache hit chạy tới END sau khi thực thi ZERO output guard.**

### Nửa 2 — cache key **KHÔNG chứa guardrail**

```python
cache_port.py:90   return f"t:{tenant}:bot:{bot}:bv:{bot_version}:cv:{corpus_version}"
```
`bot_version` = `_compute_bot_cache_version` — **đúng 3 input**: `system_prompt`, `oos_answer_template`, `custom_vocabulary`. **Guardrail rule KHÔNG phải input.**
`GuardrailRuleLoader.invalidate()` **chỉ** xóa L1 cache của chính nó — **KHÔNG BAO GIỜ đụng `semantic_cache`**.

### Cửa sổ bypass = **TTL 3600s** (khớp chính xác)
> Owner thêm rule BLOCK → **mọi query đã cache tiếp tục phục vụ câu trả lời cũ, GIỜ ĐÃ BỊ CẤM, KHÔNG QUA GUARD, tới 1 tiếng.** Không có đường invalidation nào rút ngắn.

**Nguồn gốc**: KẾ THỪA (`cd08119`). **Chưa từng fix, chưa từng regress.** Không ADR, không comment thừa nhận.
**Fix**: (1) route cache hit **QUA `guard_output`** — *guard bỏ qua được thì không phải guard* · (2) hash ruleset vào `_compute_bot_cache_version`.

---

## 3 — `understand_query` 🔴 **10.3 GIÂY / CALL · 1,530 CALL**

| | |
|---|---|
| **Runtime** | **1,530 call · avg 10,314ms · cost $0 (token không ghi)** |
| 🔴 **Root cause** | **Gateway PHỚT LỜ `response_format`.** Call 1 (`json_object`, **không có schema trong prompt**) → **trả VĂN XUÔI** → validate fail → `_fallback_json_parse` không thấy `{` → `None` → **repair retry** → `_build_repair_messages` **ĐƯA SCHEMA VÀO PROMPT** → **JSON hợp lệ** |
| **Bằng chứng** | journalctl 13/07: **112 `structured_output_repair_retry` + 122 `structured_output_validation_failed`** cho `UnderstandOutput`. Thử cả strict `json_schema` → **cũng văn xuôi**. `response_format` là **NO-OP trên gateway này** |
| **Hệ quả** | **Trả thêm NGUYÊN MỘT round-trip LLM cho GẦN NHƯ MỌI REQUEST** |
| **Fix hôm qua** | `5c4fdda` thêm `_accept_query_alias` cho `UnderstandOutput` (gateway echo `{"query":...}`) — **giảm được repair-retry**, nhưng **không phải root cause đầy đủ** |
| ⭐ **FIX ĐÚNG** | **Đưa schema vào prompt NGAY CALL ĐẦU.** `_build_repair_messages` **đã làm đúng thế này rồi** ở vòng 2 — chỉ là **làm sớm hơn 1 vòng**. → cắt **~1,530 round-trip/ngày**; `understand` **10.3s → ~3.5s** |
| 🔒 **Sacred #10** | **AN TOÀN** — prompt **nội bộ pipeline**, không phải prompt answer của bot owner |

---

## 4 — `condense_question` 💀 **CHẾT >2 THÁNG**
## 5 — `router` 💀 **CHẾT >2 THÁNG**

```python
routing.py:56-62   if _pcfg(state, "merge_condense_router", True):
                       return "understand_query"
                   return "condense_question"          ← đường DUY NHẤT tới condense_question
query_graph.py:2994  graph.add_edge("condense_question", "router")   ← đường DUY NHẤT tới router
```

**LIVE CONFIG:**
```
system_config.pipeline_merge_condense_router = TRUE      (updated_at 2026-05-05)
per-bot plan_limits override                 = 0 ROWS
system_config.query_router_provider          = "null"    ← Null Object
```
→ **CẢ 2 NODE CHẾT TRÊN MỌI QUERY, CHO MỌI BOT, SUỐT >2 THÁNG.**
→ **2 LLM call-site** nuôi cho path không chạy: `nodes/condense_question.py:88` · `nodes/router.py:37`

🔴 **VI PHẠM ZERO-HARDCODE**: default `True` **inline ở 3 chỗ** (`routing.py:60`, `chat_worker/pipeline_config.py:468`, `test_chat/_pipeline_config.py:409`) — **KHÔNG có constant nào**.
⚠️ **KHÔNG xóa `_router_route`** — nó **VẪN SỐNG**, gọi bởi `_understand_query_route` (`routing.py:90`) và `_complexity_route` (`:97`).

---

## 6 — `rewrite + multi-query` 🟡
MQ **OFF cho `factoid`/`multi_hop`/`comparison`** (live DB).

---

## 7 — `decompose` ✅
Confidence-gated 0.7.

---

## 8 — `query_complexity` 🔴 **2 NHÁNH VÔ DỤNG MỖI QUERY**

`query_complexity_node.py:57-62` gather **3 nhánh**; `:93` `return merged` — **chỉ nhánh A tới được state**.

| nhánh | code | thực tế |
|---|---|---|
| **B** `_run_router_select_model` | `query_graph.py:2832` — docstring: *"No state keys are written — **purely observability**"*. Trả `{}` | **NHƯNG** `:2843` `await model_resolver.resolve_runtime(purpose="understand_query")` = **1 resolver round-trip THẬT** + 1 row `request_steps` |
| **C** `_run_semantic_cache_preflight` | `:2877` — *"**Does NOT re-query pgvector**… Returns `{}` always"* | 1 row `request_steps`, **0 DB** |

**Chi phí**: **2 row `request_steps` + 1 resolver round-trip / query, ZERO tác dụng.**
*(Sửa số em nói trước: **2 row, KHÔNG PHẢI 3** — row thứ 3 (`query_complexity`) **được `_complexity_route` dùng thật** ở `routing.py:93-97`)*

**Nguồn gốc**: **CÓ CHỦ ĐÍCH** `17eaac6` (06-19) *"refactor: extract query_complexity_node (Phase D.7)"* → refactor đó **SONG SONG HÓA sự lãng phí thay vì gỡ nó**. Tự dán nhãn *"telemetry only"* → **dead-weight ĐÃ BIẾT**.

---

## 9 — `adaptive_decompose` ✅

---

## 10 — `retrieve` 🔴🔴 **NHIỀU BUG**

### 10.a — HNSW **KHÔNG BAO GIỜ ĐƯỢC DÙNG** ✅ sự kiện · 🔴 **root cause em nói SAI**

```
pg_stat_user_indexes:
  document_chunks | idx_chunks_search_vector  | idx_scan = 19,020   ← BM25 GIN
  document_chunks | ix_chunks_embedding_hnsw  | idx_scan =      0   ← CHƯA DÙNG LẦN NÀO
```
`EXPLAIN ANALYZE` query dense thật (`pgvector_store.py:329-336`):
```
Limit  (cost=297.47..297.50 rows=10) (actual time=9.616..9.620)
  -> Sort  (cost=297.47..298.50 rows=413)
       Sort Key: ((embedding <=> '[...]'::vector))
       -> Seq Scan on document_chunks  (cost=0.00..288.55)     ← BRUTE-FORCE
```

**🔴 KHÔNG PHẢI 4 giả thuyết em nêu:**

| Giả thuyết | Verdict |
|---|---|
| Sai opclass | ❌ Index `vector_cosine_ops`, query `<=>` (cosine). **KHỚP** |
| Sai cột | ❌ `DEFAULT_EMBEDDING_COLUMN = "embedding"`, index trên `embedding`. Không có `embedding_v3` |
| Filter `WHERE record_bot_id` chặn pushdown | ❌ **PHẢN CHỨNG QUYẾT ĐỊNH**: chạy lại **BỎ HẲN filter** → planner **VẪN Seq Scan** (cost 285.45) |

**ROOT CAUSE THẬT: cost model của planner ở kích thước bảng này.**
```
906 row.   Seq-scan + sort  = cost 285
           HNSW startup     = cost 5475.29    ← ước lượng THỪA 19×
```
→ **Planner ĐÚNG VỀ MẶT SỐ HỌC khi từ chối index.**
Index **vẫn dùng được**: ép `enable_seqscan=off` → `Index Scan using ix_chunks_embedding_hnsw` **2.48ms** (nhanh hơn seq scan 13ms). **Cost model sai, không phải index sai.**

**KHÔNG mất recall hôm nay** — seq scan là **EXACT (100% recall)**. Probe per-bot: **10/10 cho cả 6 bot**.

**Vách đá tiềm ẩn CÓ THẬT và ĐÃ chứng minh**: ép HNSW + query vector rơi vào vùng bot khác:
- `iterative_scan=off` → **rows=0** (*"Rows Removed by Filter: 71"*) — **sập recall hoàn toàn, trả về IM LẶNG như "không có chunk"**
- `iterative_scan=relaxed_order` → **rows=10** ✔

Ngoại suy: planner **lật sang HNSW ở ~17k chunk** — **NGOẠI SUY, không phải đo**.
`hnsw.iterative_scan` → grep `src/` = **0 hit**. pgvector = **0.8.1** (setting có sẵn).

🔴 **DEFECT THẬT = COMMENT NÓI DỐI (3 chỗ)** — `pgvector_store.py:226-238`:
> *"…Pre-0108 this filter sat behind a subquery which prevented the planner from activating the HNSW index (**live evidence: `ix_chunks_embedding_hnsw idx_scan = 0` over a 22 MB index**). … **When no doc-level filter is requested the subquery is omitted entirely and HNSW activates.**"*

Nó **trích chính `idx_scan = 0`** làm triệu chứng TRƯỚC-fix, rồi **khẳng định fix đã chạy. `idx_scan` VẪN LÀ 0. Fix đó CHƯA BAO GIỜ chạy.**
`:257` lặp lại. Docstring `:4` ghi *"m=16, ef=64"* trong khi index thật là **m=32 / ef_construction=200**.

⚠️ **ĐÃ TRIAGE RỒI** — `plans/20260709-remediation-donow/plan.md:13`: *"HNSW `idx_scan=0` → **KHÔNG PHẢI BUG** (planner-correct) … **KHÔNG ship fix now**."*
Triage đó **ĐÚNG SỰ THẬT**. Nó **quên làm** là **xóa comment nói dối** — và chính comment đó **giữ cho chẩn đoán sai sống mãi** (nó vừa lừa cả em).

### 10.b — VN tokenizer bất đối xứng
Xem **U6**. Query side **ĐANG ĐÚNG**; bug ở **ingest**.

### 10.c — Dense query **KHÔNG NFC-normalize** 🔴

| Luồng | normalize? | evidence |
|---|---|---|
| Ingest | ✅ | `text_processing.py:82` → `normalize_vn(text)` |
| **Sparse query** | ✅ | `pgvector_store.py:391` → `normalize_vn(query_text)` — comment: *"NFC normalize to match ingest path; NFD inputs (macOS/mobile) would otherwise miss NFC-indexed content"* |
| **Dense query** | 🔴 **KHÔNG** | `_embed_query` (`query_graph.py:1553`) đưa `query_text` **thẳng** vào `embed_one` (`:1638`). `grep normalize_vn src/ragbot/infrastructure/embedding/*.py` → **0 hit** |

→ Query từ **macOS/iOS (NFD)** được **embed ở dạng NFD**, corpus embed ở **NFC** → **LỆCH KHÔNG GIAN VECTOR**. Dòng `:391` "sửa" nó **chỉ cho nhánh sparse, SAU KHI vector đã sinh xong**.
**Nguồn gốc**: KẾ THỪA. Dòng `:391` **chưa từng đụng lại**. **Không cố ý** — comment nói rõ ý định là **đối xứng với ingest**.
**Fix**: `normalize_vn(query_text)` **1 lần ở đầu `_embed_query`**, **TRƯỚC cache lookup** (`:1606`).
**KHÔNG** nhét vào embedder adapter → **vi phạm domain-neutral**.

### 10.d — 0-chunk exit bỏ qua recovery 🟡

### 10.e — 🛡️ Comment bảo vệ ở retrieve
- `pgvector_store.py:259` — *"correlated EXISTS subquery chạy **per-candidate chunk** → **~80% chi phí retrieve p50 1.6s**"* ← `doc_deleted_at` trông như denorm thừa
- `pgvector_store.py:512` — *"**Verified 2026-06-19**: giả định 'BM25 đã khớp literal token' đúng với **keyword query**, KHÔNG đúng với **câu hỏi tự nhiên**"* ← **lật đổ niềm tin sai vẫn còn viết 20 dòng phía trên**
- `pgvector_store.py:580` — *"struct params **PHẢI giữ bound**… bỏ → `InvalidRequestError: struct_p0 has no value` trên MỌI structural-pointer query"*
- `query_graph.py:2320` — *"`parse_code_query` yêu cầu **MỘT CHỮ CÁI**, nên anchor chỉ-có-số 'Điều 34' không bao giờ khớp"* ← **chứng minh domain-neutral**

---

## 11 — `graph_retrieve` 💀 **TẮT 3 TẦNG**

```python
routing.py:234-236   graph_mode = _pcfg(state, "graph_rag_mode", "disabled")
                     if graph_mode == "disabled": return "rerank"      ← node KHÔNG BAO GIỜ được chọn
```
```
system_config.graph_rag_default_mode        = "disabled"
system_config.graph_rag_entity_extraction_model = ""       ← RỖNG, không model nào dựng KG
resolve_kg_service() → None khi mode disabled
```
→ **Tắt ở 3 tầng độc lập. KHÔNG CÓ knowledge graph nào để retrieve.**
**Bật = LLM call PER CHUNK lúc ingest** (bão token spreadsheet) + KG storage → **T2-âm nặng**.
🚫 **QUYẾT ĐỊNH CẤP CHƯƠNG TRÌNH, không phải fix wiring.**

---

## 12 — `rerank` 🔴 **57.7% BỊ BÁC — 0/741**

### 🔴 EM SAI. Bác bỏ hoàn toàn.

Em từng nói: *"`factoid` trong skip-list → 57.7% retrieval bỏ qua reranker."*

**Runtime `request_steps` (`step_name='rerank'`, 741 row):**
```
mode                count    pct
rerank               740    99.9%
rerank_fallback        1     0.1%
intent_skip_set        0     0.0%     ←←← BẰNG KHÔNG
```

**Em ĐỌC HẰNG SỐ RỒI SUY RA RUNTIME — vi phạm rule#0.** Code thật có **2 điều kiện AND**:
```python
rerank.py:140-145
    _size_safety = len(inp) <= int(top_n)
    _intent_skip_set = (bool(_skip_set) and _intent_lc in _skip_set
                        and _size_safety)          # ←←← EM BỎ SÓT VẾ NÀY
```
**Vì sao không bao giờ bắn:**
```
input <= top_n ?    false: 700 (94.5%)    true: 41 (5.5%)
pool/top_n:         20/7 → 566 row · 20/5 → 55 · 20/20 → 33 · 20/12 → 25
```
Retrieval đưa pool **20**, `factoid` có `top_n=7` → `20 <= 7` = **false** → **KHÔNG BAO GIỜ SKIP.**
41 row có size-safety pass đều là `20/20` = **`aggregation`**, mà `aggregation` **không nằm trong skip set**.

🚫 **GẠCH KHỎI PLAN.** Gỡ `factoid` = **no-op trên traffic** + **vỡ ~10 assertion**/3 file test = **fix bẩn làm nát code**.

### ⚠️ Bug thật ở killswitch reranker
`rerank.py:153` `elif not enabled and not _per_bot_reranker_active` → **binding reranker per-bot GHI ĐÈ KILLSWITCH.** **Bug trong 1 killswitch.**

🛡️ **Comment bảo vệ** (`reranker_resolver.py:304`): *"system_config drift (provider 'jina' ⊥ model 'zerank-2') **âm thầm hạ cấp MỌI bot không-binding xuống NullReranker**. Fail LOUD."*

---

## 12b — CLIFF FILTER 🔴🔴 **18.1% QUERY → LLM CHỈ NHẬN 1 CHUNK**

### Code — `min_keep` KHÔNG PHẢI SÀN

```python
retrieval_filter.py:127   floor_kept = [c for c in sorted_chunks if score >= absolute_floor]
                                            ↑ CẮT FLOOR TRƯỚC, KHÔNG HỎI min_keep

:130  if not floor_kept and sorted_chunks and force_min_keep:
:131      return [sorted_chunks[0]], {"reason": "empty_context_safety_keep_top1"}   ← 1 CHUNK

:139  if len(floor_kept) <= 1:
:140      return floor_kept, {"reason": "below_floor_or_single"}                    ← 1 CHUNK

:154  if gap > gap_ratio and i >= min_keep:      ← min_keep CHỈ gác nhánh gap-cut
```

### Runtime — 741 row

```
n_kept = 1  →  134 row  =  18.1%

cliff_reason:
  empty_context_safety_keep_top1  =  79   ← nhánh floor
  below_floor_or_single           =  55   ← nhánh floor       (79+55 = 134)
  no_cliff_kept_all               = 604
  cliff (gap-cut)                 =   3   ← 0.4% — nhánh DUY NHẤT min_keep bảo vệ
```
> **`min_keep = 3` GẦN NHƯ VÔ HIỆU HOÀN TOÀN.**

### Ý định tác giả — **code làm NGƯỢC lại chính nó**

`_01_*.py:164-169`:
> *"Default 3 (**không phải 1**): một lần reranker chấm sai **KHÔNG được làm sập tập chunk còn lại xuống một**. Forensic step-level (2026-06-05, tra cứu điều khoản pháp lý)… với min_keep=1 cliff sẽ drop nó, nên **LLM không bao giờ thấy đáp án**."*

### Chain resolve — **DB THẮNG**
| key | LIVE DB | constant |
|---|---|---|
| `rerank_filter_strategy` | `"cliff"` | `"cliff"` ✔ |
| `rerank_cliff_absolute_floor` | **0.2** | 0.2 ✔ |
| `rerank_cliff_min_keep` | **3** | 3 ✔ |
| `rerank_cliff_gap_ratio` | **0.5** | **0.35** ✗ **DRIFT** |

Per-bot: 1 bot override `rerank_cliff_min_keep: 5`.
→ **Key CÓ trong DB ⇒ sửa `constants.py` LÀ CHẾT.**

### ⚠️⚠️ VÒNG FIX-REFIX — **CẤM TUNE LẠI SỐ**

```
0.15  (alembic 0068, 2026-05-08)
  ↓   gây REFUSE_GAP — CÓ LOAD-TEST chứng minh
0.05  (2026-05-11)  — evidence: reports/LOADTEST_90Q_RESULT_20260511_161747.json
  ↓
0.2   (c0c0dea, 2026-07-09)     ← HIỆN TẠI
```
🔴 **VÀ**: code SSoT giữ **0.05** suốt **62 NGÀY SAU KHI DB đã bác nó** (`cd08119` → `764f559`).
`test_cliff_floor_calibrated.py` **đang canh** window `[0.0, 0.20]`, cảnh báo floor > 0.20 sẽ **tái hiện regression REFUSE_GAP thời 0.15**. **0.2 nằm ĐÚNG TRÊN TRẦN.**

### FIX ĐÚNG — **đổi THỨ TỰ, KHÔNG đổi số**
Sau khi cắt floor, nếu `len(floor_kept) < min_keep` → **back-fill** từ `sorted_chunks`.
→ **Pattern ĐÃ CÓ SẴN** trong `mmr_filter` (`DEFAULT_MMR_MIN_KEEP`, ship ở 002-D). **TÁI DÙNG, KHÔNG PHÁT MINH.**

🛡️ **Comment bảo vệ** (`_01_*:213`): *"gap-cut của cliff làm rớt answer chunk (**đo được: một multi_hop trên corpus pháp lý → CHỈ 1 chunk sống sót**)"*

---

## 13 — `mmr_dedup` 🔴 **CHẠY Ở NGƯỠNG SAI — VÀ CONSTANT ĐÃ ĐÚNG RỒI**

> 🔴 **BẪY ĐẮT NHẤT TOÀN AUDIT.** Sửa `constants.py` = **0 TÁC DỤNG**.

```python
_14_*.py:235   DEFAULT_MMR_SIMILARITY_THRESHOLD = 0.98      ← ĐÃ FIX (9f93804, 07-04, CÓ ĐO)
_14_*.py:258   DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT = {"factoid": 0.88, ...}   ← VẪN 0.88, VÀ MAP NÀY THẮNG
mmr_dedup.py:35-48   map per-intent được hỏi TRƯỚC; global chỉ là nhánh else
```

**LIVE DB ghim NGƯỢC — bằng alembic CÓ CHỦ ĐÍCH, ĐÃ APPLY:**
```
system_config.mmr_similarity_threshold           = 0.88     ← DB THẮNG
system_config.mmr_similarity_threshold_by_intent = {"factoid":0.88, "comparison":0.95,
                                                     "multi_hop":0.95, "aggregation":0.98}
system_config.mmr_min_keep                       = (vắng) → constant 3 live ✔
```

`alembic/versions/20260709_seed_cliff_floor_mmr_parity.py` — **docstring nguyên văn**:
> *"`mmr_similarity_threshold` — constant là **0.98**, production DB là **0.88**. … việc nâng lên 0.98 là **một quyết định đo-lường RIÊNG** (MMR flip). Migration này **CHỈ ghim giá trị production hiện tại (0.88)** để clone mới khớp live."*

**Runtime — 741 row:**
| intent | before | after | threshold live | n |
|---|---|---|---|---|
| `factoid` | 4.77 | **3.19** | **0.880** | 604 |
| `comparison` | 9.30 | 6.64 | 0.950 | 44 |
| `aggregation` | 15.92 | 14.29 | 0.980 | 38 |

**factoid: −33%**

### Số 0.98 **ĐÃ ĐƯỢC ĐO** — commit `9f93804` body:
> *"**ĐO TRƯỚC** (theo plan): trên zembed-1, cosine giữa các section **KHÁC NHAU CÙNG 1 DOC** (p50 **0.975**, max **0.990**) **chồng gần hoàn toàn** lên dải near-duplicate — **KHÔNG ngưỡng nào tách được**; **0.88 cũ (calibrate thời TRƯỚC khi swap embedder) dedup NHẦM 100% cặp section phân biệt**, làm sập doc có section **6→1** và **bỏ đói generate → BỊA**."*

→ **Fix NỬA VỜI**: đo xong → sửa constant → **QUÊN flip DB** → **quên map per-intent**.

### 🔴 1 TEST ĐANG ĐỎ TẠI HEAD
```
test_per_intent_caps.py::test_default_constant_aggregation_loosens_threshold
E   AssertionError: aggregation must get a LOOSER MMR threshold than the default
E   assert 0.98 > 0.98
```
Vỡ do chính `9f93804` fix nửa vời.

### FIX ĐÚNG — **ALEMBIC MỚI**, phải update **CẢ HAI**
1. `system_config.mmr_similarity_threshold` : 0.88 → 0.98
2. **`mmr_similarity_threshold_by_intent.factoid` : 0.88 → 0.98** ← **thiếu cái này thì (1) VÔ NGHĨA** (map thắng ở `mmr_dedup.py:37`)

---

## 14 — `neighbor_expand` 💀 **OFF** — nhưng **"chưa wire" là SAI**

🔴 **Audit cũ SAI**: nó **CÓ trên edge VÔ ĐIỀU KIỆN** (`query_graph.py:3012-3013`), **chạy MỌI query** rồi early-return `{}`.
**0 row `request_steps` KHÔNG có nghĩa là chưa wire** — **step span nằm SAU enable-gate** (`neighbor_expand.py:478,495`).

```
DEFAULT_NEIGHBOR_EXPAND_ENABLED = False
system_config: KHÔNG CÓ ROW
bots WHERE plan_limits ~ 'neighbor' → 0 ROWS
```
→ **OFF khắp nơi, bằng default trong code, chưa ai từng opt-in.**
**Không tài liệu nào nói đã ĐO rồi loại** → **QUYẾT ĐỊNH YẾU**.

🧪 **THÍ NGHIỆM T1 TỐT NHẤT hiện có** — docstring: *"cửa sổ context rộng hơn cho LLM **KHÔNG cần** thêm embedding hay LLM call — chi phí là **1 SQL round-trip batched**"* → **+0 LLM CALL.**
Corpus đang `recursive` ~700-1400 char **với cắt giữa bảng** — **đúng điều kiện fragmented-context mà neighbor-expand sinh ra để vá.**
⚠️ **LÀM A1 TRƯỚC** — A1 đổi chunking thì phải chạy lại thí nghiệm.

---

## 15 — `grade` (CRAG) 💀💀 **97.7% BÌNH PHONG · FIX HÔM QUA THẤT BẠI**

### Runtime

```
grade_path        | count | avg_ms | tổng giây ĐỐT
skip_high_score   |  418  |     0  |     0.0        ← không gọi LLM
timeout_fallback  |  306  |  2115  |   647.2        ← GỌI LLM XONG VỨT KẾT QUẢ
batch (THÀNH CÔNG)|   17  |  1637  |    27.8        ← grade THẬT DUY NHẤT (2.3%)
                    741
```

### Cơ chế — timeout **ÉP CRAG PASS**

```python
grade.py:248-267
    except asyncio.TimeoutError:
        return {"graded_chunks": _fallback_graded,
                "retrieval_adequate": True,        # ← ÉP PASS
                "grade_timeout_fallback": True}
```
`_grade_route` (`routing.py:169`) chỉ đi `rewrite_retry` khi `retrieval_adequate = False`
→ **418 skip + 306 timeout ĐỀU BỎ QUA vòng correction THEO CẤU TRÚC.**

### 🚨 FIX HÔM QUA (`5c4fdda`) **THẤT BẠI — VÀ LÀM TỆ HƠN**

`5c4fdda` nâng `DEFAULT_GRADE_TIMEOUT_S` **2.0 → 3.0**, lý do ghi trong code: *"nằm ngay TRÊN p95 đo được (2.56s)"*.

**Cửa sổ cap=3.0 (14:39:56 → 15:02:38 ngày 13/07, trong lúc chạy load-test):**
```
timeout_fallback  : 30   avg 3015ms
batch (thành công): 0
```

**Latency các lần grade THÀNH CÔNG (17 lần):** `p50 1803ms · p95 1944ms · max 1996ms`

### 🔴 3 điều số liệu này nói

1. **Con số "p95 = 2.56s" em ghi trong commit KHÔNG KHỚP DỮ LIỆU.** `request_steps` nói p95 grade thành công = **1944ms**. **Em dẫn sai số vào chính commit message của mình.**
2. **`max thành công = 1996ms` — sát rạt trần 2000ms.** Đây là **dấu vân tay của MẪU BỊ CẮT CỤT (right-censored)**. **KHÔNG THỂ ước lượng p95 từ mẫu cắt tại p5.**
3. **Mọi timeout chạm ĐÚNG TRẦN** (2115 @2000, 3015 @3000).

### ✅ Root cause thật — **agent GỌI THẬT gateway, ĐO THẬT**

| population | min | p50 | max |
|---|---|---|---|
| concurrency 1 | **2799ms** | 3255ms | 4162ms |
| concurrency 8 | **3852ms** | 5319ms | 8347ms |

> 🔴 **CAP 2.0s NẰM DƯỚI CẢ GIÁ TRỊ NHỎ NHẤT (2799ms).**
> **100% timeout là TẤT YẾU SỐ HỌC.** Không phải "treo" (giả thuyết của em — **SAI**).
> 3.0s vẫn dưới p50 @conc-1 và dưới min @conc-8 → **cứu 0/30.**

**Và nguyên nhân sâu hơn**: cùng bug structured-output ở §3 — **gateway phớt lờ `response_format`** → cần **2 round-trip (~7s)**.
**`grade` có 0 repair-retry** — **vì `wait_for` HỦY trước khi call đầu kịp về.** → **CHÍNH CÁI TIMEOUT ĐÃ GIẤU BUG NÀY ĐI.**

### Gateway **REGRESS 2026-07-08**
```
ngày        thành công   timeout
2026-07-07      12          91
2026-07-08       0          29    ← ĐIỂM GÃY
2026-07-13       0          63
```

### Tách bạch nguồn gốc
- `skip_high_score` (418) — **QUYẾT ĐỊNH.** `crag_skip_retry_above_score = **0.55**` live (constant 0.7 — **DB HẠ XUỐNG**, cố ý skip **NHIỀU HƠN**). 🛡️ Comment `_10_rbac.py:157`: *"production-tuned từ **trace fa7983c2-…** — top_score=0.91 phí 10683ms cho retry"*
- `timeout_fallback` (306) — **TAI NẠN.** 41% timeout = **component HỎNG**

### Trạng thái hiện tại **TỆ HƠN CẢ HAI PHƯƠNG ÁN**
- Tệ hơn CRAG hoạt động: **cùng chi phí, 0 lợi ích**
- Tệ hơn không CRAG: **thuế latency thuần — 306 × ~2.1s = 647 giây ĐỐT SẠCH** + provider vẫn tính tiền token

### 2 phát hiện phụ
- `model_used = "openai/claude"` — **KHÔNG phải cấu hình sai.** `openai/` là **tiền tố transport của litellm** (→ wire `model: "claude"`); provider thật = `innocom` gateway. Gọi tay → **HTTP 200 + completion thật**. ⚠️ **NHƯNG `ai_models.supports_json_mode = true` cho model này là SAI SỰ THẬT**
- **Mọi grade row có `input_tokens=0, output_tokens=0, cost_usd=0`** — kể cả path thành công → **chi phí CRAG hiện VÔ HÌNH**

---

## 16 — `rewrite_retry` 💀 **CHẠY ĐÚNG 1 LẦN — VÀ LẦN ĐÓ CŨNG LÀ BÌNH PHONG**

Row **duy nhất** trong toàn bộ lịch sử, nguyên văn:
```json
2026-07-03 | 5ms | {"attempt":1, "triggered_by":"grade_low", "n_chunks_after":20,
  "original_query_preview":  "Mình có thể thanh toán bằng thẻ tín dụng được không?",
  "rewritten_query_preview": "Mình có thể thanh toán bằng thẻ tín dụng được không?"}
```
> **Query "đã viết lại" GIỐNG HỆT TỪNG BYTE query gốc, sinh ra trong 5ms** (quá nhanh để là 1 LLM call).
> 🔴 **CRAG CHƯA BAO GIỜ CHẠY THÔNG END-TO-END TRONG HỆ THỐNG NÀY.**

---

## 17 — `generate` 🔴

### 17.a — Câu RỖNG gắn nhãn `"answered"` 🔴
Ship `""` như thành công.

### 17.b — XML-wrap inject — **gate theo NGÀY SINH CỦA BOT** 🔴 **VI PHẠM SACRED #10**

```python
generate.py:630, 663-675, 710
    _xml_wrap = _resolve_xml_wrap_enabled(state)
    context_blocks.append(f'<chunk id="{cid}" type="{_ctype}" section="{_section}">\n<content>{text}</content>\n</chunk>')
    elif _trust_hint:
        context_blocks.append(f'<context source="..." trust="data_only" ...>\n{text}\n</context>')
    _user_content = f"<documents>\n{context_str}\n</documents>\n\n<question>{_q}</question>"
```

**(a) Gate theo NGÀY — ✅ CÓ** (`query_graph.py:587-601`):
> *"`bot_created_at >= XML_WRAP_DEFAULT_ON_FROM_DATE` — bots created on/after the cutoff **default to True when the key is absent**."*
`XML_WRAP_DEFAULT_ON_FROM_DATE = "2026-05-18"` (`_00_*:113`)

**🔴 LIVE — 4/6 BOT ĐANG BỊ XML-WRAP, KHÔNG AI BIẾT:**
```
bot     | created_at | qua cutoff | owner có set không
bot-1   | 2026-05-07 |     f      |        f
bot-2   | 2026-05-13 |     f      |        f
bot-3   | 2026-06-11 |     t      |        f     ← BẬT VÌ NGÀY SINH
bot-4/5/6| 2026-06-30|     t      |        f     ← BẬT VÌ NGÀY SINH
```
> **KHÔNG MỘT OWNER NÀO set `xml_wrap_enabled`.**
> 🔴 **HAI BOT GIỐNG HỆT NHAU, CHỈ KHÁC NGÀY TẠO → NHẬN PROMPT KHÁC NHAU.**

**(b) Owner có thấy không? — ❌ KHÔNG**
`GET /admin/bots/{id}/effective-prompt` (`admin_bots.py:192-207`) chỉ render **SYSTEM prompt** qua `SysPromptAssembler`. **XML wrap inject vào USER message** → **VÔ HÌNH**.
Docstring của chính endpoint (`:215-219`) nói nó tồn tại để thỏa *"ADR-W1-S10 điều kiện 1 — platform-rule append CHỈ được phép khi **owner soi được chính xác cái gì bị append**"*.
→ **XML wrap là sửa đổi prompt do platform viết ra, VÀ NÓ THOÁT KHỎI hợp đồng minh bạch đó.**

**(c) ADR? — ❌ KHÔNG.** `docs/adr/` có 0001-0008. Grep `xml_wrap` / `trust="data_only"` → **0 hit**.

**⚖️ Cân nhắc công bằng**: `<documents>`/`<question>` envelope (`:710`) là **VÔ ĐIỀU KIỆN**, có **TRƯỚC** cái flag, và **chính sysprompt template của platform tham chiếu nó** (`context_aware_refusal_template.py:80-97`) → **hợp đồng cấu trúc**, không phải rule lậu. **Token thật sự MANG RULE là `trust="data_only"`.**
**Nhưng gate-theo-ngày là THẤT BẠI QUẢN TRỊ bất kể phân loại thế nào.**

🔒 **KHÔNG THƯƠNG LƯỢNG**: **prompt của một con bot KHÔNG BAO GIỜ được phụ thuộc vào NGÀY SINH của nó.**

### 17.c — 🛡️ Comment bảo vệ
`generate.py:542` — *"default 2900 char quá chật (**verified 2026-05-21: turn '1tr499 có mấy dịch vụ' làm rớt 3/7 graded chunk**)"* ← **tái hiện cụ thể của bug K1 aggregation** mà CLAUDE.md trích dẫn làm **cái giá của psql hot-fix**.

---

## 18 — `critique_parse` (Self-RAG) 💀 **OFF ĐÚNG**

🔴 "chưa wire" **SAI** — nó **CÓ trên edge vô điều kiện** (`query_graph.py:3026-3027`), chạy mọi query rồi `return {}`. Step span **nằm SAU gate** → 0 row `request_steps` **không có nghĩa là chưa wire**.

```
DEFAULT_SELF_RAG_ENABLED = False · system_config: không có row · 0 bot override
```

🚫 **KHÔNG BẬT ĐƯỢC BẰNG FLAG.** Docstring (`critique_parser.py:1-21`): nó **chỉ hoạt động nếu bot owner TỰ THÊM rule `[Supported]`/`[Unsupported]` vào `bots.system_prompt`**. Bật flag mà không có rule → LLM không phát token → parse 0 marker → **fail open**.
🔒 **Sacred #10 CẤM application tự inject rule đó.**
→ **Giữ code (feature opt-in hợp lệ). ĐỪNG hồi sinh từ phía platform.**

---

## 19 — `guard_output` 🟡

| | |
|---|---|
| **Grounding** | chỉ chạy trên **20%** câu trả lời |
| 🔴 **`grounding_confirmed_action` fail-closed — REFUTED** | Default là **`observe`**, KHÔNG phải block. `system_config`: **không có key**. 1 bot duy nhất set nó = **`observe`**. **KHÔNG bot nào block** |
| ✅ **Commit bị em vu oan là MẪU MỰC** | `c0c0dea` comment `:320-324`: *"Default 'observe' để **không bot nào bị đổi refuse-rate mà không opt-in tường minh**; owner chỉ flip 'block' per-bot **SAU KHI ĐO** rằng độ lệch false-positive của ngưỡng grounding không over-refuse những câu thật sự grounded."* ← **Đây chính xác là kỷ luật CLAUDE.md yêu cầu** |
| **`grounding_failure_mode` = `fail_closed`** | ✅ **HỢP PHÁP** — chỉ bắn khi grounding judge **KHÔNG CHẠY ĐƯỢC**, và thay bằng **`oos_answer_template` CỦA CHÍNH BOT** → **đúng Application-MINDSET rule #3** |
| ⚠️ **Phát hiện THẬT** | **8 commit guard / 7 ngày, 0 ADR** — bề mặt app-override **nới rộng từng bot một**, *"owner-approved"* **chỉ nằm trong commit message** (`f22a808`) |
| 🛡️ **Comment bảo vệ** | `:265` — *"truth-audit step20: `<brand>` bị deny trong khi **50+ SKU tồn tại**… Default observe = chỉ log, **ĐO tỉ lệ từ-chối-sai TRƯỚC KHI bất kỳ bot nào opt vào block**"* |

---

## 20 — `reflect` 💀 **TẮT BẰNG SỐ ĐO — TUYỆT ĐỐI KHÔNG BẬT**

`routing.py:197-216` — **nguyên văn**:
> *"Reflect-gate (added 2026-05-18): bot owners opt in via `plan_limits.reflection_enabled`. Default là False… **Production audit (req 9cf611b5) found reflect firing 2× per turn (3.57s wasted) on bots that never enabled it.** Gating here saves the round-trip."*

```
DEFAULT_REFLECTION_ENABLED = False · PLAN_LIMIT_SCHEMA default False · 0 bot opt-in
```

🚫 **HỒI SINH = TÁI TẠO REGRESSION ĐÃ ĐO 3.57s/turn.**
🛡️ **GIỮ NGUYÊN COMMENT `routing.py:201-206`** — nó là **TRÍ NHỚ THỂ CHẾ** sống sót qua đợt re-init repo **CHỈ NHỜ NẰM TRONG COMMENT**. Audit tương lai nào gắn cờ "reflect chết" **phải bị đập lại bằng chính nó**.

🔴 **BẪY KÈM THEO**: `reflect_skip_if_grounded=true` và `reflect_skip_top_score_floor` là **2 OVERRIDE PER-BOT TRƠ** — chúng cấu hình một node **KHÔNG BAO GIỜ được vào**. **Một cài đặt KHÁCH HÀNG NHÌN THẤY, mà KHÔNG LÀM GÌ.**

---

## 21 — `persist` ✅ **EXPERT**

- **Refuse KHÔNG BAO GIỜ được cache**
- **Số được cache với NULL embedding** (chống stale-number — rất tinh tế)

---
---

# PHẦN III — LUỒNG XUYÊN SUỐT (cross-cutting)

## III.1 — LUỒNG RESOLVE CONFIG 🔴 **4 READER, 2 GATE ĐỘC LẬP, KHÔNG AI RAISE**

```
constants.py  →  PLAN_LIMIT_SCHEMA  →  system_config (DB)  →  bots.plan_limits
                                            ▲
                                    DB THẮNG nếu key tồn tại
```

**Thứ tự thực tế (đo từng key):**
`bots.threshold_overrides` > `bots.<column>` > `bots.plan_limits` > **`system_config`** > `PLAN_LIMIT_SCHEMA["default"]` / `DEFAULT_*`

| # | Reader | Cache | Gate | Key thiếu → |
|---|---|---|---|---|
| 1 | `SystemConfigService.get*` (`system_config_service.py:72-189`) | **Redis** `ragbot:sysconfig:{key}`, TTL **300s** ±10% jitter | **không** | `return default` |
| 2 | `get_boot_config()` (`bootstrap_config.py:297-360`) | **in-process dict**, TTL **30s**, sync psycopg2 | **`_ALLOWED_KEYS` — 133 key** | không allow-list → **DB row BỊ PHỚT LỜ** |
| 3 | `_pcfg(state, key, default)` (`query_graph_helpers.py:164-179`) | `state["pipeline_config"]`, bulk-load 1 lần | **`_PIPELINE_CFG_KEYS` — 87 key** + **1 tuple mirror 173-key** ở `chat_worker/config.py:190-204` **phải sync TAY** | không trong tuple → **không bao giờ load** → `default` |
| 4 | `resolve_bot_limit()` (`bot_limits.py:397-470`) | — | — | **KHÔNG PHẢI reader `system_config`** — caller phải tự đọc DB rồi truyền vào |

### 🔴 GỐC RỄ CỦA DRIFT IM LẶNG

> **KHÔNG TẦNG NÀO RAISE KHI THIẾU KEY.** Cả 4 reader đều `return default`.
> **Một row bị xóa · một key chưa seed · một key bị typo khỏi allow-list — CẢ BA ĐỀU KHÔNG PHÂN BIỆT ĐƯỢC ở runtime với "operator đã chọn dùng constant".**
> **Đó CHÍNH XÁC là vì sao 0.88-vs-0.98 sống sót 9 ngày.**

### Redis invalidation ✅ **CÓ VÀ CHẠY**
`set()` → Redis `DEL` → outbox `system_config.changed.v1` → publisher → Redis Streams → `ai_config_listener.py:66` → `invalidate_local_cache()` → `DEL` trên mọi replica.

⚠️ **Khoảng trống**: `ai_config_listener.py:16` **chỉ** import Redis invalidator. **Không bao giờ gọi `bootstrap_config.invalidate_cache()`** → cache in-process 30s (reader #2 — phục vụ `embedding_provider`, `reranker_provider`, `cascade_*`) **chỉ được invalidate ở process đã phục vụ lệnh ghi admin**. Replica khác **phục vụ giá trị cũ tới 30s**. Bounded, tự lành → **severity thấp, nhưng có thật**.

### LEDGER
| Nhóm | n | Nghĩa |
|---|---|---|
| **L1 DRIFT** (có cả 2, **giá trị KHÁC**) | **24** | **DB thắng 24/24.** Chỉ **2/24** có document |
| **L2 SHADOWED** (giá trị trùng) | **54** | ➡️ **Constant CHẾT.** Sửa = vô tác dụng |
| **L3 DB-ONLY** | **187** | **72 key KHÔNG code nào đọc** = **row rác** |
| **L4 CONSTANT-ONLY** | **87** | ➡️ **Constant DUY NHẤT còn chịu tải** |

**→ 78/171 constant đã CHẾT ở runtime.**

### 🔴 KHÔNG CÓ GUARD NÀO CANH GIÁ TRỊ
```bash
grep -rn "== DEFAULT_" tests/ scripts/    →    0 hit
```
| Guard hiện có | Thật sự kiểm gì |
|---|---|
| `check_config_completeness.py` | **chỉ KEY CÓ MẶT** |
| `audit_config_key_drift.py` | đúng **2 cặp tên key** hardcode |
| `test_seed_paths_agree.py` | ghim vào migration trong **`_archive_pre_squash`** — **KHÔNG trong chain active**. **Test XANH trong khi canh thứ KHÔNG BAO GIỜ CHẠY** = **NIỀM TIN GIẢ** |

---

## III.2 — LUỒNG SEED / DEPLOY 🔴🔴 **DB KHÔNG TÁI TẠO ĐƯỢC**

### Thí nghiệm thật (đã chạy, đã xóa DB tạm)
```bash
CREATE DATABASE ragbot_seedcheck_tmp;
ALEMBIC_SQLALCHEMY_URL=<tmp> alembic upgrade head       # 40 revision chạy đủ
SELECT count(*) FROM system_config;
```
```
FRESH DB : 5 row      PROD : 264 row      THIẾU : 259
```
**5 key sống sót**: `adaptive_context_enabled` · `mmr_similarity_threshold` · `pipeline_multi_query_speculative_enabled` · `rerank_cliff_absolute_floor` · `vlm_caption_prompt`

### Cơ chế
```bash
grep -cE "^(COPY|INSERT)" alembic/squashed_baseline.sql   →   0
grep -cE "^CREATE TABLE"  alembic/squashed_baseline.sql   →  44
```
**`squashed_baseline.sql` = `pg_dump --schema-only` — 0 ROW DỮ LIỆU.**
**9/12 migration active dùng `UPDATE … WHERE key='…'` → match 0 row trên bảng rỗng → NO-OP IM LẶNG.**

| | archive (279 file) | chain ACTIVE |
|---|---|---|
| migration có seed INSERT | **93** | 7 |
| `INSERT INTO system_config` | **91** | 3 |
| key được seed | **85** | **15** |
| **key MẤT khi squash** | | **75** |

### Hệ quả — **DB fresh chạy STACK KHÁC HẲN**

| | PROD | FRESH (rơi về constant) |
|---|---|---|
| `embedding_provider` | `zeroentropy` | **`jina`** |
| `embedding_dimension` | **1280** | **1024** |
| `reranker_provider` / `_model` | `zeroentropy` / `zerank-2` | **`jina`** / **`jina-reranker-v3`** |
| **cột DB** | `vector(1280)` | ← **INSERT 1024-dim = HARD FAIL** |
| `chunking_policy` | `table_dual_index` | **`table_csv`** (silent, chunk sai) |
| `guardrail_rules` | **13 rule** | **~1 rule** (chỉ `prompt_injection_vi` được seed active) |
| mmr / cliff_gap / rerank_top_n / grounding | 0.88 / 0.5 / 10 / 0.5 | **0.98 / 0.35 / 7 / 0.3** |

> 🔴 **DEV/CI KHÔNG THỂ TÁI HIỆN PROD. Mọi phép đo A/B trước khi vá cái này đều VÔ NGHĨA.**

### 🔴 VI PHẠM SACRED RULE #7 — **QUY MÔ 98%**
CLAUDE.md: *"Mọi thay đổi DB content state CHỈ qua alembic tracked HOẶC admin UI có audit_log."*
→ **259/264 key (98%) KHÔNG nằm trong alembic.**

### Đã được BIẾT lúc ship
`STATE_SNAPSHOT.md:675-681`, dưới heading **"🔴 RISKS / CAVEATS"**: *"**#2. Squash là SCHEMA-ONLY** — `squashed_baseline.sql` **không có DATA**."* → **ship anyway**.

### Landmine: **THỨ TỰ DEPLOY QUYẾT ĐỊNH MODEL STACK**
`scripts/init_system_config.py` seed **158 key** (`ON CONFLICT DO NOTHING`) — **KHÔNG có trong `README_DEVOPS.md`** (chỉ ghi `alembic upgrade head`).
- alembic → script : `llm_default_model = gpt-4.1-mini`
- script → alembic : `openai/claude`
**KHÔNG GÌ CƯỠNG CHẾ THỨ TỰ NÀO.**

### 2 defect **ĐÓNG BĂNG VÀO BASELINE SQL**
`document_service_index` **thiếu `missing_ok=true`** (`:1477`) và **thiếu `FORCE ROW LEVEL SECURITY`** (`:1433`) — **duy nhất trong 24 policy** → **khi bật RLS, session không bind sẽ RAISE thay vì fail-closed.**

---

## III.3 — LUỒNG GỌI LLM 🔴 **2,271 CALL/NGÀY BỎ QUA ROUTER**

```
generate  ──▶  _complete_runtime_one  ──▶  [semaphore 6] [circuit breaker] [retry_with_backoff]  ──▶  gateway   ✅

understand_query ─┐
grade            ─┼──▶  structured_output_helper.py:437                                          ──▶  gateway   ❌
rerank           ─┘        return await litellm_module.acompletion(**call_kwargs)
                           ↑ GỌI THẲNG — KHÔNG QUA GÌ CẢ
```

`query_graph.py:1352` lấy `llm._litellm_module` → `:1424` gọi `call_with_schema` → `litellm.acompletion` **trực tiếp**.

### 3 lớp bảo vệ vừa ship **CHỈ bảo vệ `generate`**

| Đã ship tuần này | Áp cho `understand`/`grade`? |
|---|---|
| Semaphore `max_concurrent = 6` (`09546f8`) | ❌ **KHÔNG** |
| Rate-based circuit breaker (`3006171`) | ❌ **KHÔNG** |
| Retry 3-tầng budget (`213b3d2`/`91163d5`/`8251944`) | ❌ **KHÔNG** |
| `num_retries=0 / max_retries=0` (B7#1) | ✅ CÓ (trong `_safe_acompletion`) |

→ **`understand` + `grade` NÃ GATEWAY KHÔNG GIỚI HẠN** → nhiều khả năng **CHÍNH CHÚNG gây ra** 94 `InternalServerError` và p50 gateway 3.3s → 5.8s dưới tải. **Chúng tự gây sự cố cho chính mình.**

### 💸 3,012 step LLM có `cost_usd = 0`
Gateway trả **`usage: None`**. `_emit_usage_sink` (`structured_output_helper.py:306-316`) **chỉ gọi `extract_usage_from_response`**, **không gọi `estimate_tokens_fallback`** (đường router **có gọi**).
→ **Dashboard chi phí BÁO THIẾU 2,271 lời gọi.**

🛡️ **Comment bảo vệ** (`structured_output_helper.py:428`): *"litellm mặc định AsyncOpenAI `max_retries=2`, **stack dưới retry loop của caller** — **244 dòng 'Retrying request' không phối hợp trong load-test**"*

---

## III.4 — LUỒNG STRUCTURED OUTPUT 🔴 **2 ROUND-TRIP CHO MỌI REQUEST**

```
Call 1:  response_format={"type":"json_object"}   +  KHÔNG có schema trong prompt
         → gateway trả VĂN XUÔI: "yes\n\nNội dung trực tiếp trả lời câu hỏi..."
         → validate fail → _fallback_json_parse: không thấy '{' → None
         ↓
Call 2:  REPAIR RETRY — _build_repair_messages (structured_output_helper.py:192-211)
         ĐƯA SCHEMA VÀO PROMPT
         → 3/3 JSON HỢP LỆ: {"grades":[{"chunk_id":…,"grade":"partial"},…]}   3.1-4.0s   ✔
```

**Thử cả strict `json_schema` → CŨNG VĂN XUÔI.** **`response_format` là NO-OP trên gateway này.**
→ **`ai_models.supports_json_mode = true` cho `openai/claude` là SAI SỰ THẬT.**

### Chi phí thật
```
understand_query:  1,530 call · avg 10,314ms · 112 repair-retry + 122 validation-failed/ngày
grade:               741 call · 0 repair-retry ← vì wait_for HỦY trước khi call đầu về
                                                  → TIMEOUT ĐÃ GIẤU BUG NÀY ĐI
```

### ⭐ FIX ĐÚNG TẦNG
**Đưa schema vào prompt NGAY CALL ĐẦU** cho provider không cưỡng chế `response_format`.
**`_build_repair_messages` ĐÃ LÀM ĐÚNG THẾ NÀY RỒI** ở vòng 2 — chỉ là **làm sớm hơn 1 vòng**.
→ Cắt **~1,530 round-trip/ngày**. `understand` **10.3s → ~3.5s**. Grade chạy trong **1 call ~3.3s**.
🔒 **Sacred #10 AN TOÀN** — prompt **nội bộ pipeline**, không phải prompt answer của bot owner.

> 🚫 **NÂNG `DEFAULT_GRADE_TIMEOUT_S` = FIX SAI TẦNG.** Kể cả 5s cũng chỉ mua 1 node tốn 2 round-trip (~7s) để **KHÔNG SINH RA GÌ**.

---

## III.5 — LUỒNG GUARDRAIL 🟡

| | |
|---|---|
| PROD | **13 rule**: `pii_en_ssn` · `pii_vi_cmnd` · `pii_vi_email` · `pii_vi_phone` · `prompt_injection` · 5× `prompt_injection_legacy_*` · `prompt_injection_vi` · `secret_leak` · `sql_injection` |
| **Chain alembic ACTIVE** | 🔴 **CHỈ seed `prompt_injection_vi`** (`20260710_seed_prompt_injection_vi.py`) |
| 12 rule còn lại | đến từ **archive** `20260516_010f_guardrail_rules_table.py` — **KHÔNG trong chain** |
| → **DB fresh** | **~1 rule thay vì 13** |
| 🔴 **Hệ quả** | **PII redaction (fix hôm qua) KHÔNG CÓ RULE ĐỂ CHẠY trên DB fresh** |
| **Cache bypass** | Cache hit **bỏ qua `guard_output`** + cache key **không tính guardrail** → **cửa sổ 1 tiếng** (§II.2) |
| 🛡️ Comment bảo vệ | `_06_llm_defaults.py:140` — *"**refusal sentence ≈ 5 match, instruction block ≈ 13-89, verbatim dump 300 từ ≈ 277.** Floor 10 lọt refusal 1-câu mà vẫn bắt bulk extraction"* |

---

## III.6 — LUỒNG RBAC 🔴 **13 ROUTE GHI/XÓA KHÔNG CÓ GATE**

```
document_routes      require_min_level = 0    route ghi/xóa = 3
chat_routes          require_min_level = 0    route ghi/xóa = 3
admin_routes         require_min_level = 0    route ghi/xóa = 3
bot_admin_routes     require_min_level = 0    route ghi/xóa = 5
monitoring_routes    require_min_level = 0    route ghi/xóa = 2
bot_insights_routes  require_min_level = 2    route ghi/xóa = 0    ← file DUY NHẤT có gate
```

```
PUT    /admin/config/{key}                        ← ĐỔI BẤT KỲ system_config NÀO
PUT    /admin/api-keys/{provider_code}            ← GHI API KEY
DELETE /admin/api-keys/{provider_code}/{label}    ← XÓA API KEY
POST   /bots · PATCH /bots/{id} · DELETE /bots/{id}
PUT    /bots/{id}/{ch}/max-history · PATCH /bots/{id}/vocabulary
POST   /bots/{id}/{ch}/documents · POST .../documents/upload
DELETE /documents/{doc_uuid}
POST   /reinit-bots · POST /validate-link
```

**🔴 MỈA MAI**: `PUT /admin/config/{key}` chính là con đường mà **sacred rule #7 gọi là "admin UI có audit_log"** — **nhưng nó KHÔNG có RBAC.**

⚠️ **Giảm nhẹ (KHÔNG phải xóa bỏ)**: CLAUDE.md quy định `test_chat` **KHÔNG expose external**, chặn ở **gateway/network**.
→ **Bảo vệ hiện tại = TẦNG MẠNG, KHÔNG PHẢI TẦNG APP. Ai vào được LAN là vào được hết.**

🔥 **FIX ĐÃ TỒN TẠI — MẮC KẸT**: `cc9880c` (nhánh `worktree-agent-a98b47eb8ed705bb5`) = **RBAC cho đúng mấy route này + `test_rbac_test_chat_destructive.py` (229 dòng)** → **CHƯA MERGE**.

---

## III.7 — 🔴🔴 PATTERN LỚN NHẤT: **TEST-HARNESS ≠ PRODUCTION**

**3 chỗ đã tìm thấy — KHÔNG PHẢI TRÙNG HỢP:**

| # | Key/tính năng | test_chat | worker PROD |
|---|---|---|---|
| **1** | **`raw_bytes`** (parser registry) | ✅ **TRUYỀN** (`document_routes.py:521`) | ❌ **KHÔNG** → **U2 CHẾT, row-as-chunk bất khả đạt** |
| **2** | `heuristic_intent_enabled` | ✅ có (`_pipeline_config.py:855`) | ❌ **VẮNG** → **override per-bot BỊ PROD BỎ QUA IM LẶNG** |
| **3** | `guard_output_parallel_enabled` | ✅ có (`:866`) | ❌ **VẮNG** → **như trên** |

```bash
grep -c heuristic_intent_enabled       workers/chat_worker/pipeline_config.py  → 0
grep -c heuristic_intent_enabled       test_chat/_pipeline_config.py           → 2
grep -c guard_output_parallel_enabled  workers/chat_worker/pipeline_config.py  → 0
grep -c guard_output_parallel_enabled  test_chat/_pipeline_config.py           → 2
```

> 🔴 **ĐÂY LÀ LÝ DO BUG TRỐN ĐƯỢC LÂU: DEV TEST THẤY ĐÚNG, KHÁCH HÀNG NHẬN ĐƯỜNG KHÁC.**
> **Mọi bug "trốn được lâu" từ giờ PHẢI hỏi: route test có đi CÙNG ĐƯỜNG với prod không?**

**Fix**: thêm 2 key vào worker + **1 test ghim: 2 whitelist PHẢI KHỚP NHAU**.

---

## III.8 — ☠️ ~30 FLAG TRƠ (set nhưng code không đọc)

### Gốc rễ: `feature_flag=` **KHÔNG PHẢI GATE**
```python
shared/intrinsic_metrics.py:315   feature_flag: str = "ekimetrics_5metric_selector_enabled",
shared/intrinsic_metrics.py:335   @param feature_flag: flag name to emit in the structlog event
```
→ Mọi `feature_flag="x"` **phát log nói rằng flag `x` chi phối bước này** — **trong khi `x` có thể KHÔNG ĐƯỢC ĐỌC Ở ĐÂU CẢ**. **Flag ma.**

| flag | LIVE | vì sao không làm gì |
|---|---|---|
| 🔴 **`circuit_breaker_enabled`** | `true` | **KILLSWITCH GIẢ.** Chỉ có trong **docstring**. `FailoverOrchestrator(` **KHÔNG BAO GIỜ khởi tạo**. **Với tay lấy nó lúc sự cố = KHÔNG CÓ GÌ XẢY RA** |
| 🔴 **`embedding_text_strategy = "auto"`** | `"auto"` | **"auto" KHÔNG có trong registry** {`prefix_plus_raw`,`raw_only`,`field_selective`,`null`} → `_REGISTRY.get(key)` → `None` → **LUÔN rơi về `NullEmbeddingTextStrategy`**. **Toàn bộ registry BẤT KHẢ TIẾP CẬN** |
| `table_csv_emit_header_footer_chunks_enabled` | `true` | reader trong `elif strategy == "table_csv"`; live là `table_dual_index` |
| `adapchunk_layer5_cross_check_enabled` | `true` | `apply_cross_check` gọi **vô điều kiện** |
| `tenant_rate_limit_enabled` · `docs_only_strict_enabled` · `understand_query_cache_enabled` · `cache_stampede_singleflight_enabled` · `robust_json_parser_enabled` · `callback_ssrf_guard_enabled` · `parser_heading_detection` · `parser_table_detection` · `token_quota_notify_enabled` · `bm25_symbol_phrase_enabled` | | **0 reader** hoặc **reader không bao giờ chạy** |

### Flag **SAI TÊN KEY**
| constant | key code THẬT SỰ đọc |
|---|---|
| `cr_prompt_cache_enabled` | `contextual_retrieval_prompt_cache_enabled` |
| `enriched_prefix_persist` | `enriched_prefix_persist_in_content` |
| `self_rag_enabled` | `self_rag_critique_enabled` |
| `rerank_intent_whitelist_enabled` | DTO lồng `rerank_intent_whitelist.enabled` |
| `diff_reingest_enabled` | `diff_based_reingest_enabled` (chỉ log `not_implemented`) |

### ⛔ 3 CÁI BẪY — trông xóa được nhưng KHÔNG
1. **`decomposer_enabled`** — key live là **DOTTED**: `decomposer.enabled` (live `true`). **Quét regex = GIẾT FLAG ĐANG SỐNG**
2. **22 flag Class-B2** — node **CÓ trên LangGraph**, gate nằm **TRƯỚC** span → **trông chết trong `request_steps` dù wiring ĐÚNG**
3. **`grounding_*` / `*_fidelity_action` / `degeneration_action`** — guard **HALLU=0**. Class C+D, **không phải nợ**

---

## III.9 — 🗑️ BỀ MẶT CODE CHẾT (đã kiểm chứng sẵn)
```
66 file có "DEAD-CODE NOTICE — 2026-06-03"   ·   6,477 dòng
12 registry comment 100%
```
⚠️ **GIỮ `application/services/hyde_generator.py`** — HyDE **THẬT** (`bootstrap.py:598`). Chỉ `infrastructure/hyde/*` là bản trùng chết.

---
---

# PHẦN IV — BẢNG ĐIỂM

## Upload (13 stage)
| | n |
|---|---|
| ✅ EXPERT | 6 |
| 🟡 YẾU | 3 |
| 🔴 SAI/HỎNG | 3 |
| 💀 CHẾT | 1 |

## Query (21 node)
| | n |
|---|---|
| ✅ EXPERT | 5 |
| 🟡 YẾU | 3 |
| 🔴 SAI/HỎNG | 7 |
| 💀 CHẾT | 6 |

## Xuyên suốt (9 luồng)
| luồng | trạng thái |
|---|---|
| Config resolve | 🔴 4 reader · 2 gate · **không ai raise** · **24 drift** · **0 guard** |
| Seed/deploy | 🔴🔴 **5/264** — DB không tái tạo được |
| LLM call routing | 🔴 **2,271 call bypass router** |
| Structured output | 🔴 **2 round-trip mọi request** |
| Guardrail | 🟡 13 rule prod / **~1 rule fresh DB** |
| RBAC | 🔴 **13 route ghi/xóa TRẦN** |
| **Test ≠ Prod** | 🔴🔴 **3 chỗ lệch — PATTERN** |
| Flag | ☠️ **~30 TRƠ**, 1 **killswitch GIẢ** |
| Code chết | 🗑️ **6,477 dòng** đã kiểm chứng |

---

# PHẦN V — 12 CÁI SAI CỦA CHÍNH AUDIT TRƯỚC (tự kiểm)

| Cáo buộc | Sự thật |
|---|---|
| *"factoid trong skip-list → 57.7% bỏ rerank"* | 🔴 **0/741 = 0.0%.** Đọc hằng số rồi suy ra runtime — **vi phạm rule#0** |
| *"VN tokenizer: index segmented, query không"* | 🔴 **NGƯỢC 180°.** Index **KHÔNG** segmented. Fix của em **sẽ GIẢM recall**. Bug thật ở **ingest** |
| *"IdempotencyService 0 caller"* | 🔴 **6 caller thật.** Grep sai tên thuộc tính (`_idempotency` vs `_idem`) |
| *"grounding fail-closed vi phạm sacred"* | 🔴 **Default là `observe`.** **Vu oan code TỐT** |
| *"test_crossdoc_reconcile:68 ghim bug"* | 🔴 Test **ĐÚNG** — ghim chống brand-conflation |
| *"HNSW: opclass/cột/filter sai"* | 🔴 **Cả 3 SAI.** Là **cost model**. Bỏ filter → planner **vẫn** Seq Scan |
| *"grade timeout: grader TREO"* | 🔴 **Không treo.** **Cap nằm DƯỚI min latency (2799ms)** |
| *"recursive unreachable"* | 🔴 Nó **vẫn thắng** bằng max-score |
| *"default = hybrid→proposition"* | 🔴 **`proposition` KHÔNG LIVE.** Rơi vào `hybrid` rồi dừng |
| *"coverage gate mù do proposition"* | 🔴 Thủ phạm là **`_chunk_hdt`** (217 chunk live) |
| *"cột superseded_by bị gỡ = mất tính năng"* | 🔴 **Giàn giáo CHƯA TỪNG có logic.** Không migration ADD nào tồn tại |
| *"null_embedder = mất Null Object"* | 🔴 Registry **đã degrade an toàn**. Bản commented **RAISE** → **vi phạm hợp đồng Null-Object** |

## 4 BÀI HỌC PHƯƠNG PHÁP
1. 🚫 **CẤM đọc hằng số rồi suy ra runtime** — số hành vi PHẢI từ `request_steps`/log
2. 🚫 **CẤM grep theo tên thuộc tính đoán mò** — grep theo **METHOD/SYMBOL**, và **xử lý dotted key**
3. 🚫 **"0 step runtime" ≠ "chưa wire"** — kiểm span **TRƯỚC hay SAU** gate
4. 🚫 **CẤM tính p95 trên mẫu bị cắt cụt** (survivorship bias) — muốn biết latency thật: **đo KHÔNG timeout**
