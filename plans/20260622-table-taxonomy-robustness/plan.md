# [T1-Smartness] Table-taxonomy robustness — L1/L3 generalize cho mọi cấu trúc bảng

> Tier: **T1-Smartness** (tăng Coverage extraction cho data đa cấu trúc, không đụng pattern/SOLID).
> Ngày: 2026-06-22 · Branch: `expert-rag-squash-conflate-logcenter-20260619`
> Evidence gốc: `scripts/table_taxonomy_stress_test.py` (27 cấu trúc, scorecard dưới).

---

## 0. Vấn đề (evidence, không đoán)

Stress-test 27 cấu trúc bảng (taxonomy SOTA: Docling / Microsoft TATR / PubTables-1M /
SciTSR / Lautert web-table / unstructured.io) qua **code production thật**
(`rows_to_structured_markdown` L1 + `parse_table_chunks` L3):

```
PASS=9  GRACEFUL=5  PARTIAL=5  FAIL=4  RISK=3
```

| Verdict | Nghĩa | Cases |
|---|---|---|
| PASS 9 | relational row-oriented đúng | T-01,03,04,13,14,19,22,23,33 |
| GRACEFUL 5 | md-grid giữ nguyên → vector/LLM đọc được, không rác | T-07,08,10,18,26 |
| **BUG 12** | sai / mất / đẻ rác | bảng dưới |

### 12 bug có evidence (file:line root cause)

| ID | Cấu trúc | Triệu chứng | Root cause |
|---|---|---|---|
| **R-02** | name chứa tiền ("Gói 6 triệu") | DROP nguyên dòng | `document_stats.py:288` `parse_money_vn(col)` bắt tiền TRONG name → col0 thành price → name=None |
| **T-05** | stub/cột-nhóm `Nhóm,Tên,Giá` | entity="Cao cấp" (nhóm) | `document_stats.py:311` lấy col0 làm name, không biết col0 là category-stub |
| **T-11** | rowspan nhóm-trống | mất Item A/C | cùng `:311` + không forward-fill rowspan |
| **T-20** | dòng "Tổng cộng" | entity rác "Tổng cộng"=300k | `:336` guard không loại aggregate-word |
| **T-02** | transposed (dịch vụ=cột) | entity rác "Giá"=100k | `:311` name="Giá" (label) không bị loại |
| **T-17** | key-value dọc | entity rác "Giá"=100k | cùng `:311` |
| **T-27** | layout/nav table | entity "Trang chủ" | name-only no-price, harm thấp |
| **T-06/09** | 2-D / year-columns | no-grid + matrix-loss | `tabular_markdown.py:71` year "2022" = pure-money → không thành header |
| **R-01** | section-in-header `X,,col,col` | section mất | `tabular_markdown.py:135` cả dòng thành header, col0 bị chôn |
| **R-03** | title dài >8 từ | section mất | `tabular_markdown.py:122` cap cứng `<=8` chặn title dài |

---

## 1. Nguyên tắc kiến trúc (SOTA Rec #2 — dual-storage, unstructured.io/Docling)

> **Markdown grid = UNIVERSAL** (27/27 đọc được cho vector/LLM, orientation-agnostic).
> **Stats fast-path = CHỈ bắn cho relational sạch, SKIP phần còn lại — đừng đẻ rác.**

→ "Code chuẩn expert" KHÔNG = "parse mọi bảng exotic" (cần ML như TATR/Docling).
Mà = **(P1)** sửa đúng nhóm relational + **(P2)** stats conservative chặn rác +
**(P3)** orientation-detect (ambitious, deferred). **TẤT CẢ shape/grammar-based, KHÔNG
hardcode value, KHÔNG support riêng tenant nào.**

---

## P1 — Relational correctness (impact Coverage trực tiếp)

### P1.1 — R-02: pure-money gate cho price-detection (`document_stats.py`)

- **Root**: `_extract_entity_from_row:288` gọi `parse_money_vn(col)` — hàm này bắt tiền
  ở BẤT KỲ đâu trong chuỗi (docstring `:161` "extracts FIRST money value found"). Nên
  name "Gói 6 triệu" → đọc thành price 6.000.000 → col0 mất tư cách name → drop dòng.
- **Expert fix**: một cell là PRICE chỉ khi **PURELY money** (digit+separator+đơn-vị,
  KHÔNG kèm chữ mô tả). Tái dùng đúng shape `_PURE_MONEY_RE` đã có ở
  `tabular_markdown.py:43` (SSoT — chuyển thành helper share, hoặc import). Ở `:288`:
  `money = parse_money_vn(col) if _is_pure_money(col) else None`.
- **Effect**: "Gói 6 triệu" → not-pure-money → thành NAME; "6 triệu"/"6000000" đứng một
  mình vẫn = price. T-01/R-02 PASS.
- **No-hardcode**: shape regex, 0 literal. **Test**: R-02 trong corpus → PASS.

### P1.2 — T-05/T-11: header-aware column-role + rowspan forward-fill (`document_stats.py`)

- **Root**: `_extract_entity_from_row:311` luôn lấy col-non-money ĐẦU TIÊN làm name. Khi
  cột đầu là CATEGORY-stub (`Nhóm`/`Danh mục`) → stub thành name, item thật rớt xuống attr.
- **Expert fix** (SOTA T-05/T-11 — TATR cell-role / Docling row-header class): khi có
  HEADER, gán **role mỗi cột theo token chuẩn-hoá**. **CHỈNH (verify 2026-06-22)**:
  `_HEADER_EXACT_TOKENS` hiện là 1 set phẳng (chỉ dùng cho "is-header"); phải **TÁCH
  thành 3 sub-set role** + bổ sung token thiếu (`nhom`, `san pham` CHƯA có trong set):
  - `_NAME_COL_TOKENS`: ten/name/dich vu/service/san pham/goi/combo
  - `_CATEGORY_COL_TOKENS`: nhom/danh muc/category/loai/vung/type  ← thêm `nhom`
  - `_PRICE_COL_TOKENS`: gia/price/phi/amount/cost
  (`_HEADER_EXACT_TOKENS` = union 3 set → giữ `_is_header_row` không đổi.)
  Name = value ở name-col; category = value ở category-col (**forward-fill** khi blank =
  rowspan continuation, SOTA T-11); price từ price-col. **Fallback positional** y nguyên
  khi không có header (giữ PASS hiện tại của no-header T-03).
- **Effect**: T-05/T-11 → name=Item A/B/C, category=Cao cấp/Phổ thông (đúng B3). Tổng quát
  luôn T-04 (multi-row header).
- **No-hardcode**: token-set là grammar/structure (đã tồn tại `_HEADER_EXACT_TOKENS`),
  domain-neutral. **Test**: T-04/T-05/T-11 PASS + category đúng.

### P1.3 — R-01: section-in-header split (`tabular_markdown.py`)

- **Root**: `:135` `_looks_header` thấy `["Gói dịch vụ A","","Thời gian","Giá"]` đủ label
  → cả dòng thành header, "Gói dịch vụ A" bị chôn làm col0.
- **Expert fix**: detect SHAPE "section-in-header" TRƯỚC nhánh header: **col0 filled +
  col1 empty (gap) + col2+ filled-label** → emit `## <col0>` rồi mở header từ col2+. Gap
  ngay sau col0 là tín hiệu cấu trúc (header thật không có lỗ giữa). Shape-only.
- **Effect**: R-01 → `## Gói dịch vụ A` + table(Thời gian|Giá). section bound.
- **No-hardcode**: shape (vị trí empty cell), 0 literal. **Test**: R-01 sect✓.

### P1.4 — R-03: long-title bằng lookahead, bỏ cap cứng (`tabular_markdown.py`)

- **Root**: `:122` `len(only.split()) <= 8` chặn title 1-cell dài >8 từ → thành NOTE.
- **Expert fix**: bỏ cap-từ cứng; section-title 1-cell = **đứng NGAY TRÊN một header/data
  row** (lookahead precedes-a-table) AND không-phải-prose (không kết câu `.…`, không
  price-note, không bullet). Structure-based thay vì đếm từ.
- **Effect**: R-03 title 9-từ → `##` đúng; prose-1-cell vẫn là NOTE (không false-positive).
- **No-hardcode**: structure (lookahead), bỏ magic-8. **Test**: R-03 sect✓ + 1 case prose
  dài KHÔNG thành section (guard regression).

---

## P2 — Stats conservative (chặn rác vào index, không vỡ md)

### P2.1 — Loại name = structural-label / aggregate-word (`document_stats.py`)

- **Root**: `_extract_entity_from_row:336` guard chưa loại name là cột-label/aggregate
  ("Giá","Tổng cộng","Thuộc tính","Chỉ số") → transposed/KV/total đẻ entity rác.
- **Expert fix**: thêm reject vào guard: nếu `_normalise(name)` ∈ **label/aggregate token
  set** → `name=None` (skip, để md+vector lo). **CHỈNH (verify 2026-06-22)**: `gia` ĐÃ
  có trong `_HEADER_EXACT_TOKENS` (nên "Giá" bị reject ✓) NHƯNG aggregate CHƯA có → thêm
  set mới `_AGGREGATE_TOKENS = {tong, tong cong, total, subtotal, cong, thuoc tinh,
  chi so}`. Reject khi `_normalise(name) ∈ (_HEADER_EXACT_TOKENS | _AGGREGATE_TOKENS)`,
  **exact-match** (verify: "gia vang" ≠ "gia" → "Giá vàng" KHÔNG bị drop ✓).
- **Effect**: T-02/T-17/T-20 RISK→GRACEFUL (md-grid vẫn còn, 0 entity rác).
- **No-hardcode**: token grammar/structure (giống `_HEADER_EXACT_TOKENS` sẵn có),
  domain-neutral. **Test**: T-02/T-17/T-20 không còn entity trong reject-set.

### P2.2 — T-27 layout/nav (residual, harm thấp — chấp nhận hoặc optional)

- "Trang chủ" name-only **price=None** → KHÔNG vào price-index (chỉ thêm 1 name). Harm
  thấp, hiếm (nav-table trong sheet giá là bất thường). **Quyết định**: để residual, log
  trong report; KHÔNG thêm heuristic mỏng manh dễ false-drop catalog name-only thật.

---

## P3 — Orientation detection (ambitious, DEFERRED — cần ADR nếu làm)

> P2 đã chặn rác cho transposed/pivot. P3 là để các bảng đó CŨNG có structured-entity
> (không chỉ graceful-skip). SOTA: cần ML (TATR cell-role / Docling TableFormer) cho phủ
> đầy đủ — state-machine tay chỉ làm heuristic. **Defer + ghi ADR khi quyết làm.**

- **P3.1 transposed detect+rotate**: col0 toàn label-unique + col1+ toàn pure-money →
  xoay 90° trước extraction (SOTA T-02). Risk: heuristic sai trên bảng mixed.
- **P3.2 multi-row-header merge**: gộp 2 dòng header phân cấp thành "Quý 1 > Doanh thu"
  (SOTA T-04, Docling repeat-span). 
- **P3.3 pivot/matrix unpivot**: cell → (row_key,col_key,value) long-form (SOTA T-07).
- **Gate**: chỉ làm khi có data thật cần (đo coverage trước), tránh over-engineer T3.

---

## 2. Verification (sau mỗi P)

1. **Rerun corpus**: `python scripts/table_taxonomy_stress_test.py`
   - Target sau P1+P2: `PASS≥18  GRACEFUL≥8  RISK=0  FAIL≤2` (FAIL còn lại = T-06/09
     pivot → P3 territory, ghi rõ).
2. **No-hardcode grep**:
   ```bash
   grep -rnE "(spa|triet|massage|goi dau|xe|lop|thong tu)" \
     src/ragbot/shared/tabular_markdown.py src/ragbot/shared/document_stats.py \
     | grep -viE "docstring|#|comment"   # expect 0 functional literal
   ```
3. **Full pytest**: `pytest tests/unit/ -q` (đặc biệt test_tabular_markdown_b3,
   test_document_parser_strategy, test_parser_google_sheets) → 0 regression.
4. **3-bot replay** (sau khi user wipe+reupload): re-ingest → coverage eval, so baseline.

---

## 3. Files changed (dự kiến)

| File | P | Thay đổi |
|---|---|---|
| `src/ragbot/shared/tabular_markdown.py` | P1.3, P1.4 | section-in-header split + long-title lookahead; export `_is_pure_money` (SSoT) |
| `src/ragbot/shared/document_stats.py` | P1.1, P1.2, P2.1 | pure-money gate; header column-role + forward-fill; reject label/aggregate name |
| `tests/unit/test_table_taxonomy.py` (NEW) | all | corpus 27-case làm regression test thật (assert PASS/GRACEFUL) |
| `scripts/table_taxonomy_stress_test.py` | — | đã có (diagnostic CLI) |

---

## 4. Risk / Rollback

- **Risk P1.2** (column-role): nếu header token miss → fallback positional (giữ hành vi
  cũ). Không vỡ no-header.
- **Risk P2.1**: reject quá tay có thể drop catalog name trùng label-word. Mitigation:
  reject CHỈ khi name == token (exact normalise), KHÔNG substring → "Giá vàng" (name
  thật) không bị loại vì != "gia".
- **Rollback**: mỗi P là commit riêng; corpus test xanh là gate. Đỏ → revert commit đó.

---

## 5. Compliance self-check (CLAUDE.md)

- ✅ Sacred #10: KHÔNG inject text/override answer — chỉ sửa extraction.
- ✅ Domain-neutral: 0 literal tenant/brand; token-set grammar/structure.
- ✅ Zero-hardcode: reuse constants `DEFAULT_STATS_ATTR_MAX_*`; reject-set là grammar token.
- ✅ HALLU=0: extraction vẫn pure-Python regex, 0 LLM.
- ✅ T1 declared. ✅ No version-ref. ✅ Test real assertion (corpus PASS/GRACEFUL).
- ✅ EVOLVE not rewrite: sửa cục bộ L1/L3, giữ khung Port/registry/strategy.
```
```
