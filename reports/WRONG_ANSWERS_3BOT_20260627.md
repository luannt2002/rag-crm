# WRONG-ANSWER LOG — 3 bot golden test (2026-06-27)

> Stack: chat=innocom qwen3 · embed=ZeroEntropy zembed-1@1280 · rerank=ZE. Scorer semantic. Stats-index CHƯA re-ingest (extraction cũ).

> Phân loại: ERROR=innocom 503 (provider, không phải lỗi đáp) · MISMATCH=bot trả sai · REFUSE=từ chối/miss · SCORER=thực ra đúng bị chấm sai.


## spa (test-spa-id) — 45/50 = 90%  (5 fail)

Phân bố: {'ANSWERED-MISMATCH': 4, 'REFUSE/miss': 1}  | file: `GOLDEN_test-spa-id_20260629_162115.json`


### 1. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Giá trải nghiệm lần đầu cho dịch vụ Căng bóng da tráng gương kim cương là bao nhiêu?
- **ĐÚNG:** Giá trải nghiệm cho khách hàng lần đầu là 249.000 VNĐ (giá gốc 1.600.0
- **BOT TRẢ:** Dạ, dịch vụ Căng bóng da tráng gương kim cương có giá trải nghiệm lần đầu là 1.500.000 VNĐ ạ.  
Anh/chị muốn em tư vấn đặt lịch trải nghiệm ngay hôm nay không ạ

### 2. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Combo 10 buổi Nâng cơ trẻ hóa Dr. Medi giá bao nhiêu?
- **ĐÚNG:** Giá khuyến mãi là 8.000.000 VNĐ (giá gốc 15.000.000 VNĐ)
- **BOT TRẢ:** Dạ, combo 10 buổi CSD Nâng cơ 7 điểm vàng có giá 7.000.000 VNĐ ạ.  
Anh/chị muốn em hỗ trợ đặt lịch trải nghiệm ngay hôm nay không ạ?

### 3. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Tẩy da chết body và ủ trắng body 60 phút giá bao nhiêu?
- **ĐÚNG:** Giá là 550.000 VNĐ
- **BOT TRẢ:** Dạ, dịch vụ "Tẩy da chết & ủ trắng body" 60 phút có giá **450.000 VNĐ** ạ.  
Anh/chị muốn em hỗ trợ đặt lịch trải nghiệm ngay hôm nay không ạ?

### 4. [REFUSE/miss] sc=1.0 chunks=1
- **HỎI:** Giá lẻ 1 buổi triệt lông vùng Bikini là bao nhiêu?
- **ĐÚNG:** Giá là 499.000 VNĐ
- **BOT TRẢ:** Dạ, hiện tại em chưa có thông tin về giá dịch vụ triệt lông vùng Bikini trong danh mục bên em ạ. Anh/chị vui lòng liên hệ hotline để được tư vấn cụ thể hơn ạ.

### 5. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Triệt râu cho nam combo 10 buổi giá bao nhiêu?
- **ĐÚNG:** Giá là 1.499.000 VNĐ
- **BOT TRẢ:** Combo 10 buổi triệt lông vùng nách cho nam tại Dr. Medispa giá **1.990.000đ** (tiết kiệm 1.000.000đ so với mua lẻ), đang có ưu đãi **mua 10 tặng 1 buổi** ạ.  
A

## xe (chinh-sach-xe) — 19/40 = 48%  (21 fail)

Phân bố: {'ANSWERED-MISMATCH': 10, 'ERROR(innocom-503)': 10, 'REFUSE/miss': 1}  | file: `GOLDEN_chinh-sach-xe_20260629_162234.json`


### 1. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Sản phẩm mã '2-ZR17 215/45 LPD' thuộc dòng lốp nào của Landspider?
- **ĐÚNG:** Thuộc dòng CITYTRAXX H/P
- **BOT TRẢ:** Dạ, mã hàng "2-ZR17 215/45 LPD" không được tìm thấy trong dữ liệu ạ. Anh/chị vui lòng kiểm tra lại mã hàng hoặc cung cấp quy cách lốp khác để em hỗ trợ tra cứu 

### 2. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Chỉ số tải trọng và tốc độ của lốp LANDSPIDER 215/70R16C là bao nhiêu?
- **ĐÚNG:** Chỉ số là 108/106T
- **BOT TRẢ:** Dạ, lốp LANDSPIDER 215/70R16C có chỉ số tải trọng và tốc độ là **100/98R** ạ.

### 3. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Sản phẩm có mã '2-ZR21 275/40 DVT' thuộc thương hiệu lốp nào?
- **ĐÚNG:** Thuộc thương hiệu lốp DAVANTI
- **BOT TRẢ:** Dạ, mã hàng "2-ZR21 275/40 DVT" không được tìm thấy trong dữ liệu ạ. Anh/chị vui lòng kiểm tra lại mã hàng hoặc cung cấp quy cách lốp khác để em hỗ trợ tra cứu 

### 4. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Giá bán của lốp LANDSPIDER 155/80R13 G/P là bao nhiêu?
- **ĐÚNG:** Giá bán là 684,000 VND
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 5. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Số lượng tồn kho hiện tại của mã hàng '2-R14 165/65 LPD' là bao nhiêu?
- **ĐÚNG:** Tồn kho là 404 cái
- **BOT TRẢ:** Dạ, mã hàng "2-R14 165/65 LPD" không được tìm thấy trong dữ liệu ạ. Anh/chị vui lòng kiểm tra lại mã hàng hoặc cung cấp quy cách lốp khác để em hỗ trợ tra cứu c

### 6. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Giá của lốp mã '2-ZR19 255/35 LPD' là bao nhiêu?
- **ĐÚNG:** Giá là 2,160,000 VND
- **BOT TRẢ:** Dạ, mã hàng "2-ZR19 255/35 LPD" không được tìm thấy trong dữ liệu ạ. Anh/chị vui lòng kiểm tra lại mã hàng hoặc cung cấp quy cách lốp khác để em hỗ trợ tra cứu 

### 7. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Lốp mã '2-R16 205/55 LPD' còn tồn bao nhiêu sản phẩm?
- **ĐÚNG:** Còn tồn 780 sản phẩm
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 8. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Sản phẩm '2-R17 225/60 LPD' có giá bán niêm yết là bao nhiêu?
- **ĐÚNG:** Giá bán là 1,602,000 VND
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 9. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Loại lốp Landspider nào có giá bán cao nhất là 3,735,000 VND?
- **ĐÚNG:** Đó là lốp LANDSPIDER 285/45ZR22 H/P (Mã: 2-ZR22 285/45 LPD)
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 10. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Cho biết số lượng tồn kho của lốp Rovelo 155R12C RCMX+?
- **ĐÚNG:** Số lượng tồn kho là 134 cái
- **BOT TRẢ:** Dạ, mã hàng "155R12C RCMX+" không được tìm thấy trong dữ liệu ạ. Anh/chị vui lòng kiểm tra lại quy cách lốp hoặc cung cấp thêm thông tin để em hỗ trợ tra cứu ch

### 11. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Lốp mã '2-R13 175/70 LPD' còn bao nhiêu cái trong kho?
- **ĐÚNG:** Còn tồn 23 cái
- **BOT TRẢ:** Dạ, mã hàng "2-R13 175/70 LPD" không được tìm thấy trong dữ liệu ạ. Anh/chị vui lòng kiểm tra lại mã hàng hoặc cung cấp quy cách lốp khác để em hỗ trợ tra cứu c

### 12. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Chính sách bảo hành của Nam Phát áp dụng cho những thương hiệu nào?
- **ĐÚNG:** Áp dụng cho thương hiệu Landspider (Thailand) và Rovelo (Vietnam)
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 13. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Sau bao nhiêu ngày làm việc thì khách hàng sẽ nhận được kết quả giám định bảo hành?
- **ĐÚNG:** Kết quả được thông báo trong vòng 7 ngày làm việc
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 14. [REFUSE/miss] sc=1.0 chunks=1
- **HỎI:** Số Hotline/Zalo để liên hệ bảo hành là số nào?
- **ĐÚNG:** Số điện thoại là 0988 771 310
- **BOT TRẢ:** Dạ, hiện tại em chưa có thông tin chính xác về số Hotline/Zalo để liên hệ bảo hành ạ. Anh/chị vui lòng để lại thông tin hoặc liên hệ hotline của Nam Phát để đượ

### 15. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Địa chỉ kho lốp Nam Phát tại Hà Nội ở đâu?
- **ĐÚNG:** Tại Kho Hải Ngân, Ngõ 3 Đê Đại Hà, Xóm 10, Yên Mỹ, Thanh Trì, Hà Nội
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 16. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Những sản phẩm dòng CITYTRAXX H/T nào dự kiến về vào ngày 28/11?
- **ĐÚNG:** Các kích thước bao gồm: 215/70R16, 225/60R18, 225/70R16, 235/60R18, 23
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 17. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Date sản xuất (date1) của lốp mã '2-R16 195/65 NEO' là bao nhiêu?
- **ĐÚNG:** Date sản xuất là 26
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 18. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Link thư mục hình ảnh của lốp mã '2-R14 165/60 LPD' là gì?
- **ĐÚNG:** Link là: https://drive.google.com/drive/folders/1e7FaVP5kdpp0QNUETw2uk
- **BOT TRẢ:** Dạ, em là trợ lý tư vấn lốp xe của Nam Phát, chuyên lốp Landspider và Rovelo ạ. Anh/chị cần hỏi về lốp, giá, tồn kho, ngày về hàng hay chính sách bảo hành thì e

### 19. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Date sản xuất ghi nhận của lốp mã '2-R14 185/60 LPD' là bao nhiêu?
- **ĐÚNG:** Date sản xuất là 25
- **BOT TRẢ:** Dạ, em chưa tìm thấy thông tin về ngày sản xuất của mã lốp "2-R14 185/60 LPD" trong dữ liệu hiện có ạ. Anh/chị vui lòng kiểm tra lại mã sản phẩm hoặc cung cấp t

### 20. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Sản phẩm mã '2-ZR17 225/50 LPD' có date sản xuất là bao nhiêu?
- **ĐÚNG:** Date sản xuất là 26
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 21. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Link hình ảnh của lốp LANDSPIDER 155/80R13 G/P nằm ở đâu?
- **ĐÚNG:** Tại đường dẫn: https://drive.google.com/drive/u/1/folders/12ataSHws-Nz
- **BOT TRẢ:** Dạ, em chưa tìm thấy thông tin về link hình ảnh của lốp LANDSPIDER 155/80R13 G/P trong dữ liệu hiện có ạ. Anh/chị vui lòng kiểm tra lại hoặc cho em biết thêm th

## legal (thong-tu-09-2020) — 41/50 = 82%  (9 fail)

Phân bố: {'ERROR(innocom-503)': 3, 'ANSWERED-MISMATCH': 5, 'REFUSE/miss': 1}  | file: `GOLDEN_thong-tu-09-2020-tt-nhnn_20260629_162507.json`


### 1. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Đối tượng áp dụng của Thông tư này bao gồm những đơn vị nào?
- **ĐÚNG:** Áp dụng đối với các tổ chức tín dụng, chi nhánh ngân hàng nước ngoài, 
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 2. [ANSWERED-MISMATCH] sc=1.0 chunks=1
- **HỎI:** Xác thực đa yếu tố (MFA) yêu cầu tối thiểu bao nhiêu yếu tố?
- **ĐÚNG:** Yêu cầu tối thiểu hai yếu tố để chứng minh tính đúng đắn của một danh 
- **BOT TRẢ:** Theo Điều 31 Khoản 1 Thông tư 09/2020/TT-NHNN, yêu cầu tối thiểu về yếu tố xác thực đa yếu tố (MFA) là:

- Phải sử dụng **ít nhất hai yếu tố xác thực** khác nha

### 3. [ANSWERED-MISMATCH] sc=0.957405 chunks=5
- **HỎI:** Quy chế an toàn thông tin của tổ chức phải được rà soát định kỳ bao lâu một lần?
- **ĐÚNG:** Tối thiểu mỗi năm một lần
- **BOT TRẢ:** Theo Điều 42 Khoản 3 điểm c Thông tư 09/2020/TT-NHNN, tổ chức phải thực hiện kiểm tra, đánh giá an toàn thông tin và quản lý rủi ro an toàn thông tin tổng thể t

### 4. [ANSWERED-MISMATCH] sc=0.944012 chunks=6
- **HỎI:** Biện pháp chống thất thoát dữ liệu áp dụng cho hệ thống từ cấp độ mấy?
- **ĐÚNG:** Từ cấp độ 3 trở lên
- **BOT TRẢ:** Theo Điều 8 Khoản 4 Thông tư 09/2020/TT-NHNN, tài sản thông tin trên hệ thống thông tin từ cấp độ 3 trở lên phải áp dụng phương án chống thất thoát dữ liệu.

### 5. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Trung tâm dữ liệu yêu cầu bao nhiêu nguồn điện?
- **ĐÚNG:** Tối thiểu một nguồn điện lưới và một nguồn điện máy phát, có hệ thống 
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_

### 6. [REFUSE/miss] sc=1.0 chunks=1
- **HỎI:** Giao dịch chuyển tiền liên ngân hàng từ bao nhiêu tiền trở lên phải áp dụng MFA tại bước phê duyệt cuối?
- **ĐÚNG:** Từ 100 triệu đồng trở lên
- **BOT TRẢ:** Dựa trên nội dung văn bản được cung cấp, không có thông tin nào quy định cụ thể mức tiền từ bao nhiêu trở lên đối với giao dịch chuyển tiền liên ngân hàng phải 

### 7. [ANSWERED-MISMATCH] sc=0.974043 chunks=5
- **HỎI:** Định kỳ kiểm tra phục hồi dữ liệu sao lưu từ phương tiện lưu trữ ngoài đối với hệ thống cấp độ 3 trở lên là bao lâu?
- **ĐÚNG:** Tối thiểu một năm một lần
- **BOT TRẢ:** Theo Điều 22 Khoản 4 điểm a của Thông tư 09/2020/TT-NHNN, định kỳ kiểm tra, phục hồi dữ liệu sao lưu từ phương tiện lưu trữ ngoài đối với hệ thống thông tin từ 

### 8. [ANSWERED-MISMATCH] sc=0.947225 chunks=5
- **HỎI:** Tần suất kiểm tra, đánh giá an toàn thông tin đối với hệ thống cấp độ 4 là bao lâu?
- **ĐÚNG:** Tối thiểu một năm một lần
- **BOT TRẢ:** Theo Điều 42 Khoản 3 điểm b của Thông tư 09/2020/TT-NHNN, tần suất kiểm tra, đánh giá an toàn thông tin đối với hệ thống thông tin cấp độ 4 là **một năm một lần

### 9. [ERROR(innocom-503)] sc=None chunks=None
- **HỎI:** Thời hạn thông báo kết quả giám định bảo hành lốp xe (trong file Chính sách bảo hành lốp)?
- **ĐÚNG:** Kết quả được thông báo trong vòng 7 ngày làm việc
- **BOT TRẢ:** (EMPTY/error)
- _err: HTTP 503_