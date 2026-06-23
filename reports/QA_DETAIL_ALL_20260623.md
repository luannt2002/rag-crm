# QA DETAIL — LOAD-TEST AGENT-SCORED (20260623)

> 42 câu / 3 bot. Chấm bởi Claude agent (đọc evidence per câu, KHÔNG LLM-judge). Rule#0: verify corpus khi nghi ngờ.
> Re-seed sạch happy-case sau fix Jina/FK/_split_cols/dedup (commit ded8e01 + c57f7fe).

## GLOBAL ROLLUP

- **HALLU = 0/6 trap** ✅ SACRED HOLD
- **Content answer-rate = 32/36** (89%)
- **Pipeline-layer fail (chunk/retrieval/filter) = 0** ✅
- **Generation-layer (broad-list deflect) = 2** (sysprompt/route-tunable, cùng 1 gốc)
- **Golden-stale / corpus-gap = 2** (KHÔNG phải bug)
- **Latency p95 = 9686ms** (T2 gap)

## chinh-sach-xe — content 10/12 · HALLU 0/2

| # | category | câu | verdict | note |
|---|---|---|---|---|
| q01 | hoi_tham | Chào shop, tư vấn giúp mình với | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q02 | liet_ke_san_pham | Shop có những loại lốp nào, liệt kê giúp mình | 🟡 GENERATION (broad-list deflect) | Bot né 'cho biết nhóm nào' thay vì xổ summary doc (có full l |
| q03 | soi_san_pham | Lốp CITYTRAXX H/T có đặc điểm gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q04 | hoi_theo_size | Có lốp size 185/55R16 không? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q05 | hoi_theo_size | Lốp cho xe size 225/45ZR18 có mẫu nào? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q06 | hoi_hang | Hãng LANDSPIDER có những dòng lốp gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q07 | liet_ke_kho | Kho còn những mã lốp nào của loại WILDTRAXX? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q08 | tu_van_xe | Xe sedan gầm thấp thì nên dùng lốp gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q09 | so_sanh | So sánh lốp H/T và H/P khác nhau thế nào? | ⚪ DATA | Golden trống/không trong corpus — refuse defensible. |
| q10 | hoi_ngay_ve | Lốp 195/65R15 ngày nào về hàng? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q11 | dat_lich | Mình muốn đặt mua lốp, cần làm gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q12 | dat_lich_slot | Tên Hùng, sđt 0912345678, địa chỉ 12 Lê Lợi, xe  | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q13 | oos_trap | Shop có bán dầu nhớt không? | ✅ CHUẨN (trap refused) | Đọc answer: từ chối đúng, không bịa. |
| q14 | hallu_trap | Lốp bay MICHELIN PILOT phản lực giá bao nhiêu? | ✅ CHUẨN (trap refused) | Đọc answer: từ chối đúng, không bịa. |

## test-spa-id — content 14/16 · HALLU 0/2

| # | category | câu | verdict | note |
|---|---|---|---|---|
| q01 | hoi_tham | Chào shop, cho mình hỏi thăm chút | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q02 | liet_ke_dich_vu | Spa có những dịch vụ gì, liệt kê tất cả giúp mìn | 🟡 GENERATION | Giữ prelim. |
| q03 | hoi_gia_san_pham | Laser Carbon giá bao nhiêu? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q04 | hoi_gia_san_pham | Giá dịch vụ trị mụn chuyên sâu? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q05 | soi_san_pham | Dịch vụ Nano kim cương là gì, có gì đặc biệt? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q06 | liet_ke_duoi_gia | Có dịch vụ nào dưới 500 nghìn không? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q07 | liet_ke_tren_gia | Liệt kê các dịch vụ trên 1 triệu | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q08 | re_nhat | Dịch vụ nào rẻ nhất ở spa? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q09 | dat_nhat | Dịch vụ nào đắt nhất? | ⚪ DATA (golden-stale) | Corpus max=3.000.000đ (Meso); golden 10M lệch data mới → bot |
| q10 | ngan_sach | Mình có 2 triệu thì làm được những dịch vụ nào? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q11 | khuyen_mai | Hiện có dịch vụ nào đang khuyến mãi / combo khôn | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q12 | so_sanh | So sánh Laser Carbon với Trẻ hóa IPL | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q13 | triet_long | Triệt lông nách giá combo 10 buổi bao nhiêu? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q14 | tu_van_quy_trinh | Quy trình chăm sóc da chuyên sâu gồm các bước nà | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q15 | dat_lich | Mình muốn đặt lịch khám da mặt | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q16 | dat_lich_slot | Tên mình là Lan, sđt 0901234567, hẹn 9h sáng mai | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q17 | oos_trap | Spa có bán xe máy không? | ✅ CHUẨN (trap refused) | Đọc answer: từ chối đúng, không bịa. |
| q18 | hallu_trap | Dịch vụ cấy chỉ collagen vàng 24k giá bao nhiêu? | ✅ CHUẨN (trap refused) | Đọc answer: từ chối đúng, không bịa. |

## thong-tu-09-2020-tt-nhnn — content 8/8 · HALLU 0/2

| # | category | câu | verdict | note |
|---|---|---|---|---|
| q01 | pham_vi | Thông tư 09/2020/TT-NHNN quy định về vấn đề gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q02 | doi_tuong | Đối tượng áp dụng của Thông tư này là ai? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q03 | dieu_khoan | Điều 2 quy định nội dung gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q04 | dieu_khoan | Điều 5 nói về phân loại hệ thống thông tin thế n | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q05 | giai_thich | Hệ thống thông tin cấp độ 3 là gì? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q06 | tra_cuu | Nguyên tắc bảo đảm an toàn thông tin gồm những g | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q07 | tra_cuu | Trách nhiệm của đơn vị vận hành hệ thống thông t | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q08 | hieu_luc | Thông tư này có hiệu lực từ ngày nào? | ✅ CHUẨN | Prelim pass — golden khớp answer. |
| q09 | oos_trap | Thông tư này quy định lãi suất tiết kiệm bao nhi | ✅ CHUẨN (trap refused) | Đọc answer: từ chối đúng, không bịa. |
| q10 | hallu_trap | Điều 99 của thông tư quy định gì? | ✅ CHUẨN (trap refused) | Đọc answer: từ chối đúng, không bịa. |

## REAL FAILURES + FIX-TIER (3-tier /rag-debug)

**Gap duy nhất: 'liệt kê TẤT CẢ X' deflect** (xe + spa, cùng gốc):
- Triệu chứng: 'liệt kê tất cả lốp/dịch vụ' → bot né 'cho biết nhóm nào' thay vì xổ summary doc (có full list 38 dịch vụ / 171 lốp).
- Tầng: **GENERATION** (chunk summary tới LLM nhưng bot chọn né) — KHÔNG phải pipeline/retrieval bug.
- Fix-tier: **(A) owner-sysprompt** — mở rộng rule 'liệt kê tất cả → xổ toàn bộ từ summary, không né' (đã có cho list-cụ-thể). HOẶC **(B) generic-route** broad-list → summary-doc. KHÔNG per-bot code.

**KHÔNG phải failure:** spa 'đắt nhất' (golden 10M stale, corpus max 3M, bot đúng) · xe 'so sánh H/T vs H/P' (corpus-gap, refuse defensible) · 2 prelim-HALLU (false-positive, trap refuse đúng).

## VERDICT: HALLU=0 ✅ · pipeline sạch ✅ · legal 10/10 · 1 gap generation cùng gốc (sysprompt-tunable).