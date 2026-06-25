# Spa bot — RAW Google-Sheets ingest + 40Q golden retest (2026-06-25)

**Flow:** commit T2+G4 (`5bf1792`) → restart `ragbot-py` → **delete 5 normalized happy-case docs** →
ingest **4 RAW Google-Sheets links verbatim** (NORMALIZE-to-IR test, ADR-0005) → 50Q golden.

## Ingest (RAW, no happy-case rewrite)
| doc | source | chars | chunks | stats entities |
|---|---|---|---|---|
| spa-1 | sheet gid=1394860155 | 715 | 1 | 18 |
| spa-2 | sheet gid=749628067 | 4 660 | 8 | 51 |
| spa-3 | sheet gid=0 | 358 | 1 | 12 |
| spa-4 | sheet gid=227222648 | 27 646 | 33 | 26 |

- All 4 active. **189 stats entities (86 unique)** extracted deterministically from raw.
- **G4 advisory FIRED on real data** (production evidence):
  - spa-2: `unassigned=[STT, col4, "CÁC DỊCH VỤ TRONG GÓI", "Giá Combo 10 buổi"]` (messy title-row).
  - spa-3: `has_name_column=FALSE`, `unassigned=[STT, "Giá Combo 10 buổi"]` — "Vùng triệt" not a name token (positional fallback still keyed it → prices passed).

## Score: 38/50 = **76%** (scorer-fixed) · ~78% real (1 scorer false-neg)
| Nhóm | Pass |
|---|---|
| 1 Thông tin cơ bản | 3/5 = 60% |
| 2 CSD CNC & Buffet | 9/10 = 90% |
| 3 Dịch vụ nổi bật | 4/8 = 50% |
| 4 Dưỡng sinh/Massage | 6/7 = 86% |
| 5 Triệt lông | 7/10 = 70% |
| 6 Quy trình tư vấn | 9/10 = 90% |

## Root cause of the 11 real misses (every claim has a raw-data line — rule#0)

**A. Stats-route topK=1 short-circuit DROPS prose note-lines (6 cases).** The fact exists in raw as a
single-column note but the synthetic stats chunk (1 entity) wins and omits it:
- "tối đa 10 buổi" ← spa-2 `"Mỗi gói sử dụng tối đa 10 buổi…"` (note) → bot refused.
- "20 bước tráng gương" ← spa-2 `"- Quy trình độc quyền hơn 20 bước…"` → refused.
- "có đau không / Diode" ← spa-2 `"Công nghệ Diode Laser… an toàn"` → refused.
- "hiệu quả 3-5 buổi" ← (note) → refused.

**B. Section-header row grouping lost (3 cases).** A `"2. Trẻ hóa da Ultherapy,,,"` header groups the
priced rows below it, but the section name isn't linked → bot "chưa thấy Ultherapy/tráng gương/Nâng cơ
Dr.Medi trong danh mục". Prices DO exist under the section.

**C. 1 conflation:** "Quy trình CSD chuyên sâu 10 bước" → bot "1 bước" (picked spa-4 `"Bước 1: Chào
khách"` consultation script). Correct `"10 bước chuẩn y khoa"` exists in spa-4 too.

**D. 1 HALLU (extrapolate) — HALLU=0 violation, reproducible:** "Triệt râu combo 10 buổi" → bot
**2.490.000** (= 249.000 × 10). Raw spa-3 `"Râu (nam),249000,1499000"` → combo = **1.499.000**. Stats
chunk surfaced the unit price; LLM multiplied by 10 instead of reading the combo column.

**E. 1 scorer false-neg:** "Giờ mở cửa" → bot `"9h đến 21h, thứ 2 đến chủ nhật"` = CORRECT (gold-fact
regex misfired).

## Honest verdict
- **RAW ingest (no happy-case rewrite) works out-of-the-box at 76-78%.** The data IS present; the gap is
  **RETRIEVAL** (stats-route hiding prose-notes + section grouping), **NOT input-format / column-roles**.
- **T2 + G4 confirmed working on real data**: G4 flagged spa-3 pre-test; names recognized; prices retrieved.
- **1 HALLU (D)** is the one sacred-rule concern → needs a stats-route fix (don't surface unit price for a
  combo query without the combo column / labelled linearization).
- Not a T2/G4 regression — these are pre-existing stats-route-vs-prose issues the raw (richer) corpus exposes
  more than the normalized one did.
