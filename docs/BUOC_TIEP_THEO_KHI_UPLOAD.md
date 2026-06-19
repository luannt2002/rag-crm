# Bước tiếp theo sẽ làm gì khi file được upload?

> Trả lời đúng 1 câu hỏi: sau khi file được upload, hệ thống xử lý tiếp như thế nào. 2026-06-19.

## Ngay lúc upload xong
File mới chỉ ở trạng thái `DRAFT`: hệ thống đã lưu **text thô** (`raw_content`), **CHƯA** cắt mẩu, **CHƯA** có vector. Bắn 1 event rồi **trả HTTP 202 ngay** (không xử lý tiếp trong request đó).

Ví dụ text thô lúc này (Thông tư 09/2020):
```
NGÂN HÀNG NHÀ NƯỚC VIỆT NAM   Số: 09/2020/TT-NHNN
CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM ...   (1 khối text dài liền mạch)
```

## BƯỚC TIẾP THEO = worker chạy ngầm
Một worker nhận event → biến text thô thành **các mẩu nhỏ có vector** để bot tìm kiếm, lần lượt 7 bước:

| # | Bước | Đầu vào | Đầu ra |
|---|---|---|---|
| 1 | **PARSE** | text thô | text có cấu trúc (nhận ra bảng / heading / đoạn) |
| 2 | **CLEAN** | text cấu trúc | text sạch (chuẩn unicode, bỏ rác) |
| 3 | **CHUNK** ⭐ | text sạch | cắt thành nhiều mẩu nhỏ (văn bản luật → mỗi Điều 1 mẩu) |
| 4 | **ENRICH** | mỗi mẩu | mẩu + 1 câu context do AI (`gpt-4.1-mini`) thêm |
| 5 | **TÁCH TỪ (VN)** | mẩu tiếng Việt | text đã tách từ (cho tìm từ khoá) |
| 6 | **EMBED + LƯU** | mẩu | **vector 1024 số** (Jina) → lưu bảng `document_chunks` |
| 7 | **CHỐT** | toàn bộ mẩu | `state: DRAFT → active` ✅ (bot dùng được) |

## Cụ thể: Đầu vào → Đầu ra (file Thông tư 09/2020)
- **Đầu vào:** 1 khối text thô dài liền mạch (như trên).
- **Đầu ra:** **576 mẩu**, mỗi mẩu = `[câu context AI] + [text gốc 1 ý]`, có vector → search được.
- 1 mẩu thật:
```
[context]  Đoạn đầu, giới thiệu Thông tư 09/2020/TT-NHNN về an toàn thông tin ngân hàng.
[gốc]      NGÂN HÀNG NHÀ NƯỚC VIỆT NAM ... Căn cứ Luật Các tổ chức tín dụng ...
```

## Tóm 1 câu
Sau khi upload (DRAFT), **bước tiếp theo là worker cắt text thô thành các mẩu nhỏ + gắn vector**, xong thì lật trạng thái sang `active` để bot tìm kiếm / trả lời được.
