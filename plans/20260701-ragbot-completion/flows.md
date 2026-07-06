# Ragbot — Chi tiết TẤT CẢ LUỒNG (as-is broken → to-be fixed)

> Kèm plan [plan.md](plan.md). Mỗi luồng: các bước + `file:line` + HIỆN TẠI (vỡ ở đâu) → SAU FIX.
> `[FIX-Pn]` = do Phase n giải quyết.

---

## LUỒNG 1 — INGEST (upload → chunk + stats-index)

```
POST /api/ragbot/documents/create            interfaces/http/routes/documents.py
  │  (BE-to-BE, X-Idempotency-Key)
  ▼
detect_parser  (mime → file-ext → BYTE-SNIFF)  infrastructure/parser/registry.py
  │   URL pdf octet-stream → sniff %PDF- mới route đúng
  ▼
parser.parse()  → structured-markdown (canonical)
  ├─ GoogleSheets/CSV  google_sheets_parser.py:127  rows_to_structured_markdown → :50 _split_md_to_row_chunks (per-row + header)
  ├─ XLSX              excel_openpyxl_parser.py:82   rows_to_structured_markdown (CẢ sheet = 1 blob)   ✗ [FIX-P1] L1-XLSX-SHEET-ONE-BLOB
  ├─ DOCX              docx_parser.py:110-119  BYPASS converter, hardcode rows[0]=header               ✗ [FIX-P1] L1-DOCX-BYPASS
  └─ PDF/HTML/PPTX     kreuzberg_markdown_parser.py  BYPASS converter, flat grid → col_N               ✗ [FIX-P1] L1-KREUZBERG-NO-STRUCT
  ▼
─────────── L1 STRUCTURE RECOVERY (rows → markdown sạch) ───────────  ← PAIN gốc, xem LUỒNG 3
  ▼
chunking  (size-budget)  orchestration/.../strategies.py:142
  │   ✗ gộp nhiều row/chunk → bind nhầm giá row kế  [FIX-P2] L2-ROW-MIXING
  ▼
ingest_stages_final.py
  ├─ :474 đọc custom_vocabulary["column_roles"]  (glossary owner, Tier-2)  ✅
  ├─ :488 parse_table_chunks(rows, custom_roles)  document_stats.py:913
  │     └─ :1000 _column_roles → name/category/price/aliases  (THIẾU quantity/date → col_N)  ✗ [FIX-P1/P2]
  │     └─ :637 label = header[idx] else f"col_{idx}"   ← col_N khi header mất
  ├─ :500 analyze_table_headers → unassigned_columns  (CHẾT trong log, owner không thấy)  ✗ [FIX-P1] fail-loud
  ├─ :568 aggregate_summary → summary_json  (write-only orphan)  ✗ [FIX-P3] wire read
  ├─ INSERT document_service_index  (entity_name/category/price/attributes_json)
  └─ embed chunks
  ▼
store  document_service/__init__.py
  │   ✗ :911 dedup SELECT `deleted_at IS NULL` → is_reindex=False → KHÔNG purge chunk cũ
  │      → re-ingest KHÔNG sạch → col_N stale sót   ✗✗ [FIX-P0] DQ-REINGEST-PURGE-BUG (chặn đo lường)
  ▼
document_chunks + document_service_index  (+ embedding)
```

---

## LUỒNG 2 — QUERY (câu hỏi → trả lời)

```
POST /api/ragbot/chat  (hoặc /test/chat)   4-key: (record_tenant_id, workspace_id, bot_id, channel_type)
  ▼
understand  nodes/understand.py:113  intent classify (heuristic → LLM)
  │   ✗ intent label CHỈ tune grade/rerank, KHÔNG gate stats-path  (2 classifier rời)
  ▼
routing  nodes/routing.py:65  complexity: simple→retrieve | complex→decompose (multi-query)
  ▼
retrieve  nodes/retrieve.py:215
  ├─ parse_range_query / parse_list_query / parse_code_query  query_range_parser.py → operation
  │     "có bao nhiêu" → op=count (parse_list_query:358)                        ✅ [P1a shipped, uncommitted]
  ├─ _do_stats_lookup  query_graph.py:2102
  │     ├─ op==count      → count_by_name_keyword COUNT(*)  → count-fact chunk   ✅ [P1a] (xem LUỒNG 4)
  │     │     ✗ THIẾU GROUP-BY series (5 dòng), THIẾU sum/avg                    ✗ [FIX-P3]
  │     ├─ op==keyword    → query_by_name_keyword → rows
  │     │     ✗ render col_4:214|col_6:26 KHÔNG nhãn → LLM đoán  (nếu L1 vỡ)     ✗ [FIX-P1/P2]
  │     ├─ op in max/min  → top_by_price
  │     └─ else           → query_by_price_range
  │     ✗ list cap DEFAULT_STATS_INDEX_LIMIT=100 < 257 → undercount im lặng      ✗ [FIX-P3] B-TRUNC
  │     ✗ 0 CROSS-DOC JOIN → đáp án nhiều doc miss  (xem LUỒNG 5)                ✗ [FIX-P4]
  ├─ (nếu stats miss) → vector + BM25 hybrid → RRF fuse (k=60) → rerank
  ▼
generate  1 LLM call  context = synthetic-chunk + retrieved chunks (double-newline / XML)
  │   LLM tự ghép multi-doc (retrieve-then-concatenate — như cả ngành)
  ▼
answer  (+ citations, grounding check, HALLU trap)
```

---

## LUỒNG 3 — L1 STRUCTURE RECOVERY (chi tiết — rows → markdown sạch)  `[FIX-P1]`

```
raw rows (CSV matrix / cell grid)
  ▼
[NEW] normalize_rows()   ← THÊM MỚI, form-only, domain-neutral
  ├─ used-range trim: cắt dòng rỗng ĐẦU + CUỐI (đuôi Excel)          PROVEN
  ├─ skip-blank + gap-K: run<K = spacer BỎ; run≥K = TABLE-BREAK       PROVEN (case 02,03)
  │     K = const DEFAULT_TABLE_GAP_ROWS
  ├─ forward-fill: cột sparse (ô gộp) fill giá trị từ dòng trên       PROVEN (case 04)
  │     gate: dòng trên có value AND dòng này cột khác populated
  └─ trim empty header col (leading+trailing)                         (case 07,08)
  ▼
rows_to_structured_markdown()   shared/tabular_markdown.py   ← ĐÃ CÓ, giữ nguyên
  ├─ :102 _is_header_continuation (multi-row header merge)  ✅
  ├─ :64 _is_pure_money gate (name chứa tiền không thành price)  ✅
  ├─ :220 section-in-header split  ✅
  └─ emit: ## section + | header | + | --- | + data rows
  ▼
_split_md_to_row_chunks()   google_sheets_parser.py:50  ← ĐÃ CÓ
  │   :84 out = section + HEADER + sep + 1 row   (mỗi row tự mang header)  ✅
  │   ✗ HIỆN: blank line reset header (:68) → SAU normalize KHÔNG còn blank → hết vỡ
  ▼
parse_table_chunks()  document_stats.py:913
  ├─ _column_roles: header → name/category/price/aliases (+ owner glossary Tier-2)
  └─ :637 attributes[header[idx]] = value   → "Số lượng: 214"  (KHÔNG col_N vì header CÓ)
  ▼
[NEW] fail-loud: nếu còn unassigned/col_N → surface DTO cho owner (không giấu log)  ← THÊM
  ▼
entities (name/category/price/attributes có nhãn)
```

**Ranh giới (mindset):** CẤU TRÚC → robust recover tự động (form-only, không đoán). NGHĨA cột mơ hồ →
fail-loud + owner glossary (KHÔNG bịa).

---

## LUỒNG 4 — ANALYTICAL (count/sum/group-by)  `[P1a shipped + FIX-P3]`

```
"có bao nhiêu loại X"
  ▼
parse_list_query:358 → op=count, keyword=X   ✅
  ▼
_do_stats_lookup count branch  query_graph.py:2122   ✅ (uncommitted [FIX-P0])
  ├─ count_by_name_keyword → SQL COUNT(*) scoped record_bot_id  → 117   ✅ verified
  └─ [FIX-P3] nếu "loại/dòng" → GROUP BY series-key (recurring-token) → 5 dòng
  ▼
count-fact synthetic chunk  "X — count: 117"   (KHÔNG dump priced rows → hết bịa/leak giá)
  ▼
LLM narrate "Có 117..."   (source fact, LLM narrate — KHÔNG app-override #10)
```
`[FIX-P3]` thêm: SUM/AVG (repo + signal), COUNT(*) exact + capped-honesty "N of M (capped)".

---

## LUỒNG 5 — CROSS-DOC RECONCILE (đáp án nhiều doc)  `[FIX-P4]`

```
HIỆN TẠI: 3 doc → 3 entity RỜI, 0 join → "155/80R13 giá + ngày về" → miss ngày (sheet3)

SAU FIX:
entity@sheet1 (giá 684k)    ┐
entity@sheet2 (kho, ảnh)    ├─ normalized shape-key = (record_bot_id, workspace_id, lower(spec|code))
entity@sheet3 (ngày về)     ┘        ← alembic unique index
  ▼
query-time reconcile: GROUP BY shape_key across doc CỦA BOT
  merge fragments: prefer non-NULL (giá@1 + ngày@3)   RLS scoped
  ▼
1 entity hợp nhất → trả đủ giá + tồn + ngày về
```
**Pre-req DATA:** key phải NHẤT QUÁN cross-sheet (hiện sheet1 code ≠ sheet3 cargo-desc) → owner chuẩn hóa
HOẶC glossary khai key. Engine giỏi mấy cũng không join 2 key khác nhau = 2 SP khác.

---

## Bản đồ vỡ → Phase
| Luồng | Vỡ | Phase |
|---|---|---|
| INGEST store | purge-bug (stale không sạch) | **P0** ⭐ |
| INGEST parse | DOCX/Kreuzberg/XLSX bypass converter | P1 |
| L1 recovery | blank/ô-gộp/headerless/empty-col | **P1** (2 fix PROVEN) |
| L2 chunk | row-mixing, col_N-linearize, breadcrumb | P2 |
| QUERY analytical | GROUP-BY series, sum/avg, list-cap | P3 |
| QUERY cross-doc | 0 join | P4 |
| RLS/security | superuser DSN, CB-4xx | P5 |

→ Thứ tự: **P0 (unblock) → P1 (L1 + golden 15-case) → P2 → P3 → P4 → P5**. Mỗi phase gated golden + A/B.
