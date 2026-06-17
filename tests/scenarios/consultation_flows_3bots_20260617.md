# Consultation Flows — 3 Bots (Grounded in Real Corpus)

**Generated:** 2026-06-17  
**Purpose:** Realistic multi-turn customer consultation flows for RAG load-testing. Every non-trap question is grounded in a literal chunk read from the live DB. Trap questions are labelled explicitly — the bot MUST refuse/say it does not know rather than fabricate.

**Corpus sources used:**
- `test-spa-id` — service price table, Buffet CNC packages, hair wash, massage, triệt lông, dưỡng sinh
- `chinh-sach-xe` — Landspider/Rovelo warranty policy (Nam Phát), product catalogue (CITYTRAXX G/P, H/T, H/P, WILDTRAXX A/T)
- `thong-tu-09-2020-tt-nhnn` — TT 09/2020/TT-NHNN (Điều 1–57), IT security requirements for banking organisations

**Question type codes:**
| Code | Meaning |
|---|---|
| GREET | greeting / chitchat |
| OPEN | open consultation ("tư vấn cho tôi", "có dịch vụ gì") |
| FACT | factoid lookup — specific fact/price/term that EXISTS in corpus |
| PARA | paraphrase / typo / colloquial restatement of a corpus fact |
| COMP | comparison between two items both in corpus |
| AGG | aggregation (cheapest/most expensive/list-all/under-X) — hard for retrieval |
| COREF | multi-turn coreference ("cái đó", "nó", "dịch vụ vừa nãy") |
| OOS | **TRAP** — out-of-scope / anti-hallucination; bot MUST refuse, NOT fabricate |
| BOOK | booking / action intent |

---

## Bot 1: `test-spa-id` — Spa & Beauty Services

**Flow premise:** A female customer (mid-30s) walks into the chat cold. She has skin concerns + wants to understand pricing and package options. She then pivots to hair removal. Natural informal Vietnamese — typos and casual phrasing included.

| Turn | Type | User message (natural Vietnamese) | Expected answer — key facts (literal from corpus) | Supporting chunk |
|---|---|---|---|---|
| 1 | GREET | "Alo bên mình ơi, cho hỏi chút được không ạ?" | Chào đón khách, sẵn sàng tư vấn | (no specific chunk — greeting) |
| 2 | OPEN | "Bên mình có những dịch vụ chăm sóc da gì ạ? Em muốn biết tổng quan cái đã" | Liệt kê nhóm: Chăm sóc da (CSD) chuyên sâu, trị mụn, cấp oxi tươi, thải độc da, cấp nước đa tầng, nano kim cương, nâng cơ 7 điểm vàng, dưỡng sinh mắt; Trẻ hóa IPL, Laser Carbon; Peel (trị thâm, trị mụn, trẻ hóa); Vikim; Meso | Bảng giá dịch vụ CSD — STT 1–18 |
| 3 | FACT | "Giá chăm sóc da chuyên sâu 1 buổi là bao nhiêu ạ?" | CSD Chuyên sâu (Chăm sóc da chuyên sâu): 700.000 VND / buổi | "STT: 1 \| Tên dịch vụ: Chăm sóc da chuyên sâu \| Giá 1 buổi: 700.000" |
| 4 | PARA | "Còn cái nano kim cương thì mắc hơn hả chị, giá cỡ bao nhiêu vậy" | CSD Nano kim cương: 1.500.000 VND / buổi (đắt hơn CSD chuyên sâu) | "STT: 6 \| Chăm sóc da nano kim cương \| 1.500.000" |
| 5 | COMP | "So sánh giúp em cái Trẻ hóa IPL với Laser Carbon đi ạ, cái nào tốt hơn cho da tuổi 35?" | Cả 2 đều 1.200.000 VND / buổi. Cả 2 nằm trong Gói 6 triệu, 7 triệu, 10 triệu (đều có ký hiệu x). Corpus không có mô tả lâm sàng "cái nào tốt hơn" — bot nên trả giá bằng nhau và để khách hàng hỏi thêm ở quầy | "STT: 9 \| Trẻ hóa IPL \| 1.200.000" & "STT: 10 \| Laser carbon \| 1.200.000" |
| 6 | FACT | "Gói Buffet CNC 7 triệu thì bao gồm những dịch vụ gì ạ?" | Gói 7 triệu = tất cả dịch vụ của gói 6 triệu + Peel điều trị mụn chuyên sâu + Vikim medic (Nano màng sinh học) + Peel trẻ hóa tái tạo tế bào Tretinoin. Mỗi gói tối đa 10 buổi, đa dịch vụ, không giới hạn số lần sử dụng | "STT: 2 \| Gói Buffet CNC 7 triệu..." + "Mỗi gói sử dụng tối đa 10 buổi..." |
| 7 | COREF | "Cái gói đó ngoài CSD thông thường ra thì có thêm Meso không ạ?" | Gói 7 triệu KHÔNG bao gồm Meso căng bóng trẻ hóa — Meso chỉ có trong gói 10 triệu. Gói 10 triệu = gói 7 triệu + Peel trẻ hóa xóa nhăn Ribo + Nano collagen trẻ hóa da + Meso căng bóng trẻ hóa | "STT: 3 \| Gói Buffet CNC 10 triệu" |
| 8 | AGG | "Dịch vụ chăm sóc da rẻ nhất bên mình là cái gì ạ? Em budget có hạn :)" | Rẻ nhất: CSD Chuyên sâu và Trị mụn chuyên sâu — cùng giá 700.000 VND / buổi | STT 1 (700k) & STT 2 (700k) |
| 9 | OOS | "Bên mình có dùng công nghệ RF Thermage hay Ultherapy không ạ? Em nghe nói cái đó trẻ hoá cực mạnh" | **REFUSE — không có trong corpus.** Corpus không đề cập Thermage hay Ultherapy. Bot phải nói không có thông tin về dịch vụ này, không được bịa ra | Không có chunk nào |
| 10 | OPEN | "Thôi em hỏi thêm về triệt lông nhé, bên mình có triệt không ạ?" | Có. Bảng giá triệt lông: Mép 129k/buổi, Mặt 249k, Nách 199k, 1/2 tay 349k, Cả tay 499k, 1/2 chân 599k, Cả chân 699k, Lưng 699k, Ngực & bụng 699k, Bikini (Bi) 499k, Toàn thân 2.499.000/buổi, Râu (nam) 249k. Có combo 10 buổi | Bảng triệt lông — STT 1–12 |
| 11 | FACT | "Triệt nách 1 buổi giá bao nhiêu, combo 10 buổi thì sao?" | Nách: buổi lẻ 199.000 VND; combo 10 buổi 1.199.000 VND | "STT: 3 \| Nách \| 199.000 \| 1199000" |
| 12 | COREF | "Vùng đó mà mua combo thì tiết kiệm được bao nhiêu so với mua lẻ ạ?" | Mua lẻ 10 buổi = 10 × 199.000 = 1.990.000 VND; Combo 10 buổi = 1.199.000 VND → tiết kiệm 791.000 VND | Tính từ STT 3 (nách lẻ 199k, combo 1199k) |
| 13 | FACT | "Massage cổ vai gáy 90 phút giá bao nhiêu ạ? Em hay bị đau cổ lắm" | Massage cổ vai gáy 90 phút: 500.000 VND | "STT: 2 \| Massage cổ vai gáy \| 90 phút \| 500000" |
| 14 | OOS | "Bên mình có dịch vụ phun xăm môi hoặc phun chân mày không chị?" | **REFUSE — không có trong corpus.** Corpus không đề cập dịch vụ phun xăm. Bot phải từ chối, không bịa ra | Không có chunk nào |
| 15 | BOOK | "Ok em muốn đặt lịch thử 1 buổi CSD chuyên sâu, bên mình đặt qua đây được không ạ?" | Corpus không có quy trình đặt lịch online chi tiết — bot có thể xác nhận nhu cầu nhưng không được bịa thông tin. Nếu corpus có hotline thì cung cấp (không thấy hotline spa trong corpus — REFUSE chi tiết đặt lịch) | Không có chunk đặt lịch spa cụ thể |

**Turns:** 15 | **Traps (OOS):** T9, T14 (2 traps) | **Booking turn:** T15

---

## Bot 2: `chinh-sach-xe` — Vehicle Tyre Policy (Nam Phát)

**Flow premise:** A tyre dealer rep contacts the bot after receiving a complaint from an end customer about a defective tyre. He wants to understand the warranty terms, the claims process, and what SKUs are in stock. Natural informal business Vietnamese.

| Turn | Type | User message (natural Vietnamese) | Expected answer — key facts (literal from corpus) | Supporting chunk |
|---|---|---|---|---|
| 1 | GREET | "Chào anh/chị, em là đại lý Nam Phát muốn hỏi về chính sách bảo hành ạ" | Chào đón, sẵn sàng hỗ trợ | (no specific chunk — greeting) |
| 2 | OPEN | "Cho em hỏi chính sách bảo hành lốp Landspider áp dụng cho loại xe nào ạ?" | Áp dụng cho tất cả sản phẩm lốp xe du lịch (PCR) Landspider và Rovelo có số seri hợp lệ. Bảo hành lỗi do chất lượng vật liệu hoặc quy trình sản xuất trong điều kiện sử dụng bình thường | "I. Phạm vi áp dụng — Áp dụng cho tất cả các sản phẩm lốp xe du lịch (PCR) Landspider và Rovelo..." |
| 3 | FACT | "Thời hạn bảo hành là mấy năm ạ?" | Hiệu lực bảo hành: 05 năm kể từ ngày sản xuất HOẶC đến khi gai còn ≥ 1.6mm — tùy điều kiện nào đến trước | "Hiệu lực bảo hành: 05 năm kể từ ngày sản xuất hoặc đến khi gai còn ≥ 1.6mm" |
| 4 | PARA | "Vậy nếu lốp bị mòn hết gai rồi thì còn được bh không anh?" | Gai < 1.6mm → Hết hiệu lực bảo hành | "Gai <1.6mm → Hết hiệu lực bảo hành" |
| 5 | FACT | "Nếu gai còn hơn 70% và lỗi do nhà sản xuất thì xử lý thế nào ạ?" | Gai còn trên 70% → Đổi mới 100% 01 lốp tương đương nếu xác định lỗi do nhà sản xuất | "Gai còn trên 70% → Đổi mới 100% 01 lốp tương đương nếu xác định lỗi do nhà sản xuất" |
| 6 | COMP | "Còn nếu gai còn khoảng 40% thì khác với còn 80% như thế nào ạ?" | Gai còn 40% (nằm trong khoảng 1.6mm đến <70%) → Bồi thường theo tỷ lệ % gai còn lại so với gai mới. Gai còn 80% (>70%) → Đổi mới 100% | "Gai còn trên 70% → Đổi mới 100%..." + "Gai còn từ 1.6mm đến <70% → Bồi thường theo tỷ lệ..." |
| 7 | FACT | "Quy trình bảo hành thực tế gồm mấy bước, và kết quả được báo trong bao lâu ạ?" | 3 bước: (1) Đại lý/khách hàng gửi lốp lỗi về điểm bán hoặc kho Nam Phát kèm thông tin đơn hàng; (2) Bộ phận kỹ thuật Nam Phát kiểm tra và lập biên bản giám định; (3) Kết quả được thông báo trong vòng 7 ngày làm việc. Hình thức: đổi lốp, bồi thường theo tỷ lệ, hoặc từ chối bảo hành | "Kết quả được thông báo trong vòng 7 ngày làm việc" |
| 8 | COREF | "7 ngày đó tính từ lúc nào ạ? Từ lúc em gửi hay từ lúc họ nhận được?" | Corpus ghi "7 ngày làm việc" — không nói rõ tính từ lúc gửi hay nhận. Bot nên trả lời đúng thông tin có (7 ngày làm việc) và không tự suy diễn thêm | "Kết quả được thông báo trong vòng 7 ngày làm việc" |
| 9 | OOS | "Lốp bị hư do đường xấu, cắt đinh thì có được bảo hành không ạ?" | **REFUSE đúng nghĩa:** Hư hỏng do đường sá (cắt, rách, thủng, vết thâm, tác động mạnh) thuộc danh sách "Các trường hợp KHÔNG bảo hành" → Không bảo hành. Đây KHÔNG phải OOS — corpus có đáp án, bot phải trả lời rõ: KHÔNG bảo hành | "Hư hỏng do đường sá: cắt, rách, thủng, vết thâm, tác động mạnh" nằm trong mục "Các trường hợp không bảo hành" |
| 10 | FACT | "Đại lý báo hàng lỗi thì có ưu tiên gì đặc biệt không so với khách lẻ?" | Đại lý báo hàng lỗi được ưu tiên xử lý nhanh và đổi lốp trong vòng 72h. Hàng lỗi trong 3 tháng đầu từ khi bán ra → đổi mới 100% (không tính % mòn gai) | "Đại lý báo hàng lỗi được ưu tiên xử lý nhanh và đổi lốp trong vòng 72h" |
| 11 | AGG | "Bên kho có lốp kích thước R13 không ạ? Kho có những size nào khoảng đó?" | Có nhiều size R13: LANDSPIDER 155/80R13 79T CITYTRAXX G/P, 165/80R13 83T CITYTRAXX G/P (và 75H), 175/70R13 82T CITYTRAXX G/P. Kho lốp LANDSPIDER — mã hàng 2-R13 ... | Bảng tồn kho Kho lốp LANDSPIDER — STT R13 |
| 12 | FACT | "Lốp WILDTRAXX A/T có size nào không ạ, em có khách hỏi off-road?" | Có: LT235/75R15 104/101S WILDTRAXX A/T; 245/65R17 111TXL WILDTRAXX A/T | "2．货物描述: LT235/75R15 104/101S WILDTRAXX A/T" + "245/65R17 111TXL WILDTRAXX A/T" |
| 13 | OOS | "Bên anh có bán lốp Michelin hay Bridgestone không ạ? Khách em hỏi so sánh giá" | **REFUSE — không có trong corpus.** Corpus chỉ đề cập Landspider và Rovelo. Bot không được bịa giá hay thông tin về Michelin/Bridgestone | Không có chunk nào về Michelin/Bridgestone |
| 14 | BOOK | "Cho em xin hotline và địa chỉ kho để em liên hệ giao hàng lỗi ạ" | Hotline/Zalo: 0988 771 310. Địa chỉ: Kho Hải Ngân, Ngõ 3 Đê Đại Hà, Xóm 10, Yên Mỹ, Thanh Trì, Hà Nội | "Hotline/Zalo: 0988 771 310" + "Địa chỉ: Kho Hải Ngân, Ngõ 3 Đê Đại Hà, Xóm 10, Yên Mỹ, Thanh Trì, Hà Nội" |
| 15 | OOS | "Nam Phát có chính sách trả góp khi mua số lượng lớn không ạ?" | **REFUSE — không có trong corpus.** Corpus không đề cập bất kỳ chính sách trả góp nào. Bot phải từ chối, không bịa | Không có chunk nào |
| 16 | COREF | "Cái địa chỉ kho anh vừa đưa đó, mở cửa từ mấy giờ đến mấy giờ ạ?" | **REFUSE — không có trong corpus.** Corpus cung cấp địa chỉ nhưng không đề cập giờ mở cửa. Bot không được bịa giờ giấc | Không có chunk giờ hoạt động |

**Turns:** 16 | **Traps (OOS + anti-fabrication):** T13, T15, T16 (3 traps) | Note: T9 is NOT an OOS trap — corpus has the answer (not covered under warranty).

---

## Bot 3: `thong-tu-09-2020-tt-nhnn` — Banking IT Security Circular (TT 09/2020)

**Flow premise:** A compliance officer at a Vietnamese bank is reviewing their IT security posture. They are working through TT 09/2020/TT-NHNN requirements section by section. Formal but sometimes shorthand Vietnamese — realistic for a compliance/legal research session.

| Turn | Type | User message (natural Vietnamese) | Expected answer — key facts (literal from corpus) | Supporting chunk |
|---|---|---|---|---|
| 1 | GREET | "Chào bot, mình cần tra cứu một số nội dung của TT 09/2020 về an toàn hệ thống thông tin ngân hàng" | Chào đón, xác nhận đây là Thông tư 09/2020/TT-NHNN quy định về an toàn hệ thống thông tin trong hoạt động ngân hàng | "Thông tư Quy định về an toàn hệ thống thông tin trong hoạt động ngân hàng — Số: 09/2020/TT-NHNN, ngày 21 tháng 10 năm 2020" |
| 2 | FACT | "Thông tư này có hiệu lực từ ngày nào?" | Thông tư có hiệu lực thi hành kể từ ngày 01 tháng 01 năm 2021, trừ Điểm b khoản 4 Điều 20 có hiệu lực từ 01/01/2022. Thay thế Thông tư 18/2018/TT-NHNN ngày 21/08/2018 | "Điều 56. Hiệu lực thi hành: 01/01/2021... thay thế Thông tư 18/2018/TT-NHNN" |
| 3 | OPEN | "Cho mình biết Thông tư quy định mấy loại thông tin, phân loại thế nào?" | Điều 4 phân loại theo thuộc tính bí mật: (1) Thông tin công cộng — công khai cho tất cả, không cần xác định danh tính; (2+) thông tin nội bộ, thông tin bí mật (corpus trích đầu Điều 4) | Điều 4. Phân loại thông tin — Chương 1 |
| 4 | FACT | "Hệ thống thông tin cấp độ 3 phải đáp ứng tiêu chí gì? Cho ví dụ cụ thể?" | Điều 5.4 — Hệ thống thông tin cấp độ 3 có một trong các tiêu chí: (a) xử lý thông tin bí mật nhà nước ở cấp độ Mật; (b) phục vụ hoạt động nội bộ (Điều 5.4b — corpus trích ngắn); (c) hệ thống thông tin quốc gia trong ngành Ngân hàng yêu cầu vận hành 24/7 và không chấp nhận ngừng vận hành không có kế hoạch trước; (d) các hệ thống thanh toán quan trọng trong ngành Ngân hàng | Điều 5.4 — Chương 1, đoạn 10 & 11 |
| 5 | PARA | "Nếu 1 hệ thống gồm nhiều sub-system, cấp độ được tính sao vậy?" | Trường hợp hệ thống thông tin bao gồm nhiều hệ thống thành phần, mỗi hệ thống thành phần tương ứng cấp độ khác nhau → cấp độ hệ thống thông tin được xác định là cấp độ CAO NHẤT trong số các thành phần | Điều 5.7 — "cấp độ hệ thống thông tin được xác định là cấp độ cao nhất trong số..." |
| 6 | FACT | "Điều 13 yêu cầu gì về tổ chức nhân lực IT security?" | Điều 13: Người đại diện hợp pháp phải trực tiếp tham gia chỉ đạo và có trách nhiệm trong công tác xây dựng chiến lược, kế hoạch về bảo đảm an toàn thông tin | Điều 13. Tổ chức nguồn nhân lực — Chương 2 Mục 2 |
| 7 | FACT | "Mật khẩu (mã khóa bí mật) phải đáp ứng tối thiểu những gì theo Điều 28?" | Điều 28.2a: Mã khóa bí mật phải có độ dài từ sáu ký tự (6 ký tự) trở lên, cấu tạo gồm ký tự số, chữ hoa, chữ thường và ký tự đặc biệt (nếu hệ thống cho phép). Phải được kiểm tra tự động khi thiết lập. Điều 28.2b: mật khẩu mặc định nhà sản xuất phải thay đổi trước khi đưa vào sử dụng | Điều 28.2 — Chương 2 Mục 5, đoạn 41 |
| 8 | COREF | "Còn về yêu cầu phần mềm quản lý cái mật khẩu đó thì sao?" | Điều 28.2c: Phần mềm quản lý mã khóa bí mật phải: (i) yêu cầu thay đổi lần đầu đăng nhập; (ii) thông báo mật khẩu sắp hết hạn; (iii) hủy hiệu lực mật khẩu hết hạn; (iv) hủy hiệu lực khi nhập sai quá số lần cho phép; (v) cho phép thay đổi ngay khi bị lộ; (vi) ngăn chặn dùng lại mật khẩu cũ trong một khoảng thời gian | Điều 28.2c — đoạn 42 |
| 9 | COMP | "Sao lưu dự phòng cho hệ thống cấp 3 khác hệ thống cấp thấp hơn thế nào?" | Cấp độ 3 trở lên: phải có phương án tự động sao lưu phù hợp tần suất thay đổi, bảo đảm dữ liệu phát sinh sao lưu trong vòng 24 giờ; dữ liệu sao lưu phải lưu ra phương tiện lưu trữ ngoài và cất giữ tách rời khu vực lắp đặt ngay trong ngày làm việc tiếp theo; kiểm tra phục hồi tối thiểu 1 năm/lần. Hệ thống còn lại: sao lưu định kỳ theo quy định của tổ chức; kiểm tra phục hồi tối thiểu 2 năm/lần | Điều 22 — Mục 4, đoạn 33 |
| 10 | AGG | "Tổ chức phải báo cáo sự cố ATTT về NHNN trong bao nhiêu giờ, và gửi báo cáo hoàn thành trong bao nhiêu ngày?" | Báo cáo sự cố: trong vòng 24 giờ kể từ thời điểm phát hiện (gửi về antt@sbv.gov.vn). Báo cáo hoàn thành khắc phục: trong vòng 05 ngày làm việc sau khi hoàn thành khắc phục | Điều 54.1 — "24 giờ kể từ thời điểm sự cố được phát hiện... 05 ngày làm việc sau khi hoàn thành khắc phục" |
| 11 | FACT | "Trung tâm dữ liệu yêu cầu cổng vào ra phải có người kiểm soát mấy tiếng?" | Điều 18.1: Cổng vào ra tòa nhà trung tâm dữ liệu phải có người kiểm soát 24/7 | Điều 18. Yêu cầu đối với trung tâm dữ liệu — đoạn 28 |
| 12 | COREF | "Yêu cầu đó áp dụng cho mọi hệ thống hay chỉ từ cấp độ mấy trở lên?" | Corpus không nói rõ ngưỡng cấp độ riêng cho Điều 18 — Điều 18 nằm trong Mục 3 áp dụng chung. Bot nên trả lời theo corpus và không suy diễn thêm (hoặc chỉ ra rằng Điều 18 là "ngoài việc bảo đảm yêu cầu tại Điều 17") | Điều 18 — "Ngoài việc bảo đảm yêu cầu tại Điều 17 Thông tư này, trung tâm dữ liệu phải..." |
| 13 | FACT | "Hệ thống dự phòng thảm họa cấp độ 3 phải thay thế hệ thống chính trong bao lâu?" | Điều 50.1c(i): 4 giờ đối với các hệ thống thông tin từ cấp độ 3 trở lên (ngoại trừ hệ thống xử lý thông tin bí mật nhà nước). Hệ thống bí mật nhà nước: 24 giờ. Các hệ thống khác: theo thời gian quy định của tổ chức | Điều 50.1c — đoạn 66 |
| 14 | OOS | "Thông tư này có quy định gì về xử phạt vi phạm hành chính không, mức phạt bao nhiêu?" | **REFUSE — không có trong corpus.** TT 09/2020 quy định yêu cầu kỹ thuật và tổ chức — không quy định mức xử phạt cụ thể. Bot phải từ chối, không bịa ra mức phạt | Không có chunk nào về mức phạt |
| 15 | OOS | "Vậy so với ISO 27001 thì Thông tư này có điểm gì khác không ạ?" | **REFUSE — không có trong corpus.** Corpus không so sánh TT 09/2020 với ISO 27001. Bot không được tự suy luận ra so sánh không có trong văn bản | Không có chunk nào |
| 16 | AGG | "Liệt kê các nội dung tổ chức phải thực hiện trong Mục 5 Quản lý truy cập gồm những Điều nào?" | Mục 5 Chương 2 gồm: Điều 28 (Yêu cầu đối với kiểm soát truy cập), Điều 29 (Quản lý truy cập mạng nội bộ), Điều 30 (Quản lý truy cập hệ thống thông tin và ứng dụng), Điều 31 (Quản lý kết nối Internet) | Đoạn 39–45 — Chương 2 Mục 5 |
| 17 | COREF | "Điều 31 về kết nối Internet yêu cầu ban hành quy định bao gồm những nội dung gì?" | Điều 31.1: Quy định quản lý kết nối, truy cập sử dụng Internet gồm các nội dung cơ bản: (a) trách nhiệm cá nhân và... (corpus trích ngắn, không liệt kê hết) — bot trả theo corpus, chỉ trích dẫn những gì có | Điều 31 — đoạn 45 |
| 18 | OOS | "Bên mình (ngân hàng) đang dùng Azure để host core banking, cần làm gì để tuân thủ TT09?" | **REFUSE dạng tư vấn cụ thể — không có trong corpus.** TT 09/2020 quy định nguyên tắc chung; không có hướng dẫn riêng cho Azure. Bot có thể trích dẫn điều khoản bên thứ ba/đám mây (Điều 32–36) nhưng KHÔNG được tự suy luận ra checklist tùy chỉnh cho Azure | Corpus chỉ có Điều 32–36 nguyên tắc chung; không có Azure-specific guidance |

**Turns:** 18 | **Traps (OOS + anti-fabrication):** T14, T15, T18 (3 traps)

---

## Summary

| Bot | Turns | OOS Traps | Booking turns | Primary challenge |
|---|---|---|---|---|
| `test-spa-id` | 15 | 2 (T9, T14) | 1 (T15) | Price lookup, package comparison, coreference across service names |
| `chinh-sach-xe` | 16 | 3 (T13, T15, T16) | 1 (T14) | Warranty tier logic, product catalogue SKU lookup, non-warranty exclusions |
| `thong-tu-09-2020-tt-nhnn` | 18 | 3 (T14, T15, T18) | 0 | Article-level factoid, cross-article comparison, aggregation over section headings |
| **Total** | **49** | **8** | **2** | |

**Anti-hallucination coverage:** 8/49 turns (16.3%) are traps designed to elicit fabrication. A passing bot scores HALLU_FABRICATE = 0 on all 8 traps.

**Hard retrieval turns (AGG type):** T8/T15 (spa), T11 (xe), T10/T16 (circular) — these require the bot to either enumerate or compare across multiple chunks. Expect retrieval to struggle here; failures should be classified as Coverage misses, not HALLU.
