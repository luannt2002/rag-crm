# Tóm tắt & hướng dẫn đọc dữ liệu — bot xe

## ⚙️ Cấu trúc dữ liệu (schema các bảng) — cột nào nghĩa gì

Dữ liệu gồm 3 bảng. Mỗi DÒNG = 1 sản phẩm/bản ghi; cột số-thứ-tự đầu thường để trống. Một dòng dữ liệu có thể không nằm cùng đoạn với dòng tiêu đề — đối chiếu vị trí cột với schema dưới đây để biết mỗi giá trị thuộc cột nào (không lấy số của dòng khác).

**Bảng GIÁ & TỒN** — cột theo thứ tự:
`Tên` (tên sản phẩm) · `Giá` (giá bán, VNĐ) · `Mã` (mã hàng) · `Số lượng` (tồn kho, cái) · `Ngày` · `Ảnh` (link ảnh) · `Aliases` (các cách viết khác của quy cách — chỉ để tìm kiếm, KHÔNG phải thuộc tính sản phẩm).

**Bảng KHO theo nhà kho** — cột theo thứ tự:
`(STT)` · `Tên kho` · `Mã hàng` · `Tên hàng` · `date1` (ngày sản xuất) · `date2` · `hình ảnh1` / `ẢNH 1` / `ẢNH 2` / `Ảnh 3` (các link ảnh). Nếu thấy placeholder `col1`, `col5`, `col6`… nghĩa là ô tiêu đề gốc bị để trống — dùng tên cột ở trên, đừng coi placeholder là dữ liệu.

**Bảng LỊCH VỀ HÀNG** — cột theo thứ tự:
`Marks` · `Cargo description` (mô tả/quy cách lốp) · `Ngày về` (ngày dự kiến về hàng).

## Tổng quan corpus

Tổng **192** sản phẩm thuộc **3 kho**: Kho lốp LANDSPIDER (123), Kho lốp ROVELO (69), Kho lốp các loại (31). Khoảng giá **630.000đ – 3.735.000đ**. Các thương hiệu chính: **Landspider** (dòng CITYTRAXX G/P · H/P · H/T, WILDTRAXX A/T, ROVERTRAXX X/T), **Rovelo** (RHP-A68, RCM-X+, INSTINCT, Road quest A/T), **Davanti** (DX640).

> Chi tiết từng sản phẩm (giá, tồn kho, ngày, quy cách, hình ảnh) nằm trong dữ liệu bảng — tra theo mã hàng / quy cách lốp, đối chiếu cột theo schema ở trên. Tài liệu này chỉ giải thích cấu trúc, không liệt kê lại toàn bộ sản phẩm.
