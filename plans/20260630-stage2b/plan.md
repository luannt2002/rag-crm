# [T1-Smartness] Table/Aggregate Quality — Stage-2b + Aggregate-wiring + Reconcile

> Scope: the **non-price table flow** (price keystone already closed by B-FMA `07a4e94`).
> Discipline: shape-based (THE ONE LAW), multi-bot/multi-doc/multi-tenant, no per-bot/vocab
> hardcode. Each phase gated by an A/B load-test on 3 demo bots (xe + spa + legal).
> Status: PLAN — no code until approved.

---

## Bug inventory (tất cả là bug CŨ — đã phát hiện trong audit phiên 2026-06-30, evidence-backed)

| ID | Triệu chứng (live) | Gốc rễ (layer) | NEW/OLD |
|----|---|---|---|
| **B-AGG** | "có bao nhiêu loại Landspider" → bịa `1.020.000` (0× corpus), KHÔNG ra số đếm | `summary_json` **write-only orphan** (tính lúc ingest, 0 read site ở answer); count route đi per-entity (`list_all_entities`/`query_by_name_keyword`) → trả product rows; `count_by_price_range` **dead (0 caller)** | OLD (orphan + B-COUNT + F7-revert) |
| **B-ROLE** | "giá 155/80R13" → SL **26**; "155/80R13 còn bao nhiêu" → **214** (cùng 1 row, mâu thuẫn) | quantity/date KHÔNG phải role (`_roles_def` chỉ name/category/aliases/price) → land `attributes_json` dạng `col_N` **không nhãn** → render `col_4:214 | col_6:26` → LLM đoán cột | OLD (Stage-2b core) |
| **B-FRAG** | "Davanti 215/60R17 còn mấy" → **26** (truth 98) | 1 SP vật lý = **2 row cross-doc** (doc giá: col_4=98; doc khác: col_4=26 NULL-price); `_dedup_stats_entities` chỉ per-doc | OLD (audit A2/A3) |
| **B-FORMAT** | DOCX/PDF/HTML table → `col_N`, no row-split; XLSX cả sheet = 1 chunk | non-Sheets parser **bypass** `rows_to_structured_markdown` (docx hardcode rows[0]-header) | OLD (audit A1 B7/B8) |
| **B-CODETOK / B-TRUNC** | "155 80 13" → token sai; list "liệt kê tất cả" cap 100 < 257 | `_CODE_QUERY_RE` space-split; `DEFAULT_STATS_INDEX_LIMIT=100` | OLD (audit A2) |
| ~~B-FMA price~~ | ~~by-spec giá bịa~~ | ✅ **ĐÃ FIX** `07a4e94` (attributes search) — **đừng làm lại** | DONE |

---

## PLAN — 5 phase + verify-gate (mỗi phase 1 lớp vấn đề)

### Phase 1 — **Aggregate/summary-driven list-count** (giải **B-AGG** + **B-SERIES**) ⭐ ưu tiên #1 (user-insight)
- **Vấn đề giải**: list / đếm / tổng-hợp phải đọc tầng TÓM TẮT, không lôi per-product → hết bịa giá + ra đúng số đếm.
- **B-SERIES (evidence 2026-06-30, live DB)**: "có bao nhiêu loại Landspider" → đáp án ĐÚNG = **5 dòng/series** (NotebookLM xác nhận), KHÔNG phải 117 row spec-size, càng KHÔNG phải `1.020.000` bịa. Group-by trên `document_service_index` (record_bot_id=chinh-sach-xe): CITYTRAXX H/P=50, CITYTRAXX G/P=30, CITYTRAXX H/T=24, WILDTRAXX A/T=10, ROVERTRAXX X/T=1, +2 no-series = 117 row. → "loại" = **group-by series-token (shape)**, KHÔNG `COUNT(*)` thô.
- **Việc**:
  1. Dispatch `operation="count"` → `count_by_price_range` / aggregate (cardinality), KHÔNG `else`-rows.
  2. **Wire `summary_json`** vào answer cho list/count/aggregate (hiện write-only) — đọc entity_count/buckets/categories.
  3. Thêm **bot-level aggregate-by-attribute** (count theo brand/category/series, gộp cross-doc) — shape-based: gom variant→series theo token chung xuất hiện trong attributes_json, KHÔNG hardcode "Landspider"/"CITYTRAXX".
  4. **Golden-guard** `chinh-sach-xe`: "loại Landspider"=5, COUNT khớp corpus, số bịa 0× (HALLU=0) → khóa chống tái phát (giải luôn C5).
- **Files**: `query_graph.py` (`_do_stats_lookup` dispatch), `stats_index_repository.py` (count/aggregate query), `document_service` (summary read path).
- **Shape-based**: count theo attribute-generic (mọi cột/brand), không vocab/per-bot. **Alembic**: optional (bot-level aggregate view). **Re-ingest**: KHÔNG (summary đã có). **Tier T1**.

### Phase 2 — **Column-role quantity/date** (giải **B-ROLE** = Stage-2b gốc)
- **Vấn đề giải**: SL/ngày đọc nhất quán (hết 26-vs-214) — bằng cách gán role + render có nhãn.
- **Việc**: thêm role `quantity` + `date` vào `_roles_def` (Tier-1 shape: số gần price = qty-candidate, value `DD-thg-MM` = date; Tier-2 owner `custom_vocabulary` HINT — ADR-0006); persist role; render `quantity: 214` thay `col_4: 214`.
- **Files**: `document_stats.py` (`_column_roles`/`_roles_def`), `stats_index_repository` (persist role sentinel), `query_graph.py` (render label).
- **Shape-based**: role suy luận theo FORM + owner HINT, KHÔNG hardcode "Số lượng"/"Ngày về". **Alembic**: optional (role-sentinel trong attributes_json = no schema; hoặc typed column = alembic). **Re-ingest**: CÓ (forward-effective). **Tier T1**.

### Phase 3 — **Entity reconcile / fragmentation** (giải **B-FRAG**)
- **Vấn đề giải**: 1 SP = nhiều row cross-doc → chọn đúng row có giá+SL (Davanti 98 không phải 26).
- **Việc**: cross-doc merge theo shared shape-key `(record_bot_id, workspace_id, lower(entity_name)|spec)`; query-time COALESCE (lấy row có price+qty non-null) hoặc UPSERT-merge.
- **Files**: `ingest_stages_final.py` (`_dedup_stats_entities` → cross-doc), `stats_index_repository`.
- **Shape-based**: key theo shape, không brand. **Alembic**: CÓ (unique index cho ON CONFLICT). **Re-ingest**: CÓ. **Tier T1/T2**.

### Phase 4 — **Multi-format converter parity** (giải **B-FORMAT**)
- **Vấn đề giải**: bảng DOCX/PDF/HTML/XLSX cũng được row-split + header-merge như Sheets/CSV.
- **Việc**: route DOCX/PDF/HTML table rows qua **cùng** `rows_to_structured_markdown`; XLSX row-split toggle (`ingest_row_atomic`, default OFF backward-compat).
- **Files**: `docx_parser.py`, `kreuzberg_markdown_parser.py`, `excel_openpyxl_parser.py`.
- **Multi-format**: 1 converter mọi format. **Re-ingest**: CÓ. **Tier T2**.

### Phase 5 — **Parser token + list cap** (giải **B-CODETOK / B-TRUNC**)
- **Vấn đề giải**: space-spec "155 80 13" parse đúng; list "tất cả" không bị cắt 100.
- **Việc**: `_CODE_QUERY_RE` capture full space-joined spec (≥2 token); enumerate ops raise cap → repo cap (1000) + truncation marker.
- **Files**: `query_range_parser.py`, `query_graph.py` / constants. **Code-only**. **Tier T2**.

### Verify-gate (cross-cutting, BẮT BUỘC mỗi phase — no-guess)
- A/B load-test deterministic 3 bot (xe tabular + spa + legal prose) `bypass_cache=true`, cùng file câu hỏi, đo **PASS-rate + failure-layer BEFORE/AFTER** từng phase. Cohort phân biệt = câu quantity/date/count/list của xe.
- Golden: số đếm khớp `COUNT(*)` corpus; quantity khớp `col_4`; HALLU=0 (số bịa 0×).

---

## Genericity / sacred (CLAUDE.md) — mọi phase
- ✅ shape-based (FORM not VOCABULARY), 0 per-bot/brand literal · owner override qua `custom_vocabulary` (ADR-0006) · ✅ multi-tenant RLS · ✅ no app-inject/override answer (chỉ đổi DATA/retrieval, sysprompt là owner) · ✅ zero-hardcode (constants) · alembic-only cho content-state (sacred#7).

## Ưu tiên đề xuất
**Phase 1 (aggregate) → Phase 2 (role) → Phase 3 (reconcile)** = 3 cái T1 ăn thẳng dạng-lỗi (count/quantity). Phase 4-5 = T2 hardening. Làm tuần tự, đo A/B mỗi phase trước khi sang phase sau.
