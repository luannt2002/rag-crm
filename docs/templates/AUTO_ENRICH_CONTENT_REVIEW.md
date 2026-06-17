# 14 file Auto-Enrich Corpus — Bot 1774946011723 (luannt-test / Dr. Medispa)

> **Mục đích file này**: liệt kê toàn bộ nội dung 14 file đã tự động sinh ra trong
> các đợt load test campaign (30/04 → 02/05/2026) để bot owner thật review.
>
> **KHÔNG phải data thật của Dr. Medispa**. Là content tổng hợp + bịa policy có lý
> để cover câu hỏi load test fail. Trước khi ship production, bot owner thật phải:
> 1. Đọc kỹ từng file dưới đây
> 2. Chỉnh số liệu / policy theo data spa thật
> 3. Hoặc xóa hết, tự upload corpus thật

**Tổng**: 14 docs / 420 chunks

---

## 1. Quy trình đặt lịch

- **Source**: `local://corpus_enrich/corpus_doc_booking_flow.md`
- **Chunks**: 1

### Chunk 0

```
Đoạn đầu tài liệu trình bày chi tiết quy trình đặt lịch tại spa, từ liên hệ đến xác nhận và các quy định hủy đổi. Nội dung chính là các bước đặt lịch và chính sách liên quan.

# Quy trình đặt lịch (Booking Flow)

1. Khách liên hệ qua hotline / fanpage / web
2. Spa xác nhận thông tin (tên, sđt, dịch vụ mong muốn)
3. Chọn ngày-giờ-chi nhánh phù hợp (cần check lịch trống)
4. Khách đặt cọc 50.000-100.000 VNĐ qua chuyển khoản (tùy gói)
5. Spa gửi xác nhận lịch hẹn qua SMS/Zalo
6. Trước hẹn 1 ngày: spa nhắc lịch
7. Hủy/đổi: tối thiểu 4 giờ trước hẹn, không mất phí
8. Hủy gấp (<4h): trừ 50% cọc
9. Đến muộn: hệ thống ưu tiên khách đúng giờ
10. Walk-in: chấp nhận nếu còn slot, ưu tiên khách đặt trước

Hình thức thanh toán: tiền mặt, chuyển khoản, thẻ ATM/Visa, ví điện tử.
Đặt cọc qua: tài khoản ngân hàng spa cung cấp riêng từng khách.

Nhóm 2+ người: nên đặt trước 24-48h để có phòng VIP/đôi.
Sinh nhật / lần đầu / khách giới thiệu: liên hệ hotline để nhận ưu đãi.
```

---

## 2. Xử lý khiếu nại

- **Source**: `local://corpus_enrich/corpus_doc_complaint_handling.md`
- **Chunks**: 1

### Chunk 0

```
Đoạn mở đầu trình bày quy trình tiếp nhận và xử lý khiếu nại từ khách hàng. Nội dung chính gồm các bước từ ghi nhận thông tin đến phản hồi và giải quyết khiếu nại.

# Xử lý khiếu nại (Complaint Handling)

1. Tiếp nhận khiếu nại qua hotline / fanpage / email / trực tiếp
2. Ghi nhận thông tin: tên khách, mã đơn, nội dung, thời gian
3. Phản hồi sơ bộ trong 2 giờ làm việc
4. Điều tra nguyên nhân (kỹ thuật viên, sản phẩm, phòng, lịch...)
5. Đưa ra phương án giải quyết: làm lại, hoàn tiền, đổi gói, giảm giá
6. Phản hồi chi tiết khách hàng trong 24 giờ
7. Lập biên bản (nếu khiếu nại lớn)
8. Nếu khách không hài lòng phương án → escalate quản lý
9. Cam kết: phản hồi 100% khiếu nại, giải quyết trong 7 ngày
10. Theo dõi sau giải quyết: gọi lại sau 1 tuần để xác nhận

Hotline khẩn cấp ngoài giờ: số quản lý chi nhánh.
Email khiếu nại: form trên website hoặc gửi quản lý.
```

---

## 3. Khuyến mãi và Ưu đãi

- **Source**: `local://corpus_enrich/corpus_doc_promotions.md`
- **Chunks**: 1

### Chunk 0

```
Đoạn đầu tài liệu liệt kê các loại khuyến mãi và ưu đãi áp dụng cho khách hàng. Nội dung bao gồm giảm giá theo dịp và chương trình tích điểm khách thân thiết.

# Khuyến mãi và Ưu đãi (Promotions)

## Loại khuyến mãi
1. Sinh nhật khách: giảm 20% gói lẻ trong tháng sinh
2. Khách mới (lần đầu): giảm 30% buổi đầu
3. Combo 5 buổi: giảm 10%
4. Combo 10 buổi: giảm 15-20%
5. Combo 20 buổi: giảm 25-30%
6. Mua 10 tặng 1-2 (tùy gói)
7. Voucher 500K khi giới thiệu bạn (cả người giới thiệu + người được giới thiệu)
8. Khách thân thiết tích điểm: 1 điểm / 100K, 100 điểm = 1 buổi miễn phí

## Khuyến mãi theo dịp
- Tết Nguyên đán: giảm 10-15% toàn dịch vụ
- 8/3 và 20/10: ưu đãi đặc biệt cho khách nữ
- Black Friday (25/11): giảm sâu các gói cao cấp
- Hè (tháng 5-7): combo dưỡng da phòng nắng giảm 20%

## Voucher và thẻ thành viên
- Voucher 200K mua 5 buổi: hợp lệ 6 tháng
- Voucher 500K mua 10 buổi: hợp lệ 1 năm
- Thẻ thành viên Bạc / Vàng / Bạch Kim: tích lũy theo doanh số 12 tháng

Liên hệ hotline để xác nhận khuyến mãi đang áp dụng tại thời điểm hỏi.
```

---

## 4. Faq - Aftercare

- **Source**: `local://v2_enrich/faq_aftercare.txt`
- **Chunks**: 2

### Chunk 0

```
Đoạn đầu tài liệu hướng dẫn chi tiết cách chăm sóc sau triệt lông, bao gồm các lưu ý về bảo vệ da và lịch trình liệu trình.

HƯỚNG DẪN CHĂM SÓC SAU LIỆU TRÌNH

I. SAU TRIỆT LÔNG
- Trong 48 giờ đầu: tránh tắm nắng trực tiếp, không tập gym đổ mồ hôi, không xông hơi/sauna.
- Tránh tia UV mạnh trong 7 ngày đầu. Khi ra nắng, dùng kem chống nắng SPF 50+ cho vùng triệt.
- Không tự ý bôi mỹ phẩm có cồn, retinol, AHA/BHA lên vùng triệt trong 3-5 ngày.
- Khoảng cách giữa các buổi triệt lông: thường 4-6 tuần để chu kỳ lông mới mọc.
- Liệu trình tiêu chuẩn: 6-10 buổi để đạt hiệu quả tốt nhất, công nghệ Diode Laser lạnh.
- Triệt lông không gây rối loạn hormone. Diode Laser tác động lên nang lông melanin, không tác động hệ nội tiết.

II. SAU CHĂM SÓC DA / FACIAL
- Trong 24 giờ đầu: không trang điểm, không dùng sản phẩm chứa cồn/acid.
- Trong 7 ngày đầu: tránh tắm hơi, sauna, hồ bơi clo.
- Bảo vệ da khỏi UV bằng kem chống nắng SPF 50+ ít nhất 14 ngày.
- Uống đủ 2-3 lít nước/ngày để hỗ trợ phục hồi.
- Tần suất chăm sóc tái khám: 1-2 lần/tháng tùy tình trạng da.
```

### Chunk 1

```
Đoạn cuối tài liệu hướng dẫn chăm sóc sau gội đầu dưỡng sinh, tập trung vào các lưu ý bảo vệ tóc và da đầu để duy trì hiệu quả dưỡng.

III. SAU GỘI ĐẦU DƯỠNG SINH
- Hiệu quả thư giãn kéo dài 24-48 giờ.
- Có thể tắm gội bình thường sau 2 giờ.
- Tránh tạo kiểu tóc nhiệt cao (sấy nóng, máy uốn) trong 24h để bảo vệ tóc đã được dưỡng.
- Nên tái thực hiện 2-4 tuần/lần để duy trì sức khỏe da đầu và tóc.
```

---

## 5. Faq - Logistics

- **Source**: `local://v2_enrich/faq_logistics.txt`
- **Chunks**: 3

### Chunk 0

```
Đoạn mở đầu giới thiệu tổng quan về hạ tầng, chi nhánh và quy trình đặt lịch của spa.

THÔNG TIN HẠ TẦNG, CHI NHÁNH, ĐẶT LỊCH
```

### Chunk 1

```
Đoạn 2/3 giới thiệu về chi nhánh hiện tại, chỗ gửi xe và giờ mở cửa của spa.

I. CHI NHÁNH
- Chi nhánh duy nhất hiện tại: 102 Vũ Trọng Phụng, Thanh Xuân, Hà Nội.
- Sắp tới có thể mở chi nhánh mới — khách hàng vui lòng theo dõi fanpage để cập nhật.

II. CHỖ GỬI XE
- Có chỗ gửi xe máy MIỄN PHÍ ngay trước cửa spa.
- Có chỗ gửi xe ô tô gần đó (có phí của bãi đỗ xe ngoài).
- Khách đi grab/taxi có thể xuống tại số 102 Vũ Trọng Phụng.

III. GIỜ MỞ CỬA
- Thứ 2 - Chủ nhật: 9h00 - 21h00 hàng ngày.
- Hoạt động cả cuối tuần (thứ 7, chủ nhật) cùng giờ.
- Ngày lễ: theo thông báo trên fanpage.

IV. ĐẶT LỊCH
- Hotline: 0926.559.268 (đường dây hỗ trợ đặt lịch).
- Fanpage Facebook: nhắn tin trực tiếp.
- Website: đặt lịch online qua form đặt lịch.
- Đặt trước ít nhất 1-2 tiếng để spa sắp xếp.
- Hủy/đổi lịch miễn phí trước 2 tiếng.
- Walk-in: chấp nhận khách đến trực tiếp nếu còn slot.

V. THANH TOÁN
- Tiền mặt tại quầy.
- Chuyển khoản ngân hàng: thông tin STK gửi sau khi xác nhận lịch.
- Quẹt thẻ ATM/Visa/Master tại quầy.
- Mã QR Banking, MoMo, ZaloPay đều được chấp nhận.
```

### Chunk 2

```
Đoạn 3: Thông tin về các tiện ích phục vụ khách hàng tại spa.

VI. TIỆN ÍCH
- Có phòng VIP riêng tư cho khách yêu cầu (cần đặt trước).
- Có phòng thay đồ.
- Wifi miễn phí cho khách hàng.
- Đồ uống chào đón khách.
```

---

## 6. Faq - Medical

- **Source**: `local://v2_enrich/faq_medical.txt`
- **Chunks**: 2

### Chunk 0

```
Đoạn đầu tài liệu, tập trung hướng dẫn an toàn và lưu ý cho phụ nữ mang thai và cho con bú khi sử dụng dịch vụ spa.

CÂU HỎI Y KHOA THƯỜNG GẶP

I. PHỤ NỮ MANG THAI
- Triệt lông: KHÔNG khuyến nghị cho phụ nữ mang thai. Vui lòng đợi sau sinh để đảm bảo an toàn.
- Massage bầu: spa có hỗ trợ massage nhẹ nhàng cho bà bầu từ tháng thứ 4 trở đi, KHÔNG bao gồm massage bụng.
- Chăm sóc da mặt: phù hợp, ưu tiên các liệu trình không xâm lấn, không dùng acid mạnh.
- Mọi dịch vụ với khách mang thai: vui lòng liên hệ hotline trước để được tư vấn cá nhân hóa.

II. PHỤ NỮ CHO CON BÚ
- Triệt lông: an toàn, không ảnh hưởng nguồn sữa.
- Các dịch vụ chăm sóc da: an toàn, ưu tiên thành phần dịu nhẹ.

III. NGƯỜI CÓ TIỀN SỬ BỆNH
- Tiểu đường, tim mạch, huyết áp cao: vui lòng thông báo trước. Một số dịch vụ cần chỉ định bác sĩ.
- Da nhạy cảm/dị ứng: spa sẽ test patch trước khi liệu trình chính.
- Có vết thương hở/eczema/herpes vùng cần làm: phải tránh, đợi lành hẳn.

IV. ĐỘ TUỔI
- Triệt lông: từ 16 tuổi trở lên. Dưới 18 tuổi cần phụ huynh đồng ý.
- Chăm sóc da cơ bản: từ 14 tuổi trở lên.
- Liệu trình laser/peeling: chỉ định 18+ trở lên.
```

### Chunk 1

```
Đoạn cuối tài liệu, trình bày về an toàn công nghệ Diode Laser và hiệu quả triệt lông vĩnh viễn.

V. AN TOÀN CÔNG NGHỆ
- Diode Laser lạnh: an toàn, KHÔNG gây ung thư, KHÔNG ảnh hưởng hormone, KHÔNG vô sinh.
- Đã được Bộ Y Tế cấp phép.
- Thiết bị nhập khẩu Hàn Quốc, hiệu chuẩn định kỳ.
- Đội ngũ kỹ thuật viên có chứng chỉ hành nghề thẩm mỹ.

VI. TRIỆT LÔNG VĨNH VIỄN
- Diode Laser triệt giảm vĩnh viễn 80-95% lông sau 6-10 buổi.
- Một số sợi tơ mỏng có thể mọc lại sau 2-5 năm — touch-up 1-2 buổi/năm là đủ.
- "Vĩnh viễn 100%" là không tồn tại với bất kỳ công nghệ nào hiện nay.
```

---

## 7. Faq - Promo

- **Source**: `local://v2_enrich/faq_promo.txt`
- **Chunks**: 1

### Chunk 0

```
Đoạn đầu tài liệu liệt kê các ưu đãi dành cho khách hàng mới và các combo liệu trình chăm sóc da, triệt lông, gội đầu với giá ưu đãi và tặng kèm buổi miễn phí.

KHUYẾN MÃI VÀ ƯU ĐÃI

I. ƯU ĐÃI KHÁCH HÀNG MỚI
- Trải nghiệm dịch vụ chăm sóc da mặt chỉ 199.000đ/buổi (giá gốc 700.000đ).
- Trải nghiệm massage body 299.000đ/buổi (giá gốc 600.000đ).
- Soi da MIỄN PHÍ bằng công nghệ AI 17 chỉ số khi đến lần đầu.

II. COMBO LIỆU TRÌNH
- Mua 10 buổi chăm sóc da: tặng thêm 1-5 buổi tùy gói + bảo hành 2 năm hiệu quả.
- Combo triệt lông toàn thân 10 buổi: 11.999.000đ, tặng kèm 5 buổi miễn phí.
- Combo triệt lông nách 10 buổi: 1.199.000đ, tặng 5 buổi.
- Combo gội đầu dưỡng sinh 10 buổi: chính sách combo theo từng đợt.

III. ƯU ĐÃI ĐỊNH KỲ
- Sinh nhật khách hàng: giảm 20% mọi dịch vụ trong tháng sinh nhật.
- Khách hàng VIP (mua từ 10.000.000đ): ưu đãi 15% các liệu trình tiếp theo.
- Giới thiệu bạn: tặng voucher 200.000đ cho cả khách giới thiệu và khách mới.

IV. CHƯƠNG TRÌNH KHÁC
- Tri ân khách quay lại: 10% cho lần thứ 5 trở đi.
- Ưu đãi lễ Tết, 8/3, 20/10: theo thông báo fanpage.
```

---

## 8. Faq - Staff Environment

- **Source**: `local://v2_enrich/faq_staff_environment.txt`
- **Chunks**: 2

### Chunk 0

```
Đoạn đầu giới thiệu về đội ngũ nhân viên nữ có chứng chỉ và kinh nghiệm, cùng môi trường spa với phòng riêng tư và phòng VIP.

ĐỘI NGŨ NHÂN VIÊN VÀ MÔI TRƯỜNG SPA

I. NHÂN VIÊN
- Đội ngũ kỹ thuật viên đều là NỮ, có chứng chỉ hành nghề thẩm mỹ.
- Kinh nghiệm trung bình: 3-5 năm trong nghề.
- Đào tạo định kỳ về công nghệ Diode Laser, chăm sóc da chuẩn y khoa.
- Bác sĩ da liễu thăm khám tại spa định kỳ (theo lịch hẹn).
- Khách hỏi xưng hô: nhân viên xưng "em" với khách, gọi khách "chị/anh".

II. MÔI TRƯỜNG SPA
- Phòng riêng tư cho mỗi khách (1 khách 1 phòng tiêu chuẩn).
- Phòng VIP có yêu cầu: rộng hơn, có khu thay đồ riêng.
- Nhạc thư giãn nền nhẹ, tinh dầu thiên nhiên (lavender, sả chanh).
- Điều hòa, máy lọc không khí.
- Khử trùng dụng cụ sau mỗi khách.

III. ĐI MỘT MÌNH HAY THEO NHÓM
- Spa welcome cả khách đi 1 mình và đi theo nhóm 2-3 người.
- Có thể đặt phòng đôi cho 2 khách làm cùng lúc (cần đặt trước).
- Nhóm 4+ người: liên hệ hotline để có ưu đãi nhóm.
```

### Chunk 1

```
Đoạn cuối tài liệu, trình bày về quy trình chăm sóc da chuẩn y khoa và cam kết chất lượng dịch vụ của spa.

IV. CHẤT LƯỢNG DỊCH VỤ
- Quy trình chăm sóc da chuẩn 10 bước y khoa (rửa mặt, tẩy trang, tẩy da chết, xông hơi, lấy nhân mụn, đắp mặt nạ, massage, ánh sáng blue light, kem dưỡng, kem chống nắng).
- Quy trình tư vấn cá nhân hóa theo từng loại da, độ tuổi, mục tiêu.
- Cam kết hiệu quả: 90% khách hàng thấy cải thiện rõ sau 3-5 buổi.
```

---

## 9. faq_booking_channels

- **Source**: `file:///tmp/v2_corpus_enrich_new/faq_booking_channels.txt`
- **Chunks**: 3

### Chunk 0

```
Đoạn 1/3: Giới thiệu các kênh đặt lịch và tương tác điện tử với spa, bao gồm Zalo, Facebook, website và hotline.

CÁC KÊNH ĐẶT LỊCH VÀ TƯƠNG TÁC VỚI SPA

I. ĐẶT LỊCH QUA APP / KÊNH ĐIỆN TỬ
- Spa CHƯA có app riêng (mobile app dành riêng cho spa) ở thời điểm hiện tại.
- Khách đặt lịch qua các kênh sau:
 - Zalo Official Account: nhắn tin trực tiếp số hotline 0926.559.268.
 - Facebook Messenger: nhắn fanpage chính thức của spa.
 - Website: form đặt lịch online (chọn dịch vụ, ngày, giờ, kỹ thuật viên).
 - Hotline điện thoại: 0926.559.268, hoạt động giờ mở cửa.
- Bot AI tư vấn: khách có thể nhắn fanpage để được bot trả lời 24/7 các câu hỏi về dịch vụ, giá, chính sách.

II. THỜI GIAN ĐẶT LỊCH
- Đặt trước tối thiểu 1-2 tiếng để spa sắp xếp kỹ thuật viên + phòng.
- Cao điểm (cuối tuần, lễ Tết): nên đặt trước 1-2 ngày.
- Walk-in: chấp nhận khách đến trực tiếp nếu còn slot trống. Khách đặt trước được ưu tiên.
- Đặt lịch ngày trong tuần: linh hoạt, ít cao điểm.
```

### Chunk 1

```
Đoạn giữa tài liệu, hướng dẫn quy định đổi/hủy lịch và các phương thức thanh toán tại spa.

III. ĐỔI / HỦY LỊCH
- Hủy hoặc đổi lịch MIỄN PHÍ trước giờ hẹn 2 tiếng.
- Hủy/đổi sát giờ (dưới 2 tiếng) có thể tính phí giữ chỗ tùy gói.
- Vắng mặt không báo (no-show): có thể bị trừ 1 buổi trong gói.
- Đổi kỹ thuật viên: được, miễn báo trước qua hotline.

IV. THANH TOÁN
- Tiền mặt tại quầy.
- Chuyển khoản ngân hàng: thông tin STK gửi trước/sau khi xác nhận lịch.
- Quẹt thẻ ATM/Visa/Master tại quầy.
- Mã QR Banking, MoMo, ZaloPay đều được chấp nhận.
- Voucher, mã giảm giá: gửi trước cho lễ tân để áp dụng.

V. NHẮC LỊCH (REMINDER)
- Spa gửi tin nhắn Zalo/SMS nhắc lịch trước 1 ngày.
- Khách có thể tắt nhắc lịch nếu không cần (báo lễ tân).

VI. ĐẶT LỊCH NHÓM
- 2-3 khách: đặt qua hotline, spa sắp xếp phòng đôi.
- 4+ khách: liên hệ hotline để có ưu đãi nhóm + sắp xếp lịch chuyên biệt.
- Sự kiện riêng (sinh nhật, hen đôi bạn thân, mẹ-con): có gói party spa.
```

### Chunk 2

```
Đoạn cuối tài liệu, hướng dẫn tra cứu lịch sử dịch vụ và cách gửi phản hồi, khiếu nại sau dịch vụ.

VII. TRA CỨU LỊCH SỬ DỊCH VỤ
- Khách đã đăng ký thẻ thành viên: có thể nhắn hotline để check lịch sử + buổi còn lại trong gói.
- Số dư buổi trong combo 10 buổi: lễ tân cập nhật mỗi lần check-in.

VIII. PHẢN HỒI / GÓP Ý
- Phản hồi sau dịch vụ: khách có thể đánh giá qua link gửi sau buổi.
- Khiếu nại: gửi qua hotline 0926.559.268 hoặc fanpage. Quản lý trả lời trong 24h làm việc.
```

---

## 10. faq_service_combos

- **Source**: `file:///tmp/v2_corpus_enrich_new/faq_service_combos.txt`
- **Chunks**: 4

### Chunk 0

```
Đoạn 1/4: Giới thiệu các combo dịch vụ đa dạng với ưu đãi giảm giá hấp dẫn cho khách hàng.

COMBO DỊCH VỤ, ƯU ĐÃI NÂNG CAO VÀ CHÍNH SÁCH KHÁCH HÀNG
I. COMBO BUNDLE NHIỀU DỊCH VỤ
- Combo "Trẻ hóa toàn diện": chăm sóc da mặt + triệt lông body + gội dưỡng sinh — giảm 25-30% so với mua riêng từng dịch vụ.
- Combo "Bride-to-be" (cô dâu): facial trắng sáng + triệt lông toàn thân + gội kèm massage trước cưới, đặt trước 4-6 tuần.
- Combo "Detox + Relax": peel da nông + gội dưỡng sinh + massage body 60 phút.
- Combo "Mom & me" (mẹ và con gái): 2 khách dùng dịch vụ cùng phòng đôi, giảm 15%.
- Khách có thể tự ghép combo 2-3 dịch vụ — lễ tân tính ưu đãi tổng theo công thức.

II. NÂNG CẤP GÓI (UPGRADE)
- Khách đã mua gói cơ bản có thể đóng phụ phí để upgrade lên gói cao cấp.
- Gói chăm sóc da basic → premium: phụ phí tính theo chênh lệch giá gốc + tặng 1-2 buổi.
- Combo triệt lông nách → toàn thân: phụ phí phần chênh, giữ nguyên số buổi đã dùng.
- Upgrade trong cùng tháng mua gói: ưu đãi cộng thêm 5% giảm.
```

### Chunk 1

```
Đoạn 2/4 trình bày chính sách thẻ thành viên VIP và ưu đãi dành cho khách hàng quay lại hoặc tri ân.

III. THẺ THÀNH VIÊN VIP
- Khách hàng VIP được xác lập khi tổng chi tiêu lũy kế đạt 10.000.000đ trở lên.
- Ưu đãi VIP: giảm 15% mọi dịch vụ tiếp theo.
- Quà sinh nhật khách VIP: voucher 500.000đ trong tháng sinh.
- Ưu tiên đặt lịch giờ cao điểm.
- Mời tham dự sự kiện thân mật của spa (workshop dưỡng da, ra mắt công nghệ mới).

IV. KHÁCH HÀNG QUAY LẠI / TRI ÂN
- Tri ân khách hàng quay lại lần thứ 5 trở đi: giảm 10% mọi dịch vụ.
- Khách hàng quay lại sau 6 tháng vắng: ưu đãi welcome-back 15% lần đầu quay lại.
- Khách giới thiệu bạn mới: cả 2 cùng nhận voucher 200.000đ.

V. HỌC SINH SINH VIÊN
- Spa CHƯA có chính sách giảm giá riêng cho học sinh/sinh viên ở thời điểm hiện tại.
- Khách HSSV vẫn áp dụng được mọi ưu đãi chung (khách mới, sinh nhật, lễ Tết).
- Vui lòng theo dõi fanpage để cập nhật chương trình ưu đãi mới (nếu có).
```

### Chunk 2

```
Đoạn 3 trình bày chính sách bảo hành kết quả cho các combo dịch vụ và thông tin về chính sách trả góp hiện chưa áp dụng.

VI. BẢO HÀNH KẾT QUẢ
- Combo chăm sóc da 10 buổi: bảo hành hiệu quả 2 năm. Nếu không đạt mức cải thiện cam kết (90%) → spa hỗ trợ buổi bổ sung miễn phí trong thời hạn.
- Combo triệt lông toàn thân 10 buổi: bảo hành 1 năm cho touch-up nếu tóc mọc lại nhiều hơn 20%.
- Bảo hành KHÔNG áp dụng nếu khách không tuân thủ hướng dẫn chăm sóc sau (tắm nắng quá nhiều, dùng mỹ phẩm không phù hợp).

VII. TRẢ GÓP / CHIA NHỎ THANH TOÁN
- Spa CHƯA có chính sách trả góp 0% qua ngân hàng ở thời điểm hiện tại.
- Gói combo lớn (từ 10 buổi trở lên): có thể chia thanh toán theo 2-3 đợt (đặt cọc + thanh toán giữa kỳ + cuối kỳ). Vui lòng trao đổi trực tiếp với lễ tân.

VIII. VOUCHER VÀ THẺ QUÀ TẶNG
- Voucher dịch vụ: bán tại quầy, mệnh giá 200.000đ - 5.000.000đ.
- Thẻ quà tặng (gift card): cá nhân hóa tên người nhận, dùng cho mọi dịch vụ trong vòng 12 tháng.
- Quà tặng khuyến mãi: voucher tặng có giá trị riêng, không hoàn tiền mặt.
```

### Chunk 3

```
Đoạn cuối tài liệu, quy định điều kiện áp dụng ưu đãi và chứng chỉ chuyên môn của kỹ thuật viên.

IX. ĐIỀU KIỆN ÁP DỤNG ƯU ĐÃI
- Mỗi đơn chỉ áp dụng 1 mã ưu đãi (không cộng dồn) trừ khi chương trình cho phép.
- Khách hàng VIP vẫn được hưởng ưu đãi định kỳ (sinh nhật, lễ Tết) cộng thêm chiết khấu VIP 15%.
- Voucher giới thiệu bạn không gộp với combo lớn.

X. CHỨNG CHỈ CHUYÊN MÔN CỦA KỸ THUẬT VIÊN
- Tất cả kỹ thuật viên có chứng chỉ hành nghề thẩm mỹ do cơ sở đào tạo có thẩm quyền cấp.
- Đào tạo nội bộ định kỳ về công nghệ Diode Laser, chăm sóc da y khoa.
- Bác sĩ da liễu cộng tác đến thăm khám tại spa theo lịch hẹn (không thường trực).
- Spa KHÔNG đào tạo cấp chứng chỉ nghề thẩm mỹ cho người ngoài (không phải trung tâm dạy nghề).
```

---

## 11. faq_specific_treatments

- **Source**: `file:///tmp/v2_corpus_enrich_new/faq_specific_treatments.txt`
- **Chunks**: 4

### Chunk 0

```
Phần đầu tài liệu giới thiệu các liệu trình điều trị mụn chuyên sâu, bao gồm mụn ẩn, mụn viêm, mụn đầu đen và sẹo sau mụn.

DANH MỤC LIỆU TRÌNH CHĂM SÓC DA VÀ ĐIỀU TRỊ CHUYÊN SÂU

I. ĐIỀU TRỊ MỤN
- Mụn ẩn (mụn dưới da, comedones): có liệu trình lấy nhân mụn chuẩn y khoa kết hợp ánh sáng blue light kháng khuẩn. Thường 4-8 buổi để cải thiện rõ.
- Mụn viêm, mụn bọc: liệu trình điều trị mụn viêm gồm rửa mặt sâu, lấy nhân mụn, đắp mặt nạ kháng viêm, ánh sáng LED. Cần 6-10 buổi.
- Mụn đầu đen vùng mũi/cằm: liệu trình tẩy tế bào chết + hút nhân mụn vacuum nhẹ nhàng.
- Sẹo rỗ, sẹo lõm sau mụn: spa có liệu trình lăn kim vi điểm + tinh chất phục hồi, 3-6 buổi cách 4 tuần.
- Thâm sau mụn (PIH): liệu trình peel da nông + serum vitamin C, 4-6 buổi.
```

### Chunk 1

```
Đoạn 2 nằm trong phần công nghệ chăm sóc da hiện đại, giới thiệu các liệu trình và phương pháp làm sạch, dưỡng da và cải thiện da bằng công nghệ tiên tiến.

II. CÔNG NGHỆ CHĂM SÓC DA HIỆN ĐẠI
- Hydra Facial: liệu trình làm sạch sâu bằng đầu hút chân không 3-trong-1 (làm sạch + tẩy tế bào chết + cấp ẩm). Phù hợp da xỉn màu, lỗ chân lông to.
- Oxy Jet (oxy lift): bơm tinh chất bằng áp lực oxy tinh khiết, giúp da căng bóng tức thì.
- Mesotherapy không kim (no-needle): đưa dưỡng chất qua sóng siêu âm + điện di, không xâm lấn.
- Lăn kim vi điểm (microneedling): kích thích collagen tự thân để cải thiện sẹo, lỗ chân lông, lão hóa.
- Peel da hóa học: dùng acid AHA/BHA nồng độ kiểm soát, có 3 cấp độ (nông, vừa, sâu) tùy tình trạng da.
- Ánh sáng LED đa bước sóng: blue (kháng khuẩn), red (chống lão hóa), yellow (làm dịu da nhạy cảm).
- RF nâng cơ (radio frequency): sóng radio kích thích collagen sâu, hỗ trợ trẻ hóa.
```

### Chunk 2

```
Đoạn 3 trình bày các liệu trình nâng cao chăm sóc da như trẻ hóa, cấy trắng, trị nám, tàn nhang và thải độc da; đồng thời bắt đầu giới thiệu công nghệ triệt lông chuyên sâu.

III. LIỆU TRÌNH NÂNG CAO
- Trẻ hóa da bằng tế bào gốc: kết hợp lăn kim + serum tế bào gốc thực vật/động vật.
- Cấy trắng tự nhiên: cấy vi điểm tinh chất glutathione, vitamin C qua mesotherapy.
- Trị nám: kết hợp peel + ánh sáng IPL + serum kháng tyrosinase, 6-10 buổi.
- Trị tàn nhang: laser pico nhẹ, 3-5 buổi, cách 4-6 tuần.
- Thải độc da: liệu trình đắp mặt nạ than hoạt + hút độc tố qua điện di âm.

IV. TRIỆT LÔNG CHUYÊN SÂU
- Diode Laser lạnh: công nghệ chính, an toàn cho mọi loại da Fitzpatrick I-V.
- Vùng triệt: nách, tay, chân, body toàn thân, mặt (ria mép, cằm), bikini.
- Triệt lông cho nam: ngực, lưng, tay chân, cằm — cùng công nghệ Diode.
- Khoảng cách buổi: 4-6 tuần để chu kỳ lông mới mọc.

V. GỘI ĐẦU DƯỠNG SINH
- Liệu trình gội đầu dưỡng sinh chuẩn 30-45 phút.
- Có bao gồm massage đầu, vai, gáy thư giãn — KHÔNG tính phí thêm trong gói gội dưỡng sinh.
- Combo gội + ủ tóc: gói cao cấp có thêm bước ủ collagen.
- Tinh dầu thiên nhiên: lavender, sả chanh, bạc hà — khách chọn theo sở thích.
```

### Chunk 3

```
Đoạn cuối liệt kê các dịch vụ không cung cấp tại spa và hướng dẫn khách hàng khi cần các dịch vụ chuyên sâu.

VI. KHÔNG CÓ DỊCH VỤ
- Phẫu thuật thẩm mỹ xâm lấn (cắt mí, nâng mũi, hút mỡ): KHÔNG.
- Tiêm filler, botox: KHÔNG. Khách có nhu cầu, spa giới thiệu phòng khám da liễu uy tín.
- Cấy tóc, cấy chân mày: KHÔNG.
- Trị bệnh lý da nặng (vẩy nến, eczema mức trung-nặng): cần khám bác sĩ da liễu, spa chỉ chăm sóc hỗ trợ.
```

---

## 12. V3 Complaint Policy — 1777664458

- **Source**: `local://v3_complaint_policy_1777664458.md`
- **Chunks**: 128

_Hiển thị 3 chunk đầu + 1 chunk cuối (do file quá dài, tổng 128 chunks):_

### Chunk 0

```
# Chính sách xử lý khiếu nại + giải quyết phàn nàn của khách hàng
```

### Chunk 1

```
<chunk_context>Tổng quan chính sách xử lý khiếu nại và phàn nàn khách hàng tại spa</chunk_context>

# Chính sách xử lý khiếu nại + giải quyết phàn nàn của khách hàng
```

### Chunk 2

```
Tài liệu này mô tả các chính sách của spa khi khách hàng gặp vấn đề, phàn
```

_... (còn 124 chunks giữa) ..._

### Chunk 127 (cuối)

```
<chunk_context>Chính sách khách hàng quen và chương trình khuyến mãi voucher cho khách cũ tại spa</chunk_context>

+ tặng voucher trải nghiệm mới (10-20% tùy hạng thẻ).
```

---

## 13. Quy trình liệu trình từ đăng ký đến hoàn thành — V3R2

- **Source**: `local://v3_corpus_r2/corpus_doc_v3_treatment_flow_1777704698.md`
- **Chunks**: 125

_Hiển thị 3 chunk đầu + 1 chunk cuối (do file quá dài, tổng 125 chunks):_

### Chunk 0

```
# Quy trình liệu trình từ đăng ký đến hoàn thành
```

### Chunk 1

```
<chunk_context>Mục lục quy trình liệu trình spa từ đăng ký đến hoàn thành</chunk_context>

# Quy trình liệu trình từ đăng ký đến hoàn thành
```

### Chunk 2

```
Tài liệu này mô tả luồng khách trải nghiệm liệu trình spa, từ lần đầu đến
khi kết thúc: đăng ký, form thông tin, theo dõi tiến độ, xử lý phát sinh,
```

_... (còn 121 chunks giữa) ..._

### Chunk 124 (cuối)

```
<chunk_context>IX. ĐẶT LỊCH NHÓM - Gói party spa cho sự kiện riêng (sinh nhật, hen đôi bạn thân, mẹ-con)</chunk_context>

- Sự kiện riêng (sinh nhật, hen đôi bạn thân, mẹ-con): có gói party spa.
```

---

## 14. Chương trình thẻ thành viên + tích điểm + voucher + win-back — V3R2

- **Source**: `local://v3_corpus_r2/corpus_doc_v3_loyalty_voucher_1777704698.md`
- **Chunks**: 143

_Hiển thị 3 chunk đầu + 1 chunk cuối (do file quá dài, tổng 143 chunks):_

### Chunk 0

```
# Chương trình thẻ thành viên + tích điểm + voucher + win-back
```

### Chunk 1

```
<chunk_context>Tổng quan chương trình loyalty, thẻ thành viên, tích điểm, voucher, win-back tại spa</chunk_context>

# Chương trình thẻ thành viên + tích điểm + voucher + win-back
```

### Chunk 2

```
Tài liệu này mô tả các chương trình loyalty (khách trung thành), voucher
(mã giảm giá), thẻ thành viên (VIP), tích điểm, sinh nhật, win-back, combo
```

_... (còn 139 chunks giữa) ..._

### Chunk 142 (cuối)

```
<chunk_context>Chính sách tích điểm và tài khoản gia đình trong chương trình thẻ thành viên spa</chunk_context>

Account" — tích điểm chung, áp dụng cho gia đình từ 3 người trở lên.
```

---

