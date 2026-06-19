# Output khi upload file PDF — `1111111_TT09.pdf`

> Chạy file PDF thật qua **parser + chunker của hệ thống** (bước U2 PARSE + U4 CHUNK).
> *(U5 enrich + U7 embed bỏ qua trong demo này vì OpenAI/Jina đang rate-limit; đây là phần cốt lõi 'PDF → text → mẩu'.)* 2026-06-19.

## 1. Đầu vào
- File: `1111111_TT09.pdf` — **514 KB** (526,479 bytes)

## 2. Sau bước PARSE (PDF → text)
- Số trang đọc được (có chữ): **38**
- Tổng ký tự text trích ra: **80,223**
- Text trích ra (300 ký tự đầu):
```
## Page 1  NGÂN HÀNG NHÀ NƯỚC VIỆT NAM Số: 09/2020/TT-NHNN CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM Độc lập – Tự do – Hạnh phúc Hà Nội, ngày 21 tháng 10 năm 2020 THÔNG TƯ Quy định về an toàn hệ thống thông tin trong hoạt động ngân hàng Căn cứ Luật Ngân hàng Nhà nước Việt Nam ngày 16 tháng 6 năm 20
```

## 3. Sau bước CHUNK (text → các mẩu)
- Cấu trúc tài liệu (profile): `{'heading_counts': {'h1': 0, 'h2': 38, 'h3': 0}, 'total_headings': 38, 'table_count': 103, 'avg_text_length': 12.263840830449826, 'mixed_content_score': 0.07941403238242097, 'total_words': 14177, 'has_toc': False, 'is_csv_format': False, 'vn_hierarchical_markers': 74, 'formula_count': 0, 'image_count': 0, 'code_block_count': 0, 'heading_ratio': 0.27, 'total_blocks_estimated': 141}`
- Chiến lược cắt được chọn: **hdt** (confidence 1.0)
- **Tổng số mẩu (chunk) tạo ra: 112**

### 3 mẩu đầu tiên (output thật)
**Mẩu #1** (1176 ký tự) — headings: ['Page 1']
```
[Page 1] NGÂN HÀNG NHÀ NƯỚC VIỆT NAM Số: 09/2020/TT-NHNN CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM Độc lập – Tự do – Hạnh phúc Hà Nội, ngày 21 tháng 10 năm 2020 THÔNG TƯ Quy định về an toàn hệ thống thông tin trong hoạt động ngân hàng Căn cứ Luật Ngân hàng Nhà nước Việt Nam ngày 16 tháng 6 năm 2010;  Căn cứ Luật Các tổ chức tín dụng ngày 16 tháng 6 năm 2010 và Luật sửa đổi,  bổ sung một số điều
```
**Mẩu #2** (25 ký tự) — headings: ['Chương 1']
```
[Chương 1] QUY ĐỊNH CHUNG
```
**Mẩu #3** (655 ký tự) — headings: ['Chương 1', 'Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng']
```
[Chương 1 > Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng] 1. Thông tư này quy định những yêu cầu tối thiểu về bảo đảm an toàn hệ thống  thông tin trong hoạt động ngân hàng. 2. Thông tư này áp dụng đối với các tổ chức tín dụng, chi nhánh ngân hàng  nước ngoài, các tổ chức cung ứng dịch vụ trung gian thanh toán, công ty thông tin  tín dụng, Công ty Cổ phần Thanh toán Quốc gia Việt Nam, Công t
```

## 4. Tóm tắt luồng
`PDF 514KB` → **PARSE** → `80,223 ký tự / 38 trang` → **CHUNK (hdt)** → **112 mẩu** → (bước tiếp: ENRICH thêm context + EMBED thành vector → lưu `document_chunks` → state `active`).
