# Tóm tắt & hướng dẫn đọc dữ liệu — bot xe

## ⚙️ Cấu trúc dữ liệu (schema 3 bảng) — cột nào nghĩa gì

Dữ liệu gồm 3 bảng. Mỗi DÒNG = 1 sản phẩm/bản ghi. Đối chiếu giá trị theo ĐÚNG thứ tự cột; không lấy số của dòng khác. Cột trống cuối hoặc cột tổng ("Tổng LAND/ROVELO") không phải dữ liệu sản phẩm. Placeholder `col_N` = ô tiêu đề trống → bỏ qua.

**Bảng GIÁ & TỒN (bảng chính)** — thứ tự cột:
`question` (các cách viết quy cách lốp, ngăn bởi dấu phẩy) · `code` (mã hàng, vd "2-R13 155/80 LPD") · `productname` (tên đầy đủ) · `answer` (tên ngắn) · `quantity` (tồn kho) · `price` (giá/lốp, VND) · `date1` · `date2` · `image` (link ảnh).
→ **quantity ĐỨNG TRƯỚC price**. Khớp quy cách ở question / code / productname / answer đều coi như tìm thấy.

**Bảng KHO theo nhà kho** — thứ tự cột:
`thể loại` · `Tên kho` · `Mã hàng` · `Tên hàng` · `date1` (ngày sản xuất) · `date2` · `hình ảnh1` / `ẢNH 1` / `ẢNH 2` / `Ảnh 3` (link ảnh).

**Bảng LỊCH VỀ HÀNG** — thứ tự cột:
`Marks` · `Cargo description` (mô tả/quy cách lốp) · `Ngày về` (ngày dự kiến hàng về).

## Tổng quan corpus

~192 sản phẩm thuộc 3 kho: **LANDSPIDER**, **ROVELO**, kho lốp các loại. Khoảng giá ~630.000đ – 3.735.000đ. Thương hiệu chính: Landspider (CITYTRAXX G/P · H/P · H/T, WILDTRAXX A/T, ROVERTRAXX X/T), Rovelo (RHP-A68, RCM-X+, INSTINCT, Road quest A/T), Davanti (DX640). Có bảng lịch về hàng (NGÀY VỀ) và tài liệu chính sách bảo hành.

> Chi tiết từng sản phẩm (giá, tồn, ngày, quy cách, ảnh) nằm trong dữ liệu bảng — tra theo mã hàng / quy cách, đối chiếu cột theo schema ở trên. Tài liệu này chỉ giải thích cấu trúc, không liệt kê lại toàn bộ sản phẩm.
