# Cấu trúc DATA — bot chinh-sach-xe (Lốp Nam Phát)

> Bot: cửa hàng lốp xe **Nam Phát**. Brand phân phối: **Landspider** (Thailand) + **Rovelo** (Vietnam).
> record_bot_id: `c6e1fc56-d070-439d-99a6-c8b4964b4d2d`. Nguồn: 4 Google Sheets/Doc.
> Mục đích file này: (1) ghi cấu trúc THẬT hiện tại + vấn đề, (2) template CHUẨN để re-upload.

---

## PHẦN 1 — Cấu trúc THẬT hiện tại (4 file)

### xe-3 — Bảng giá chính ✅ (chuẩn nhất, dùng làm mẫu)
- **Nguồn**: Google Sheet · 187 dòng · có giá.
- **Header (dòng 1)**:
  ```
  | question | code | productname | answer | quantity | price | date1 | date2 | image | col10 | col11 | col12 | col13 |
  ```
- **Ý nghĩa cột**: `question`=blob biến-thể-size để search · `code`=mã SKU · `productname`=tên đầy đủ (CÓ brand) · `price`=giá · `quantity`=tồn · `date1/2`=ngày · `image`=ảnh · `col10-13`=thừa/trống.
- **Vấn đề**: có cột thừa (col10-13); `question` blob rất dài (noise). Nhưng có header rõ + productname + price → OK.

### xe-1 — Kho hàng ⚠️ (thiếu header + brand ẩn trong mã)
- **Nguồn**: Google Sheet · 207 dòng · **KHÔNG giá**.
- **Dòng data (KHÔNG có header row)**:
  ```
  | Kho lốp LANDSPIDER | 2-R16 195/55 LPD | Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P | 26 | <ảnh...> |
     nhóm/kho             mã (size+RVL/LPD)   productname (tên thật+brand)                    ngày   ảnh
  ```
- **Vấn đề**: KHÔNG dòng header · KHÔNG price · brand chỉ là hậu tố mã (`RVL`=Rovelo/`LPD`=Landspider). → **gốc bug false-deny** (entity_name lấy cột mã, mất chữ brand).

### xe-2 — Manifest hàng về ❌ (cấu trúc HỎNG, chỉ 1 chunk)
- **Nguồn**: Google Sheet · parse thành 1 chunk · **KHÔNG giá, KHÔNG cột tên rõ**.
- **Cấu trúc thật (header 2 tầng + banner)**:
  ```
  | MARKS | CARGO DESCRIPTION      |         |   ← banner chứng từ (không phải header cột)
  | GR    | LANDSPIDER BRAND TYRES | NGÀY VỀ |   ← header thật + nhãn nhóm brand span ngang
  |       | 185/55R16 CITYTRAXX G/P| 28-thg 11 |   ← data, cột brand TRỐNG
  ```
- **Vấn đề**: banner + nhóm phân cấp → pipeline không hiểu; `NGÀY VỀ` (ngày về hàng) bị chôn; brand ở dòng nhóm không xuống data.

### xe-4 — Chính sách bảo hành ✅ (prose, để nguyên)
- **Nguồn**: Google Doc HTML · 8 đoạn.
- **Nội dung**: "CHÍNH SÁCH BẢO HÀNH LỐP XE — Landspider (Thailand) + Rovelo (Vietnam) — Cty TNHH Lốp Nam Phát...".
- **Vấn đề**: không có (văn bản chính sách, đúng dạng Doc).

---

## PHẦN 2 — DATA GAP đã đo (rule #0, DB-verified)

- Size **195/55R16 có giá**: chỉ 2 dòng, **cả 2 đều LANDSPIDER** (195/55R15=963.000 · 195/55R16=1.044.000).
- **Rovelo 195/55R16 CÓ GIÁ = 0** → Rovelo size này nằm ở xe-1 (kho, không giá), thiếu ở xe-3 (bảng giá).
- ⇒ Bot deny "Rovelo 195/55R16" vì **không có giá Rovelo size đó**, KHÔNG phải lỗi code. → **re-upload cần đủ giá cho mọi (brand × size) bán thật.**

---

## PHẦN 3 — TEMPLATE CHUẨN để re-upload (5 file)

**Nguyên tắc vàng**: mỗi sheet **dòng 1 = header rõ**, mỗi cột 1 khái niệm, **product_name & brand là cột riêng**, **1 sheet = 1 cấu trúc nhất quán**. Làm đúng thì **KHÔNG cần khai column_roles, KHÔNG cần marker, KHÔNG đoán**.

### Sheet A — Bảng giá (gộp xe-1 + xe-3, đủ mọi brand×size)
| brand | product_name | size | code | price | quantity | arrival_date | image |
|---|---|---|---|---|---|---|---|
| Rovelo | Lốp Rovelo 195/55R16 RHP-A68 | 195/55R16 | 2-R16 195/55 RVL | 1044000 | 24 | 28-11 | https://... |
| Landspider | Lốp xe LANDSPIDER 195/55R16 CITYTRAXX G/P | 195/55R16 | 2-R16 195/55 LPD | 1098000 | 12 | 28-11 | https://... |

- `brand` = cột riêng (Rovelo/Landspider) → brand-aware match ngon.
- `product_name` = tên đầy đủ có brand viết chữ (KHÔNG chỉ mã) → shape-typing chọn làm "tên".
- `price` = số thuần, 1 đơn vị. **Mọi (brand × size) bán thật PHẢI có giá** (đóng data gap).
- `arrival_date` = NGÀY VỀ (thay xe-2) → trả "khi nào về hàng".

### Sheet B — Hàng đang về / sắp về (thay xe-2, dọn sạch)
| brand | product_name | size | arrival_date | status |
|---|---|---|---|---|
| Landspider | Lốp Landspider 185/55R16 CITYTRAXX G/P | 185/55R16 | 28-11 | đang về |

→ 1 dòng header, cột brand xuống từng dòng (KHÔNG để ở dòng nhóm), bỏ banner "MARKS/CARGO".

### Doc C — Chính sách bảo hành (giữ như xe-4, dạng Doc/prose)
### Doc D — FAQ / hướng dẫn (nếu có, dạng Doc)
### File thứ 5 — theo template Sheet A hoặc Doc tuỳ nội dung

---

## PHẦN 4 — Manifest sẽ tự sinh (mô tả cho LLM, 0 LLM call)

Với Sheet A sạch, hệ thống tự sinh (shape/value, không cần khai):
```
xe-priceA (table, N rows):
  brand         → identifier/group  (few distinct values: Rovelo, Landspider)
  product_name  → NAME              (multi-word free-text)
  size          → identifier        (size-code shape 195/55R16)
  code          → identifier        (SKU code)
  price         → VALUE             (money-shape, coverage 100%)
  quantity      → VALUE             (number)
  arrival_date  → date
  image         → url
```
→ Manifest này đưa kèm chunks + system_prompt cho answer-LLM → LLM đọc "product_name = tên, price = giá" → trả đúng, **0 hardcode, 0 đoán, 0 LLM thêm**.
