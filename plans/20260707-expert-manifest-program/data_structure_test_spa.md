# Cấu trúc DATA — bot spa (test-spa-id / Dr. Medispa)

> Bot: spa thẩm mỹ **Dr. Medispa**. record_bot_id `5f2e12a8-26b8-4007-a95f-f78ae7e99eb3`.
> Mục đích: (1) ghi cấu trúc THẬT, (2) so sánh với xe để CHỨNG MINH luồng domain-neutral.

---

## 5 doc + cấu trúc thật

### spa-1 (18 dịch vụ, có giá) — bảng giá GÓI phân tầng
```
| Dịch vụ | Giá lẻ (VND) | Gói 6 triệu | Gói 7 triệu | Gói 10 triệu |
| CSD Chuyên sâu | 700000 | x | x | x |
```
- `Dịch vụ` = NAME (tên dịch vụ) · `Giá lẻ` = price · `Gói N triệu` = có-trong-gói (x/trống).

### spa-2 (48 entity, 44 giá) — bảng giá chính (2 bảng con)
```
Bảng A:  | STT | Tên dịch vụ | Giá 1 buổi | col4 |         ← chăm sóc da
Bảng B:  | Vùng | Giá buổi lẻ | Giá Combo 10 buổi |         ← triệt lông (Cả chân, Nách, Râu…)
```
- `Tên dịch vụ` = NAME · `Vùng` = category-token NHƯNG body-part cell LÀ tên (ca fix A4).
- ⚠️ `col4` = col_N leak (cột trống không header).

### spa-3 (12 entity, có giá) — triệt lông
```
| STT | Vùng triệt | Giá buổi lẻ | Giá Combo 10 buổi |
| 1 | Mép | 129000 | 899000 |
```
- `Vùng triệt` = category-token, cell ("Mép", "Nách") LÀ tên (ca A4 category-collision).

### spa-4 (26 entity, 0 giá) — KỊCH BẢN CHAT (khác hẳn bảng giá)
```
| col1 | Câu hỏi/ tình huống | Câu trả lời khách | Chú ý |
## Bước 1: Chào khách
| 1 | Khách chào: Hi, xin chào… | … | … |
```
- Đây là **FAQ/flow chat** (Q&A theo bước), KHÔNG phải bảng giá. ⚠️ `col1` = col_N leak.

### spa-00-summary.md — prose tóm tắt.

---

## So sánh xe vs spa — BẰNG CHỨNG domain-neutral

| | xe (Lốp Nam Phát) | spa (Dr. Medispa) |
|---|---|---|
| Cột tên | mã "2-R16 195/55 LPD" (sai) | "Tên dịch vụ"/"Vùng" (đúng sẵn) |
| entity_name gốc | **mã (0/242 tên thật)** ❌ | **tên dịch vụ sạch** ✅ |
| Shape-typing tác dụng | **FIX** mã→tên (187/187) | **GIỮ** tên sạch (44/44) |
| Bug lộ ra | (che) | **category-collision "Vùng"** → đã FIX |
| Có cần re-upload? | CÓ (sửa DSI tại nguồn) | **KHÔNG** (đã đúng) |

→ **Cùng 1 code shape/value, 0 per-bot logic**: xe thì SỬA, spa thì GIỮ. Bug category-collision chỉ lộ khi test spa (xe không có cột category) → fix xong = **thật sự domain-neutral**. Đây KHÔNG phải "support vài bot" — là **chuẩn mindset**.

## Vấn đề spa (khác xe, cần fix riêng — không phải name)
- **col_N leak** (spa-2 `col4`, spa-4 `col1`): cột trống/không-header → tràn stats-index thành attr rác.
- **Shell entity** không giá (spa-4 FAQ 26 entity 0 giá lẫn vào stats).
- Đây là lớp data-quality khác (đã có `stats_serve_require_value` gate), không thuộc A4.
