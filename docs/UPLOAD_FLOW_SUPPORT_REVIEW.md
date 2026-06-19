# Luồng Upload tài liệu — Đầu vào / Đầu ra từng bước

> Cho Technical Support. Mô tả **phương pháp** (không code): input 1 tài liệu → mỗi bước biến đổi thành gì. Cập nhật 2026-06-19.

## Tóm tắt
Upload = **nhận nhanh rồi xử lý ngầm**. Người dùng gửi link/file → hệ thống nhận, trả `202` ngay (không bắt chờ) → worker chạy ngầm **8 bước** biến tài liệu thành các "mẩu" (chunk) có vector để bot tìm kiếm.

```
Gửi link/file ─► [B0 Nhận + 202] ─► (event) ─► worker: B1→B7 xử lý ─► state = active (xài được)
```

## Từng bước: vào gì → làm gì → ra gì

| Bước | Đầu vào | Phương pháp (làm gì) | Đầu ra |
|---|---|---|---|
| **B0. Nhận** (đồng bộ, trả 202) | link Google Doc/Sheet hoặc text | validate link → **tải text thô về ngay** → lưu DB `state=DRAFT` (kèm text thô) → bắn 1 event | 1 dòng `documents` (DRAFT, chưa có chunk) + trả **HTTP 202 "đã nhận"** |
| **B1. VALIDATE** | text thô | chặn nếu **quá lớn** (mặc định ~500k ký tự) hoặc **trùng** (so `content_hash`) | text hợp lệ (hoặc trả 400/413) |
| **B2. PARSE** | text/file thô | đọc cấu trúc: Kreuzberg (OCR/PDF), openpyxl (Excel), Google Sheets, Markdown. **Đọc lại text từ DB, không tải link lần 2** | text **có cấu trúc** (bảng, tiêu đề, đoạn) |
| **B3. CLEAN** | text có cấu trúc | chuẩn hoá unicode (NFC) + bỏ ký tự rác/nối từ + **chặn câu lệnh chèn độc (prompt-injection)** | text **sạch** |
| **B4. CHUNK** ⭐ | text sạch | **AdapChunk**: đo cấu trúc tài liệu → chọn cách cắt phù hợp (Bảng → **mỗi dòng 1 mẩu** + tiêu đề cột; Văn bản → cắt theo ý/tiêu đề; Văn bản luật → **mỗi Điều/Khoản 1 mẩu**). Cắt **2 tầng**: mẩu lớn (parent) + mẩu nhỏ (child) | **danh sách chunk** (mẩu nhỏ) |
| **B5. ENRICH** | mỗi chunk | AI (`gpt-4.1-mini`) thêm **1 câu mô tả ngữ cảnh** vào đầu mỗi mẩu (để search trúng hơn) | chunk = **[câu context] + [text gốc]** |
| **B6. TÁCH TỪ (VN)** | chunk tiếng Việt | tách từ ghép (underthesea) phục vụ tìm theo từ khoá | text đã tách từ |
| **B7. EMBED + LƯU** | các child chunk | gọi **Jina** đổi mỗi mẩu thành **vector 1024 số** → lưu bảng `document_chunks` (vector + text). *Parent KHÔNG embed — chỉ dùng để mở rộng lúc trả lời* | chunk **có vector → tìm kiếm được** |
| **B8. CHỐT** | toàn bộ chunk | mọi child đã có vector? | **`active`** ✅ (bot xài được) / **`failed`** ❌ |

## Ví dụ thật (file Thông tư 09/2020 vừa upload)

**Đầu vào:** 1 link Google Doc văn bản luật.
**Đầu ra:** `state=active`, **576 chunk** (489 child có vector + 87 parent không vector).

**1 chunk thật trông như này** (thấy rõ B5 thêm câu context ở đầu):
```
[câu context AI thêm]  Đoạn đầu của tài liệu, giới thiệu về Thông tư số 09/2020/TT-NHNN
                        ban hành ngày 21/10/2020 về an toàn hệ thống thông tin trong ngân hàng.
[text gốc]             NGÂN HÀNG NHÀ NƯỚC VIỆT NAM ... Căn cứ Luật Các tổ chức tín dụng ...
```
→ mỗi mẩu ~300–400 ký tự, gọn đúng 1 ý.

## Cách CHECK chunk có "cùi bắp" không (anh Luân hỏi)

1. **Xem chunk thật** (psql):
   ```sql
   SELECT chunk_type, left(content,150) FROM document_chunks dc
   JOIN documents d ON d.id=dc.record_document_id JOIN bots b ON b.id=d.record_bot_id
   WHERE b.bot_id='<bot>' ORDER BY chunk_index LIMIT 20;
   ```
2. **Số chunk/tài liệu có hợp lý không?** Vài chục–vài trăm = ổn. Nếu **1 tài liệu ra hàng NGHÌN chunk** = tài liệu quá to hoặc parse lỗi (ví dụ phiên này: file `xe-3` 1 sheet → **2643 chunk** = bất thường, cần tách nhỏ).
3. **Cờ cảnh báo trong log** lúc ingest:
   - `ingestion_validation_issues` — báo chunk **quá ngắn** (1–2 ký tự) hoặc **trùng nhau** (near-duplicate).
   - `chunk_quality_below_threshold` — mẩu có điểm liên quan thấp lúc tìm kiếm.

## ⚠️ Vấn đề đã biết
Tài liệu **quá lớn** (1 Sheet khổng lồ) → B4 nổ ra **hàng nghìn chunk** → B7 gọi Jina embed quá nhiều → **đụng trần token/phút (Jina ~100k tok/phút)** → ingest rất chậm, có thể **kẹt ở DRAFT** (không xong). Hướng xử lý: tách nhỏ tài liệu trước khi upload / nâng gói Jina / tách riêng việc embed nền khỏi luồng chat.

> Cần code chi tiết bước nào, báo em trích đúng đoạn. File này chỉ mô tả phương pháp.
