# GP-100 Manifest — bộ 100 câu release-gate (QA-style)

**Nguồn ground-truth**: DB đã ingest (= 4 link data gốc) · **ĐÃ ĐỐI CHIẾU 100% khớp file gốc** (`z-luannt-chinh-sach-xe-data-link-3.txt` — 0 lệch giá).
**Luật chấm (QA bar)**: PASS = 0 sai + 0 bịa. Trap PHẢI defer/từ chối lịch sự. 'Thiếu/em kiểm tra lại' = ĐƯỢC PHÉP (đếm coverage riêng).

| STT | Loại | Câu hỏi test | Kết quả mong đợi | Ground-truth ref |
|---|---|---|---|---|
| G-001 | price_lookup | Lốp Rovelo 155/70R13 A68 giá bao nhiêu? | 630.000 | 2-R13 155/70 RHP-A68 |
| G-002 | price_lookup | Lốp Landspider 155/80R13 G/P giá bao nhiêu? | 684.000 | 2-R13 155/80 LPD |
| G-003 | price_lookup | Lốp Landspider 165/65R14 G/P giá bao nhiêu? | 702.000 | 2-R14 165/65 LPD |
| G-004 | price_lookup | Lốp Landspider 165/80R13 G/P giá bao nhiêu? | 747.000 | 2-R13 165/80 LPD |
| G-005 | price_lookup | Lốp Rovelo 185/60R14 A68 giá bao nhiêu? | 783.000 | 2-R14 185/60 RVL |
| G-006 | price_lookup | Lốp Landspider 185/55R15 G/P giá bao nhiêu? | 810.000 | 2-R15 185/55 LPD |
| G-007 | price_lookup | Lốp Landspider 185/60R14 G/P giá bao nhiêu? | 819.000 | 2-R14 185/60 LPD |
| G-008 | price_lookup | Lốp Landspider 185/65R14 G/P giá bao nhiêu? | 855.000 | 2-R14 185/65 LPD |
| G-009 | price_lookup | Lốp Landspider 185/55R16 G/P giá bao nhiêu? | 900.000 | 2-R16 185/55 LPD |
| G-010 | price_lookup | Lốp Rovelo 195/50R16 A68 giá bao nhiêu? | 945.000 | 2-R16 195/50 RVL |
| G-011 | price_lookup | Lốp Landspider 195/60R15 G/P giá bao nhiêu? | 963.000 | 2-R15 195/60 LPD |
| G-012 | price_lookup | Lốp Rovelo 195/65R14 A68 giá bao nhiêu? | 981.000 | 2-R14 195/65 RVL |
| G-013 | price_lookup | Lốp Landspider 205/55R16 G/P giá bao nhiêu? | 1.044.000 | 2-R16 205/55 LPD |
| G-014 | price_lookup | Lốp Landspider 215/45ZR17 H/P giá bao nhiêu? | 1.116.000 | 2-ZR17 215/45 LPD |
| G-015 | price_lookup | Lốp Landspider 205/65R16 G/P giá bao nhiêu? | 1.170.000 | 2-R16 205/65 LPD |
| G-016 | price_lookup | Lốp Landspider 215/55R16 G/P giá bao nhiêu? | 1.224.000 | 2-R16 215/55 LPD |
| G-017 | price_lookup | Lốp Landspider 215/55R17 H/P giá bao nhiêu? | 1.233.000 | 2-ZR17 215/55 LPD |
| G-018 | price_lookup | Lốp Rovelo 215/70R15 A68 giá bao nhiêu? | 1.260.000 | 2-R15 215/70 RVL |
| G-019 | price_lookup | Lốp Rovelo 195/70R15C RCMX+ giá bao nhiêu? | 1.287.000 | 2-R15 195/70 RVL |
| G-020 | price_lookup | Lốp Landspider 225/55ZR17 H/P giá bao nhiêu? | 1.377.000 | 2-ZR17 225/55 LPD |
| G-021 | price_lookup | Lốp Landspider 225/40ZR18 H/P giá bao nhiêu? | 1.440.000 | 2-ZR18 225/40 LPD |
| G-022 | price_lookup | Lốp Landspider 245/45ZR17 H/P giá bao nhiêu? | 1.467.000 | 2-ZR17 245/45 LPD |
| G-023 | price_lookup | Lốp Landspider 235/60R16 H/T giá bao nhiêu? | 1.512.000 | 2-R16 235/60 LPD |
| G-024 | price_lookup | Lốp Landspider 235/45ZR18 H/P giá bao nhiêu? | 1.602.000 | 2-ZR18 235/45 LPD |
| G-025 | price_lookup | Lốp Landspider 235/60R17 H/T giá bao nhiêu? | 1.611.000 | 2-R17 235/60 LPD |
| G-026 | price_lookup | Lốp Landspider 245/45ZR18 H/P giá bao nhiêu? | 1.656.000 | 2-ZR18 245/45 LPD |
| G-027 | price_lookup | Lốp Landspider 245/70R16 H/T giá bao nhiêu? | 1.755.000 | 2-R16 245/70 LPD |
| G-028 | price_lookup | Lốp Rovelo 235/50R19 INSTINCT SUV giá bao nhiêu? | 1.791.000 | 2-R19 235/50 RVL |
| G-029 | price_lookup | Lốp Rovelo 235/55R19 INSTINCT SUV giá bao nhiêu? | 1.800.000 | 2-R19 235/55 RVL |
| G-030 | price_lookup | Lốp Landspider 245/70R16 A/T giá bao nhiêu? | 1.890.000 | 2-R16 245/70 LPD WILLTRAXX |
| G-031 | price_lookup | Lốp Landspider 265/60R18 H/T giá bao nhiêu? | 1.944.000 | 2-R18 265/60 LPD |
| G-032 | price_lookup | Lốp Landspider 235/40ZR19 H/P giá bao nhiêu? | 1.989.000 | 2-ZR19 235/40 LPD |
| G-033 | price_lookup | Lốp Landspider 245/40ZR19 H/P giá bao nhiêu? | 2.034.000 | 2-ZR19 245/40 LPD |
| G-034 | price_lookup | Lốp Landspider 245/55ZR19 H/P giá bao nhiêu? | 2.079.000 | 2-ZR19 245/55 LPD |
| G-035 | price_lookup | Lốp Landspider 255/45ZR19 H/P giá bao nhiêu? | 2.178.000 | 2-ZR19 255/45 LPD |
| G-036 | price_lookup | Lốp Rovelo 265/60R18 A/T giá bao nhiêu? | 2.295.000 | 2-R18 265/60 RVL |
| G-037 | price_lookup | Lốp Landspider 265/50ZR20 H/P giá bao nhiêu? | 2.322.000 | 2-ZR20 265/50 LPD |
| G-038 | price_lookup | Lốp Rovelo 265/65R17 A/T giá bao nhiêu? | 2.430.000 | 2-R17 265/65 RVL |
| G-039 | price_lookup | Lốp Landspider 255/35ZR20 H/P giá bao nhiêu? | 2.511.000 | 2-ZR20 255/35 LPD |
| G-040 | price_lookup | Lốp Davanti 285/45ZR19 DX640 giá bao nhiêu? | 2.520.000 | 2-ZR19 285/45 DVT |
| G-041 | price_lookup | Lốp Landspider 275/45ZR20 H/P giá bao nhiêu? | 2.628.000 | 2-ZR20 275/45 LPD |
| G-042 | price_lookup | Lốp Davanti 275/40ZR21 DX640 giá bao nhiêu? | 3.240.000 | 2-ZR21 275/40 DVT |
| G-043 | price_inventory | Lốp Rovelo 155/70R13 A68 giá bao nhiêu và còn bao nhiêu chiếc? | 630.000 | 2-R13 155/70 RHP-A68 qty=100 |
| G-044 | price_inventory | Lốp Landspider 165/65R14 G/P giá bao nhiêu và còn bao nhiêu chiếc? | 702.000 | 2-R14 165/65 LPD qty=369 |
| G-045 | price_inventory | Lốp Landspider 175/65R15 G/P giá bao nhiêu và còn bao nhiêu chiếc? | 783.000 | 2-R15 175/65 LPD qty=48 |
| G-046 | price_inventory | Lốp Rovelo 185/65R15 A68 giá bao nhiêu và còn bao nhiêu chiếc? | 810.000 | 2-R15 185/65 RVL qty=324 |
| G-047 | price_inventory | Lốp Landspider 185/55R16 G/P giá bao nhiêu và còn bao nhiêu chiếc? | 900.000 | 2-R16 185/55 LPD qty=265 |
| G-048 | price_inventory | Lốp Rovelo 195/65R15 A68 giá bao nhiêu và còn bao nhiêu chiếc? | 981.000 | 2-R15 195/65 RVL qty=45 |
| G-049 | price_inventory | Lốp Landspider 215/45ZR17 H/P giá bao nhiêu và còn bao nhiêu chiếc? | 1.116.000 | 2-ZR17 215/45 LPD qty=682 |
| G-050 | price_inventory | Lốp Landspider 225/55R16 G/P giá bao nhiêu và còn bao nhiêu chiếc? | 1.233.000 | 2-R16 225/55 LPD qty=35 |
| G-051 | price_inventory | Lốp Rovelo 195/75R16C RCMX+ giá bao nhiêu và còn bao nhiêu chiếc? | 1.350.000 | 2-R16 195/75 RVL qty=14 |
| G-052 | price_inventory | Lốp Landspider 225/45ZR18 H/P giá bao nhiêu và còn bao nhiêu chiếc? | 1.440.000 | 2-ZR18 225/45 LPD qty=39 |
| G-053 | price_inventory | Lốp Landspider 225/55ZR18 H/P giá bao nhiêu và còn bao nhiêu chiếc? | 1.602.000 | 2-ZR18 225/55 LPD qty=424 |
| G-054 | price_inventory | Lốp Landspider 225/50ZR18 H/P giá bao nhiêu và còn bao nhiêu chiếc? | 1.656.000 | 2-ZR18 225/50 LPD qty=145 |
| G-055 | inventory | Lốp Rovelo 155/70R13 A68 còn bao nhiêu chiếc? | 100 | 2-R13 155/70 RHP-A68 |
| G-056 | inventory | Lốp Landspider 165/80R13 G/P còn bao nhiêu chiếc? | 204 | 2-R13 165/80 LPD |
| G-057 | inventory | Lốp Landspider 185/60R14 G/P còn bao nhiêu chiếc? | 89 | 2-R14 185/60 LPD |
| G-058 | inventory | Lốp Rovelo 195/65R14 A68 còn bao nhiêu chiếc? | 19 | 2-R14 195/65 RVL |
| G-059 | inventory | Lốp Landspider 205/45ZR17 H/P còn bao nhiêu chiếc? | 55 | 2-ZR17 205/45 LPD |
| G-060 | inventory | Lốp Landspider 225/60R16 G/P còn bao nhiêu chiếc? | 18 | 2-R16 225/60 LPD |
| G-061 | inventory | Lốp Landspider 235/70R16 H/T còn bao nhiêu chiếc? | 39 | 2-R16 235/70 LPD |
| G-062 | inventory | Lốp Landspider 245/45ZR18 H/P còn bao nhiêu chiếc? | 202 | 2-ZR18 245/45 LPD |
| G-063 | arrival_date | Lốp Landspider 205/55R16 khi nào về hàng? | 28-thg 11 | 205/55R16 91V CITYTRAXX G/P |
| G-064 | arrival_date | Lốp Landspider 195/65R15 khi nào về hàng? | 28-thg 11 | 195/65R15 91H CITYTRAXX G/P |
| G-065 | arrival_date | Lốp Landspider 225/45ZR18 khi nào về hàng? | 28-thg 11 | 225/45ZR18 95WXL CITYTRAXX H/P |
| G-066 | arrival_date | Lốp Landspider 235/60R18 khi nào về hàng? | 28-thg 11 | 235/60R18 107HXL CITYTRAXX H/T |
| G-067 | arrival_date | Lốp Landspider 185/60R15 khi nào về hàng? | 28-thg 11 | 185/60R15 84H CITYTRAXX G/P |
| G-068 | arrival_date | Lốp Landspider 215/65R16 khi nào về hàng? | 28-thg 11 | 215/65R16 98H CITYTRAXX G/P |
| G-069 | policy | Lốp Landspider bảo hành bao lâu? | 5 năm | xe-1 |
| G-070 | policy | Hotline của bên em là gì? | 0988 771 310 | xe-4 |
| G-071 | policy | Địa chỉ kho của Nam Phát ở đâu? | Thanh Trì | xe-4 |
| G-072 | policy | Kết quả giám định bảo hành có trong bao lâu? | 7 ngày | xe-4 |
| G-073 | policy | Lốp Landspider xuất xứ ở đâu? | Thái Lan | xe-1 |
| G-074 | policy | Công ty phân phối lốp tên là gì? | Nam Phát | xe-4 |
| G-075 | trap_no_price | Lốp Neoterra 195/65R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-R16 195/65 NEO price=None — PHẢI defer/không bịa |
| G-076 | trap_no_price | Lốp Rovelo 185/55R15 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-R15 185/55 RVL price=None — PHẢI defer/không bịa |
| G-077 | trap_no_price | Lốp Rovelo 195/55R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-R16 195/55 RVL price=None — PHẢI defer/không bịa |
| G-078 | trap_no_price | Lốp Rovelo 205/65R15 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-R15 205/65 RVL price=None — PHẢI defer/không bịa |
| G-079 | trap_no_price | Lốp Rovelo 195/70R14 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-R14 195/70 RVL price=None — PHẢI defer/không bịa |
| G-080 | trap_no_price | Lốp Landspider 245/75R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-R16 245/75 LPD price=None — PHẢI defer/không bịa |
| G-081 | trap_no_price | Lốp Landspider 255/45ZR18 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | 2-ZR18 255/45 LPD price=None — PHẢI defer/không bịa |
| G-082 | trap_oos_brand | Lốp Michelin 205/55R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | brand ngoài KB — cấm bịa |
| G-083 | trap_oos_brand | Lốp Bridgestone 205/55R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | brand ngoài KB — cấm bịa |
| G-084 | trap_oos_brand | Lốp Goodyear 205/55R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | brand ngoài KB — cấm bịa |
| G-085 | trap_oos_brand | Lốp Pirelli 205/55R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | brand ngoài KB — cấm bịa |
| G-086 | trap_oos_brand | Lốp Kumho 205/55R16 giá bao nhiêu? | KHÔNG bịa — defer/refuse lịch sự | brand ngoài KB — cấm bịa |
| G-087 | trap_oos_domain | Thời tiết Hà Nội hôm nay thế nào? | KHÔNG bịa — defer/refuse lịch sự |  |
| G-088 | trap_oos_domain | Bên em có bán dầu nhớt không? | KHÔNG bịa — defer/refuse lịch sự |  |
| G-089 | trap_oos_domain | Có bán mâm xe không? | KHÔNG bịa — defer/refuse lịch sự |  |
| G-090 | multi_variant_listing | Size 175/70R13 bên em có những loại nào? Giá từng loại? | 720.000 | phải liệt kê CẢ 2 loại: 2-R13 175/70 RHP-A68=720.000, 2-R13 175/70 LPD=738.000 |
| G-091 | multi_variant_listing | Size 165/60R14 bên em có những loại nào? Giá từng loại? | 648.000 | phải liệt kê CẢ 2 loại: 2-R14 165/60 RVL=648.000, 2-R14 165/60 LPD=684.000 |
| G-092 | multi_variant_listing | Size 165/65R14 bên em có những loại nào? Giá từng loại? | 675.000 | phải liệt kê CẢ 2 loại: 2-R14 165/65 RVL=675.000, 2-R14 165/65 LPD=702.000 |
| G-093 | multi_variant_listing | Size 175/65R14 bên em có những loại nào? Giá từng loại? | 684.000 | phải liệt kê CẢ 2 loại: 2-R14 175/65 RVL=684.000, 2-R14 175/65 LPD=783.000 |
| G-094 | multi_variant_listing | Size 175/70R14 bên em có những loại nào? Giá từng loại? | 729.000 | phải liệt kê CẢ 2 loại: 2-R14 175/70 RVL=729.000, 2-R14 175/70 LPD=792.000 |
| G-095 | comparison | So sánh giá Rovelo 175/70R14 A68 và Landspider 215/55R16 G/P, loại nào đắt hơn? | 1.224.000 | 2-R14 175/70 RVL=729.000 vs 2-R16 215/55 LPD=1.224.000 |
| G-096 | comparison | So sánh giá Rovelo 185/70R14 A68 và Landspider 225/55ZR18 H/P, loại nào đắt hơn? | 1.602.000 | 2-R14 185/70 RVL=819.000 vs 2-ZR18 225/55 LPD=1.602.000 |
| G-097 | comparison | So sánh giá Landspider 195/60R15 G/P và Landspider 265/60R18 H/T, loại nào đắt hơn? | 1.944.000 | 2-R15 195/60 LPD=963.000 vs 2-R18 265/60 LPD=1.944.000 |
| G-098 | comparison | So sánh giá Davanti 205/55ZR17 DX640 và Landspider 245/45ZR20 H/P, loại nào đắt hơn? | 2.511.000 | 2-ZR17 205/55 DVT=1.152.000 vs 2-ZR20 245/45 LPD=2.511.000 |
| G-099 | existence | Bên em phân phối những thương hiệu lốp nào? | Rovelo | phải kể đủ brand chính |
| G-100 | existence | Bên em có lốp Davanti DX640 không? | DX640 |  |
