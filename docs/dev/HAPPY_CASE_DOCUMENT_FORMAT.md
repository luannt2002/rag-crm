# Happy-Case Document Format — quy chuẩn tài liệu đầu vào

> **Mindset (SOTA-backed)**: KHÔNG cố parse MỌI format bẩn (bất khả thi + brittle).
> Định nghĩa **1 happy-case chuẩn** cho mỗi loại tài liệu → flow tối ưu cho nó → nhanh,
> chuẩn, dễ cải thiện. Nguồn lệch chuẩn → **checker báo + khách sửa source** (rẻ hơn
> hardening parser). Khớp data-engineering best-practice: *"garbage-in → fix the
> SOURCE first; add parser logic only when source can't change"* (Databricks/Anyscale/
> unstructured) + Crestan-Pantel web-table taxonomy.

Checker tool: `python scripts/check_happy_case.py <file|--db NAME>` → report-card.

---

## 0. Nguyên tắc

- **1 canonical IR**: mọi format → structured markdown (`## heading` + `| table |`).
  Code chỉ tối ưu cho **happy-case** của IR này.
- **Happy-case = parse ĐÚNG 100% + nhanh + 0 ambiguity**. Lệch happy-case = checker
  cảnh báo, KHÔNG âm thầm đoán.
- **Dual-representation**: markdown (cho LLM) + stats-index `ParsedEntity{name,price,
  category}` (cho retrieval-filter). Happy-case phải extract sạch CẢ HAI.

---

## 1. HAPPY-CASE — SHEET / TABLE (catalog giá)

### ✅ Chuẩn (parse 100%, như spa-1/spa-3)

```
## <Tên nhóm dịch vụ>            ← section title: 1 ô, đứng riêng 1 dòng
STT, Tên, Giá                    ← header: cột tên + cột giá rõ ràng
1, Item A, 700000                ← 1 entity / dòng, giá ở CỘT giá
2, Item B, 800000
                                 ← dòng trống = ranh giới bảng con
## <Tên nhóm khác>
Tên, Giá
Item C, 129000
```

**Quy ước cột (token nhận diện role — domain-neutral):**

| Role | Header chấp nhận | Bắt buộc |
|---|---|---|
| **name** | `Tên`, `Tên dịch vụ`, `Tên sản phẩm`, `Name`, `Dịch vụ`, `Sản phẩm`, `Gói`, `Combo` | ✅ |
| **price** | `Giá`, `Đơn giá`, `Giá lẻ`, `Giá gốc`, `Giá sale`, `Phí`, `Price`, `Amount` | ✅ (nếu là catalog giá) |
| category | `Nhóm`, `Danh mục`, `Loại`, `Vùng`, `Khu vực`, `Category` | optional (stub) |
| ordinal | `STT`, `No`, `ID` | optional |

**Quy ước giá trị giá** (format chấp nhận): `700000` · `1.499.000` · `1,499,000` ·
`899k` · `1tr499` · `1.5tr` · `6 triệu` · `1M` · `5000 nghìn` · `200.000đ` ·
`từ 500k` · `500k/buổi` (trong cột giá). KHÔNG: range `100k-200k`, `2tr5`.

**Code path**: `tabular_markdown.rows_to_structured_markdown` → `## + |table|` →
`document_stats.parse_table_chunks` → `ParsedEntity`. Coverage mục tiêu **≥95%**.

### 🔴 KHÔNG happy-case (checker REJECT, khách phải sửa source)

| Anti-pattern | Ví dụ thật | Vì sao hỏng | Sửa source |
|---|---|---|---|
| **Synonym-export** | xe-3 `question: <62 cột biến-thể> \| productname \| giá` | search-index ≠ catalog | re-export `Tên \| Giá` sạch, synonym để file alias riêng |
| **Pivot / year-cols** | `Sản phẩm \| 2022 \| 2023` | entity↔cell mất ngữ nghĩa | unpivot về `Sản phẩm \| Năm \| Giá` |
| **Transposed** | dịch vụ = CỘT, thuộc tính = HÀNG | xoay 90° | transpose về row-oriented |
| **Prose-in-cell** | spa-4 script: ô chứa cả đoạn tư vấn 500 từ | không phải bảng | tách script ra DOC riêng |
| **Merged/multi-row header** | `Quý 1` span 3 cột | markdown mất colspan | tách 1-hàng-header phẳng |
| **Name lẫn giá/số** | cột tên = "Hiện tại dịch vụ…700000" | prose comma-split | 1 cột tên ngắn + 1 cột giá |

---

## 2. HAPPY-CASE — DOC (văn bản: hợp đồng, thông tư, chính sách)

### ✅ Chuẩn (như thongtu: 87 heading + 12 bảng)

```
# <Tiêu đề tài liệu>
## Chương I / Điều 1 / Mục 1.1     ← heading phân cấp markdown
<đoạn văn xuôi giải thích>
| Cột | Cột |                       ← bảng markdown chuẩn (nếu có)
| --- | --- |
| ... | ... |
```

**Quy ước:**
- Cấu trúc bằng **heading markdown** (`#`/`##`/`###`) — KHÔNG bằng bôi đậm/cỡ chữ.
- Bảng = markdown table chuẩn (header + `---` + rows).
- Giá/số nhúng trong câu (`499K/buổi`) → **GIỮ trong prose** (đừng tách field — mất
  điều kiện = HALLU). SOP/script: mỗi bước (`Bước 1:`) là 1 heading.

**Code path**: docx/pdf/html → `kreuzberg/docx_parser` (markdown) → `smart_chunk` HDT
(theo heading) → chunk mang `structural_path`. KHÔNG cần price-coverage (doc ≠ catalog).

### 🔴 KHÔNG happy-case
- Văn xuôi KHÔNG heading → flat 1 block (retrieval kém). → thêm heading.
- Script tư vấn export thành SHEET (dấu phẩy → bảng giả). → để dạng DOC có `Bước N:`.

---

## 3. Decision: doc thuộc happy-case nào?

```
File vào
├─ có heading markdown (≥3 ## ) ──────────────→ DOC happy-case
├─ comma-dense + có cột Tên+Giá ──────────────→ SHEET catalog happy-case
├─ comma-dense KHÔNG cột giá (tồn kho/manifest)→ SHEET non-price (entity-only OK)
├─ ô chứa prose dài / "question:" / 1 ô khổng lồ→ 🔴 NON-happy (sửa source)
└─ pivot/transposed (số ở mọi ô, header=năm) ──→ 🔴 NON-happy (unpivot)
```

---

## 3b. SUMMARY / LISTING doc — fix câu "liệt kê / tóm tắt" (giới hạn topK)

Câu "liệt kê TẤT CẢ dịch vụ" / "tóm tắt" KHÔNG trả được bằng top-K retrieval (K dòng ≠
tất cả; tăng K thì context LLM quá lớn). **Giải pháp scope-template**: mỗi bot có thêm
**1 doc tóm tắt + liệt kê đầy đủ** (gom mọi entity theo nhóm + giá), sinh DETERMINISTIC
từ stats-index (`scripts/build_bot_summary.py`, KHÔNG LLM). 1 chunk = cả danh sách →
query "liệt kê" hit đúng nó.

**Cặp đôi để work**: (a) data có summary-doc + (b) sysprompt cho phép xổ. Sysprompt thêm
1 luật shape: *"khi khách hỏi LIỆT KÊ TOÀN BỘ / xem hết → xổ đầy đủ từ summary-doc, không
hỏi ngược; câu mơ hồ 'có gì' vẫn hỏi nhóm"*. **Sysprompt sửa QUA admin-UI có audit (rule
#7), KHÔNG psql; nội dung tenant-specific KHÔNG vào git** (domain-neutral). Verified live:
"toàn bộ danh sách dịch vụ + giá" → bot xổ list đầy đủ.

## 4. Vì sao KHÔNG cố support-all?

SOTA (Docling/unstructured) giữ **typed-IR + ML model** mới phủ hết pivot/merged/span —
state-machine tay KHÔNG phủ nổi. Chi phí hardening parser cho mỗi format dị = vô hạn +
brittle (thêm 1 cột là vỡ). **Happy-case + checker** = chi phí hữu hạn, khách tự sửa
source 1 lần, code sạch domain-neutral. Pivot/merged → roadmap typed-JSON-sidecar (ADR).
