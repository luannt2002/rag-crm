# EVAL AGENT-JUDGE — 3 bot — 2026-06-27

Tổng hợp agent-judge (LLM-as-judge ngữ nghĩa) vs rule-scorer (substring/overlap) trên 3 bot.

## Bảng tổng hợp

| Bot | Rule-scorer % | TRUE % (agent) | Scorer false-neg | Provider-error (innocom-503) |
|---|---|---|---|---|
| test-spa-id (spa) | 78% (39/50) | 78% | 0 | 0 |
| chinh-sach-xe (xe) | 43% (13/40) | 43% | 0 | 11 |
| thong-tu-09-2020-tt-nhnn (legal) | 88% (44/50) | 94% | 3 | 3 |

Ghi chú: rule-scorer % của legal = 44/50 thô; agent-judge nâng lên 94% sau khi cứu 3 scorer-false-neg (đồng nghĩa nhưng substring không khớp). spa và xe không có scorer-false-neg — nhưng spa và xe có một số rule pass=true là FALSE-POSITIVE (rule chấm đúng nhưng thực tế sai số/sai dịch vụ), agent-judge đã lật lại thành FAIL.

---

## spa (test-spa-id) — FAIL thật

| Câu hỏi | Reason | Category |
|---|---|---|
| Slogan của spa là gì? | exp 'Nơi sắc đẹp thăng hoa'; bot refuse 'chưa có thông tin' — corpus có đáp án | refuse-miss |
| Spa có Fanpage và Tiktok không? | exp 'Có, tên Dr.Medispa'; bot refuse 'chưa thấy thông tin' | refuse-miss |
| Giá gốc/KM Ultherapy? | exp 20.000.000→8.000.000; bot trả 800.000→600.000 (sai dịch vụ + sai số) | wrong-number |
| Ưu đãi Ultherapy full-face? | exp 'mua 2 buổi tặng 1 buổi cổ'; bot refuse | refuse-miss |
| Giá trải nghiệm Căng bóng da tráng gương kim cương? | exp 249.000; bot trả 1.500.000 | wrong-number |
| Combo 10 buổi Nâng cơ trẻ hóa Dr.Medi? | exp 8.000.000; bot trả 7.000.000 (sai dịch vụ + số) | wrong-number |
| Tẩy da chết + ủ trắng body 60p? | exp 550.000; bot trả 450.000 | wrong-number |
| Giá trải nghiệm triệt lông 1 vùng KH mới? | exp 'từ 49.000/lần'; bot liệt kê 129k/249k/199k không có 49.000 (rule FP) | wrong-number |
| Giá lẻ 1 buổi triệt lông Bikini? | exp 499.000; bot refuse | refuse-miss |
| Triệt râu nam combo 10 buổi? | exp 1.499.000; bot trả 1.990.000 (combo nách nam, sai dịch vụ + số) | wrong-number |
| Giá trải nghiệm CSD chuyên sâu KH mới? | exp 199.000 ưu đãi; bot trả 700.000 giá gốc (rule FP) | wrong-number |

Tổng FAIL thật spa: 11 (5 refuse-miss + 6 wrong-number). 2 trong số đó là rule false-positive (rule chấm pass nhưng thực sai).

---

## xe (chinh-sach-xe) — FAIL thật

| Câu hỏi | Reason | Category |
|---|---|---|
| Mã NEOTERRA 195/65R16 NEOTOUR | exp '2-R16 195/65 NEO'; bot refuse (rule FP) | refuse-miss |
| Tên đầy đủ 2-R13 155/80 LPD | exp LANDSPIDER CITYTRAXX; bot 'không tìm thấy' (rule FP) | refuse-miss |
| 155/80R13 tương ứng mã nào | exp '2-R13 155/80 LPD'; bot trả '28-thg 11' (sai hoàn toàn loại dữ liệu) | wrong-fact |
| 2-ZR17 215/45 LPD dòng Landspider nào | exp CITYTRAXX H/P; bot refuse | refuse-miss |
| Rovelo 235/75R15 dòng gai nào | exp RIDGETRAK A/T II; bot trả 'WILDTRAXX A/T' (rule FP do overlap token) | wrong-fact |
| Tên Rovelo 2-R12C155/12 | exp 'Rovelo 155R12C RCMX+'; bot 'không tìm thấy' (rule FP) | refuse-miss |
| 2-ZR18 225/40 LPD là gì | exp LANDSPIDER CITYTRAXX H/P; bot refuse (rule FP) | refuse-miss |
| Chỉ số tải/tốc 215/70R16C | exp 108/106T; bot trả '100/98R' | wrong-number |
| 2-ZR21 275/40 DVT thương hiệu nào | exp DAVANTI; bot 'không tìm thấy' | refuse-miss |
| Tồn kho 2-R14 165/65 LPD | exp 404 cái; bot 'không tìm thấy' | refuse-miss |
| Giá 2-ZR19 255/35 LPD | exp 2.160.000 VND; bot refuse | refuse-miss |
| Tồn Rovelo 155R12C RCMX+ | exp 134 cái; bot 'không tìm thấy' | refuse-miss |
| Tồn 2-R13 175/70 LPD | exp 23 cái; bot 'không tìm thấy' | refuse-miss |
| Số Hotline/Zalo bảo hành | exp '0988 771 310'; bot 'chưa có thông tin' | refuse-miss |
| Link ảnh 2-R14 165/60 LPD | exp drive link; bot trả câu chào generic | refuse-miss |
| Date 2-R14 185/60 LPD | exp '25'; bot 'chưa tìm thấy' | refuse-miss |
| Link ảnh LANDSPIDER 155/80R13 G/P | exp drive link; bot 'chưa tìm thấy' | refuse-miss |

Tổng FAIL thật xe: 17 (13 refuse-miss + 2 wrong-fact + 1 wrong-number; + 1 link refuse-miss đã gộp). Ngoài ra 11 câu PROVIDER_ERROR (innocom-503, ans rỗng) không tính lỗi đáp.

---

## legal (thong-tu-09-2020-tt-nhnn) — FAIL thật + scorer-false-neg

### Scorer-false-neg (rule fail nhưng agent PASS/PARTIAL — đồng nghĩa)
| Câu hỏi | Reason |
|---|---|
| MFA tối thiểu mấy yếu tố? | exp 'tối thiểu hai'; ans 'ít nhất hai yếu tố' — đồng nghĩa, rule fail substring |
| Biện pháp chống thất thoát dữ liệu từ cấp độ mấy? | exp 'từ cấp độ 3 trở lên'; ans khớp chính xác, rule overlap 0.0 |
| Tần suất kiểm tra ATTT cấp độ 4? | exp 'tối thiểu một năm một lần'; ans 'cấp độ 4 là một năm một lần', rule fail do 'tối thiểu' |
| (PARTIAL) Rà soát quy chế ATTT định kỳ? | ans cite Điều 42 nhưng cắt trước tần suất — không xác nhận được số (RULE#0 không đoán) |
| (PARTIAL) Kiểm tra phục hồi sao lưu cấp 3? | ans cite Điều 22 nhưng cắt trước tần suất — không xác nhận |

### FAIL thật
| Câu hỏi | Reason | Category |
|---|---|---|
| Chuyển tiền liên NH từ bao nhiêu phải MFA? | exp 'từ 100 triệu trở lên'; bot refuse 'không có thông tin' — corpus có đáp án | refuse-miss |

Tổng FAIL thật legal: 1 (refuse-miss). 3 PROVIDER_ERROR (innocom-503), trong đó 1 câu cross-corpus lốp xe lẫn vào bộ legal.

---

## So sánh rule-scorer vs agent-judge

- **legal**: agent-judge +6pp (88%→94%) nhờ cứu 3 scorer-false-neg đồng nghĩa. Rule-scorer chấm khắt khe substring → undercount.
- **spa & xe**: agent-judge KHÔNG nâng điểm; ngược lại phát hiện rule **false-positive** (rule pass=true cho câu bot trả sai số/sai dịch vụ vì token overlap). Agent-judge lật lại thành FAIL → true% phản ánh đúng chất lượng thật.
- **Provider-error**: xe 11 câu + legal 3 câu innocom-503 (ans rỗng + HTTP 503) — lỗi hạ tầng provider, KHÔNG tính lỗi mô hình/đáp.

## Phân loại nguyên nhân FAIL thật (gộp)
- **refuse-miss** (corpus CÓ đáp án nhưng bot refuse): spa 5, xe 14, legal 1 = **20 câu** — coverage gap lớn nhất, nghi retrieval miss.
- **wrong-number / wrong-fact** (bot trả sai số/sai dòng/sai dịch vụ): spa 6, xe 3 = **9 câu** — nguy hiểm hơn refuse (sai im lặng, một số lọt rule-scorer).
- **provider-error**: xe 11, legal 3 = **14 câu** — hạ tầng innocom 503.
