# RESULT DETAIL REPORT — chinh-sach-xe, 2026-07-06 (block ON, 200 câu, agent-graded DB-verified)

> Anchor: `specs/002-deepdebug-luannt/evidence/step20_full_detail_verdicts.json` · corpus 6e6c0774 · commit b5fc6cb

## 0. TỔNG KẾT — câu đúng / chưa chuẩn / sai, ở tầng nào

| Phân loại | gate100 | luannt100b(bẫy) | Tổng |
|---|---|---|---|
| dung (đúng, có đáp án) | 78 | 59 | 137 |
| refuse_dung (từ chối ĐÚNG câu bẫy) | 13 | 15 | 28 |
| lech (số/entity sai dòng) | 0 | 6 | 6 |
| sai_bia (bịa) | 7 | 9 | 16 |
| thieu (sót item) | 2 | 4 | 6 |
| chua_chuan (lệch spec) | 0 | 0 | 0 |
| refuse_oan (từ chối OAN) | 0 | 7 | 7 |
| **ĐẠT (dung+refuse_dung)** | **91/100** | **74/100** | — |

### Câu SAI/CHƯA CHUẨN ở TẦNG nào (35 câu)

| Tầng lỗi | Số câu | Code nghi ngờ |
|---|---|---|
| **RETRIEVE** (retrieve) | 13 | `retrieve.py:361-376 (_decompose_active gate + is_price_ask) — comparison/aggregation/brand-list KHÔNG route stats → chunk-retrieve topN bỏ sót dòng-có-giá` |
| **GENERATE** (grounding-nonnumeric) | 11 | `generate.py (CHƯA CÓ gate) — cần grounding-claim check cho scope/brand ('chưa phân phối X' phải verify DB)` |
| **INGEST** (ingest) | 5 | `document_stats.py:946 _premerge_split_headers (header 2-dòng gộp → cột NGÀY VỀ mất tên, lưu key rỗng)` |
| **GUARD** (block-gate) | 3 | `numeric_fidelity.py + number_format.py:130 — '0 lốp' bị coi non-claim (min_digits), %-công-thức, world-knowledge unit nhỏ` |
| **UNDERSTAND/condense** (coreference) | 3 | `understand.py:173 condense — chain 'nó/SKU đó' rewritten=None, entity lượt trước không carry` |

**Note bịa-SỐ ≈ 0**: mọi sai_bia còn lại là PHI-SỐ (scope/brand/date-semantics) hoặc block-gate edge — numeric fabrication đã bị block chặn.

## 1. CHI TIẾT TỪNG TẦNG — từng câu + evidence + (queryable? / chưa-qua-topN?)

### TẦNG RETRIEVE — retrieve (13 câu)
**Code nghi ngờ**: `retrieve.py:361-376 (_decompose_active gate + is_price_ask) — comparison/aggregation/brand-list KHÔNG route stats → chunk-retrieve topN bỏ sót dòng-có-giá`

> Phân biệt anh hỏi: các câu này data **CÓ query ra được** (tồn tại trong document_service_index/corpus) nhưng **chưa qua topN** của chunk-retrieve → LLM không thấy. KHÔNG phải 'không có data'. Fix = route qua stats-index query (deterministic, lấy đủ) thay vì chunk topN.

- **G-099** [thieu]: Câu hỏi liệt kê thương hiệu phân phối; corpus có 4 brand đang bán trong document_service_index (LPD 129sp, RVL 39sp, DVT 7sp giá 1.152.000-3.240.000 còn hàng qty 92-528, NEO 1sp price=None). Answer chỉ nêu 'hai thương hiệu chính là Landspider và Rovelo', SÓT Davanti (và Neoterra). Root: existence/brand-list intent chỉ retrieve 3 chunk, chunk score-anchor là doc chính sách bảo hành xe-1 ghi literal 'Áp dụng cho thương hiệu Landspider (Thailand) và Rovelo (Vietnam)' → LLM neo theo câu 2-brand đó. Corpus KHÔNG có 1 chunk canonical liệt kê toàn bộ brand; tập brand nằm rải trong product index. Cùng run G-100 lại khẳng định bot BÁN Davanti DX640 còn hàng → mâu thuẫn nội tại, chứng minh Davanti là brand phân phối thật, không phải bịa. Bot không sai/bịa, chỉ thiếu item.
- **B-017** [refuse_oan]: Corpus CÓ đủ đáp án: '2-R16 205/65 LPD'=1170000, '2-ZR18 235/40 LPD'=1602000 → 235/40R18 đắt hơn. Nhưng chunks-to-LLM chỉ chứa dòng-có-giá của 205/65 (chunk 2: 1170000) còn 235/40 chỉ là chunk synonym-header (part 74/187) KHÔNG kèm giá; dòng '2-ZR18 235/40 LPD:1602000' không lọt vào 13 chunk. LLM thiếu giá 235/40 nên vơ 1.800.000 (giá của SKU lân cận 235/65 hoặc 235/55) gán cho 235/40r18 → block-gate bắt đúng token misattributed 1.800.000 và defer. Block-gate hoạt động đúng (ngăn 1 lỗi lệch), nhưng câu vốn TRẢ LỜI ĐƯỢC → refuse oan; gốc là retrieve miss dòng-có-giá của 235/40.
- **B-018** [refuse_oan]: Corpus CÓ dữ liệu: ~29 SKU quantity<5 (vd 2-R15 215/70 LPD=2, 2-ZR20 245/40 LPD=1, 2-R17 245/70 LPD HT=1...). Đây là truy vấn AGGREGATION toàn bảng nhưng retrieve chunk-based chỉ mang về vài chunk synonym-header rời rạc, không enumerate được toàn bộ dòng tồn<5. LLM cố liệt kê từ chunk lẻ → gán sai 1.287.000 cho '195/65r15 rhp-a68' → block-gate bắt misattributed và defer (đúng chức năng chống bịa). Nhưng câu là answerable-in-principle (dữ liệu có trong index) → refuse oan; gốc là retrieval không phục vụ aggregation quét-bảng.
- **B-021** [refuse_oan]: Corpus CÓ đáp án: đúng 1 chunk chứa 'Áp dụng cho thương hiệu Landspider (Thailand) và Rovelo (Vietnam)' (warranty policy header, verified psql: count=1 chunk chứa 'Thailand'). Chunk này chính là chunk0 mà B-027 (Rovelo origin) retrieve được ở score 0.3501. Nhưng với query B-021 'Lốp Landspider xuất xứ từ đâu?', retrieval trả score_max=0.2726 và KHÔNG có chunk origin trong top-3 (chỉ lấy warranty §I 'Phạm vi áp dụng' + 2 chunk bảng inventory xe-1). Bot vì thế trả 'không có thông tin về xuất xứ' → refuse SAI (silent retrieval miss). Đối xứng với B-027 cùng nguồn chunk: query 'Rovelo sản xuất ở đâu' surface được chunk, 'Landspider xuất xứ' thì không — chênh lệch embedding cross-phrasing khiến chunk 0.35 rớt khỏi top-K của B-021.
- **B-048** [refuse_oan]: Rewrite ĐÃ resolve đúng coreference: rewritten='link ảnh lốp 225/45ZR18 H/P'. Nhưng retrieve với query generic 'link ảnh' kéo về chunk 155/80R13 (top_score 0.8656) thay vì 225/45ZR18. LLM ghép số lạ '1.656.000' (không phải giá SKU nào đúng), numeric gate bắt n_unsupported=1 → block ra oos-template. Corpus CÓ đáp án (image links của 225/45ZR18 đã xuất hiện exact ở chunk B-046 score 1.0) nên block này là refuse_oan, không phải defer hợp lệ. Gate hoạt động đúng (chặn số bịa) nhưng gốc là retrieve miss entity đã được rewrite chuẩn.
- **B-052** [refuse_oan]: Coreference follow-up 'link Drive của nó' = 165/60R14. Corpus CÓ link (DB: 2-R14 165/60 LPD image=1e7FaVP5..., RVL image=1aj8tidsu...). LLM nhớ đúng giá 684.000/648.000 từ history nhưng retrieve turn này miss entity — topK trả chunk sai (2-R12C155/12, 175/60R15) score 0.52, KHÔNG có 165/60R14. Numeric gate flag 684.000/648.000 = n_unsupported=2 (không ground trong chunk turn này) → block bằng OOS price template. Bot đáng lẽ trả link. Immutable cause: follow-up retrieval không re-fetch SKU antecedent 165/60R14.
- **B-056** [refuse_oan]: Coreference 'sản phẩm đó' = Davanti 275/40ZR21. Corpus CÓ giá 3.240.000 (DB: 2-ZR21 275/40 DVT price_primary=3240000). LLM nhớ đúng 3.240.000 từ history nhưng retrieve turn này trả chunk synonym-list sai (155/80R13, 195R15C, 225/45R17 xe-3) score 0.78-0.82, KHÔNG chứa giá Davanti. Numeric gate flag 3.240.000 = n_unsupported=1 → block OOS price template dù giá là thật. Immutable cause: follow-up retrieval miss SKU 2-ZR21 275/40 DVT.
- **B-060** [refuse_oan]: Coreference 'mã đó' = 195/65R15 (từ B-059). Corpus CÓ giá (DB: 2-R15 195/65 LPD=972000, 2-R15 195/65 RVL=981000). LLM nhớ đúng 972.000/981.000 nhưng retrieve turn này trả chunk sai (155/80R13, 195/75R16C, 195R15C xe-3) score 0.42-0.43, KHÔNG chứa giá 195/65R15. Numeric gate flag cả 2 số = n_unsupported=2 → block OOS price template dù giá là thật. Immutable cause: follow-up retrieval miss SKU 2-R15 195/65 LPD/RVL.
- **B-062** [thieu]: Ground truth = 9 entity WILDTRAXX A/T giá >2tr (DB document_service_index, productname ILIKE '%WILDTRAXX%' AND price_primary>2000000): 255/70R16=2.079M, 265/70R16=2.133M, 245/65R17=2.268M(2-R17 245/65 LPD WILLTRAXX, qty 64), 265/65R17=2.322M, 265/60R18=2.412M, 265/50R20=2.601M, LT285/70R17=3.123M, LT285/65R18=3.267M, 285/50R20=3.267M. Bot liệt kê theo thứ tự giá tăng dần nhưng BỎ SÓT 245/65R17 (2.268.000/64) — item này nằm giữa 2.133 và 2.322 (cả hai đều có trong list bot) nên chắc chắn bị drop, không phải do truncate cuối. Chunk aggregation-card topk=1 score=1.0 nhưng row 245/65R17 không được đưa vào/LLM bỏ qua. n_grounded 7/7 (không bịa số) nhưng coverage thiếu.
- **B-065** [thieu]: Giá ĐÚNG (DB: 235/55ZR18 104WXL = 1.701.000/122, n_grounded 1/1). NHƯNG câu hỏi 2 phần 'khi nào về + giá'; 235/55ZR18 104WXL CITYTRAXX H/P CÓ trên date-sheet với ngày về = '28-thg 11' (verify attributes_json->>''='28-thg 11'). Bot trả 'tài liệu không ghi rõ ngày về cụ thể cho mã này' = CHỐI OAN dữ kiện corpus CÓ. score_max=0.6805, topk=15 nhưng chunk MARKS/date-sheet không lọt top-5 chunks_to_llm → LLM không thấy dòng ngày về của size này. Coverage thiếu phần ngày-về.
- **B-068** [thieu]: Giá + tồn ĐÚNG (DB: 255/60R18 112HXL CITYTRAXX H/T = 1.944.000/156, n_grounded 1/1). Câu hỏi trực tiếp 'có lịch về hàng không' — 255/60R18 112HXL CITYTRAXX H/T CÓ trên date-sheet ngày về '28-thg 11' (verify attributes_json->>''='28-thg 11'). Bot trả 'hiện đang có hàng, không cần chờ về thêm' = né/bỏ dữ kiện lịch về hàng mà corpus CÓ. Chunk topk=1 score=1.0 chỉ là card giá/tồn, chunk date-sheet không được retrieve → LLM không biết mã này nằm trong lịch 28/11. Coverage thiếu phần lịch về.
- **B-069** [thieu]: Ground truth 13-inch: 11 mã R13 (DB productname ILIKE '%R13%', quantity numeric), tổng tồn = 595 (155/70R13=100, 155/80R13=214, 165/65R13=35, 165/70R13=0, 165/80R13=204, 175/70R13 LPD=18, 175/70R13 RHP=16, 185/70R13=0, 155R13C=8, 165R13C=0, 175R13C=0). Bot chỉ liệt kê 4 mã (155/80=214, 165/80=204, 175/70=18, 175R13C=0) → tổng bịa 436. Bỏ sót 7 mã (100+35+0+16+0+8+0=159 tồn không tính). score_max=0.4739 (retrieval yếu nhất trong 10 câu), chunks_to_llm=5 chỉ phủ 4 R13 → LLM cộng trên tập thiếu. numeric_fidelity n_numbers=0/n_grounded=0 KHÔNG bắt được derived-sum sai (checker không validate phép cộng). Đây là lỗi coverage nghiêm trọng nhất trong lô.
- **B-070** [lech]: Câu hỏi giới hạn scope 'danh sách hàng về ngày 28/11'. Bot trả 285/45ZR22 114WXL = 3.735.000 — nhưng 285/45ZR22 KHÔNG có trên date-sheet (verify entity_name ILIKE '%285/45ZR22%' AND ngày về = 0 rows; sheet max size là 275/45ZR21). Đáp án đúng trong scope: join 54 size date-sheet với bảng giá → max = 275/45ZR21 110WXL = 3.312.000 (2-ZR21 275/45 LPD). Số 3.735.000 là giá thật của 1 mã ngoài scope → n_grounded 1/1 (không bịa số) nhưng SAI ENTITY/SCOPE cho ràng buộc 'về 28/11'. topk=1 score=1.0 chỉ lấy chunk giá cao nhất toàn kho, không join với date-sheet → không lọc theo scope 28/11.

### TẦNG GENERATE — grounding-nonnumeric (11 câu)
**Code nghi ngờ**: `generate.py (CHƯA CÓ gate) — cần grounding-claim check cho scope/brand ('chưa phân phối X' phải verify DB)`

- **G-067** [sai_bia]: Bot ĐÃ surface đúng expect '28-thg 11' (arrival date từ key rỗng '') NHƯNG bịa thêm claim ngày phi-số không grounded: 'đã có sẵn từ ngày 26/11' — LLM biến field thô date1='26' (bare number, không có ngữ nghĩa tháng trong DB) thành ngày '26/11'. numeric_fidelity n_grounded=1 nên block-gate không bắt (28-thg 11 và 26 đều trace được về chunk), nhưng phép biến đổi '26'→'26/11' là bịa ngữ nghĩa ngày → cần lớp grounding claim phi-số. Arrival value đúng nhưng câu chứa hallucination ngày.
- **G-077** [sai_bia]: Corpus DB xác nhận 2-R16 195/55 RVL price=NULL (trap hợp lệ) NHƯNG Rovelo LÀ hãng có phân phối (document_service_index có 33 entry RVL có giá, vd 2-R14 175/70 RVL=729000; G-099 expect Rovelo là brand chính). Retrieve topk_cand=1 chỉ trả về chunk sai-hãng '2-R16 195/55 LPD: 1044000' (LANDSPIDER cùng size), KHÔNG có dòng '195/55 RVL | price: —'. LLM không thấy dòng Rovelo nào nên bịa phủ nhận phi-số 'bên em chưa phân phối hãng Rovelo' — sai sự thật scope. numeric_fidelity n_numbers=0 nên block-gate (chỉ soi số) không bắt được. Đúng ra phải defer 'chưa có giá size này' như G-076/G-079 (nơi chunk CÓ dòng RVL price=—).
- **G-078** [sai_bia]: Corpus DB xác nhận 2-R15 205/65 RVL price=NULL (trap hợp lệ) NHƯNG Rovelo LÀ hãng có phân phối (33 entry RVL có giá trong document_service_index). Retrieve topk_cand=1 chỉ trả chunk sai-hãng '2-R15 205/65 LPD: 999000' (LANDSPIDER cùng size), KHÔNG có dòng '205/65 RVL | price: —'. LLM bịa phủ nhận phi-số 'bên em chưa phân phối hãng Rovelo' — trái corpus. n_numbers=0 nên block-gate không chặn. Đúng ra phải defer như G-076/G-079 (chunk có dòng RVL price=—).
- **B-011** [sai_bia]: Corpus PHÂN PHỐI Rovelo rõ ràng: 50 SKU RVL (42 có giá) trong document_service_index; riêng '2-R16 205/60 RVL' (ROVELO 205/60R16 A68) tồn tại nhưng price_primary=NULL → đáng lẽ defer về GIÁ. Nhưng bot phủ nhận cả THƯƠNG HIỆU: 'Dạ bên em chưa phân phối hãng Rovelo ạ' — bịa scope phi-số, mâu thuẫn corpus. Chunk-to-LLM chỉ có dòng LANDSPIDER 2-R16 205/60 LPD (1098000), dòng RVL không được retrieve → LLM không thấy Rovelo nên bịa 'không phân phối'. Block-gate không bắt vì đây là claim phi-số (không có token số sai).
- **B-031** [sai_bia]: Corpus [I. Phạm vi áp dụng] (document_chunks chunk_index=1) giới hạn RÕ 'lốp xe du lịch (PCR) Landspider và Rovelo'; 'xe tải' xuất hiện 0 lần trong toàn bộ warranty doc (6 hit 'xe tải/TBR/LTR' chỉ nằm trong bảng tồn kho SP, không phải chính sách). Bot trả 'chính sách bảo hành CÓ áp dụng cho lốp xe tải' — bịa mở rộng scope phi-số. Đúng bẫy note L-060 (chỉ PCR/du lịch — không bịa 'có'). Block-gate không bắt vì đây là claim phi-số (n_numbers=0).
- **B-034** [sai_bia]: Corpus [III] (chunk_index=3) chỉ ghi 'Biến dạng do điều kiện bảo quản không đúng'; các từ 'nhiệt độ (cao)/ẩm ướt/ánh nắng/nóng' = 0 hit trong corpus. Bot đúng verdict 'không được bảo hành' NHƯNG chèn thêm chi tiết bịa '(như nhiệt độ cao, ẩm ướt, ánh nắng trực tiếp)' — đúng bẫy note L-068 (tài liệu chỉ nói 'bảo quản sai' — không thêm 'nóng ẩm'). Đây là fabricate phi-số (specifics không có nguồn), cần lớp grounding claim phi-số bắt.
- **B-055** [sai_bia]: Corpus row 2-ZR21 275/40 DVT CHỈ có: productname 'Lốp xe DAVANTI 275/40ZR21 107Y XL DX640', price 3.240.000, quantity 251, image, model DX640 (verified DB attributes_json — không có trường mô tả/đặc điểm nào). Bot bịa nguyên list 5 gạch đầu dòng marketing phi-số: 'gai bám tối ưu công nghệ hình học đặc biệt', 'giảm tiếng ồn/rung động', 'vật liệu cao su tổng hợp đặc biệt', 'độ bền cao chống mài mòn', '300km/h' — world-knowledge/marketing KHÔNG có trong corpus. Chỉ 107Y/XL/251/3.240.000 là grounded. Numeric gate cho qua (n_grounded=1, n_unsupported=0) vì mọi SỐ đều đúng; gate không bắt bịa PHI-SỐ.
- **B-059** [lech]: Sheet lịch về hàng (attributes_json->>''='28-thg 11') CHỈ chứa LANDSPIDER/CITYTRAXX (54 dòng verified DB, KHÔNG có dòng ROVELO nào). 195/65R15 91H CITYTRAXX G/P (Landspider) CÓ trong sheet 28/11; Rovelo 195/65R15 A68 KHÔNG (chunk score 1.0: LPD row có ': 28-thg 11', RVL row date1=25 không có marker 28/11). Bot trả 'hai loại đang về ngày 28-thg 11' áp date 28/11 cho CẢ Rovelo = misattribution scope sang dòng sai. Giá/tồn (972.000/313, 981.000/45) grounded đúng; bot KHÔNG bịa số lượng về (dùng tồn hiện có 'hiện còn' — đúng note). Defect = claim '28/11' cho Rovelo (phi-số) không ground.
- **B-063** [sai_bia]: 3 entity + giá ĐÚNG HẾT (DB: 265/65R17 H/T=1.854M/216, A/T Wildtraxx=2.322M/40, Rovelo A/T=2.430M/1; n_grounded 3/3). NHƯNG bot bịa mô tả gai không có trong corpus: 'gai dạng đường phố phù hợp đi thành phố và cao tốc', 'gai dạng địa hình chịu được đường đất địa hình xấu', 'bền bỉ phù hợp cả đường nhựa và off-road nhẹ'. Verify: SELECT count(*) trên document_service_index + document_chunks với ILIKE '%đường phố%'/'%địa hình%'/'%độ bám%' = 0 rows. Corpus CHỈ có productname/price/quantity/image, KHÔNG có bất kỳ mô tả tread nào. Đây là world-knowledge fabrication phi-số → block-gate (numeric) không bắt được vì n_numbers grounded. Note L-090 'không bịa mô tả gai' bị vi phạm.
- **B-064** [sai_bia]: Date-sheet 28/11 (attributes_json->>''='28-thg 11') có ĐÚNG 10 size 18-inch: 225/45ZR18, 225/50ZR18, 225/55ZR18, 225/60R18, 235/40ZR18, 235/55ZR18, 235/60R18, 245/45ZR18, 255/55R18, 255/60R18. Bot BỊA 4 size 18-inch KHÔNG có trên sheet (verify on_sheet=0): 255/55ZR18, 275/45ZR18, 215/55ZR18, 205/50ZR18 — thực tế sheet có 255/55ZR20, 275/45ZR21, 215/55ZR17, 205/50ZR17 (bot corrupt inch). Đồng thời DUPLICATE nặng (245/45ZR18 ×4, 235/55ZR18 ×3) và SÓT 235/60R18 (có trên sheet). Chunk date-sheet topk=1 score=1.0 nhưng LLM đọc sai/nhân bản token từ 1 chunk dày. n_numbers=0 nên block-gate không chặn.
- **B-066** [sai_bia]: Bot bịa nguyên khối kiến thức world-knowledge: H/P='High Performance... độ bám tốt đường nhựa khô ướt, ổn định tốc độ cao, xe thể thao động cơ mạnh'; A/T='All Terrain... gai sâu chịu địa hình hỗn hợp đường đất đá cát, độ bám nhựa kém hơn'. Verify corpus: 0 rows chứa 'High Performance'/'All Terrain'/'độ bám'/'địa hình' cả trong document_service_index lẫn document_chunks. Corpus KHÔNG có bất kỳ mô tả đặc điểm sản phẩm nào — chỉ có bảng giá/tồn. Bot tự thừa nhận 'CITYTRAXX A/T không được đề cập' nhưng VẪN bịa full mô tả A/T. n_numbers=0 → block-gate không chặn được bịa phi-số.

### TẦNG INGEST — ingest (5 câu)
**Code nghi ngờ**: `document_stats.py:946 _premerge_split_headers (header 2-dòng gộp → cột NGÀY VỀ mất tên, lưu key rỗng)`

- **G-063** [sai_bia]: Cột 'NGÀY VỀ' (arrival date) mất tên header khi ingest → lưu dưới key rỗng trong document_service_index: row entity_name='205/55R16 91V CITYTRAXX G/P' có attributes_json={"": "28-thg 11", "chunk_index": 0} (DB xác nhận 55 rows có key '' value '28-thg 11', header row value 'NGÀY VỀ'). Chunk gửi LLM CÓ chứa '| : 28-thg 11' (retrieval OK) nhưng field không tên → LLM không nhận ra đó là ngày về hàng → bỏ qua giá trị expect 28-thg 11 và BỊA claim phi-số 'hiện đang còn hàng, không cần chờ về thêm' (mâu thuẫn dữ liệu có lịch về hàng). Số grounded đúng (giá 1.044.000, qty 734) nên block-gate numeric không bắt.
- **G-064** [sai_bia]: Cùng gốc rễ G-063: arrival date '28-thg 11' của 195/65R15 nằm dưới key rỗng '' trong document_service_index (entity_name='195/65R15 91H CITYTRAXX G/P', attributes_json={"": "28-thg 11", "chunk_index": 0}). Chunk CÓ '| : 28-thg 11' nhưng vô danh → LLM bỏ qua expect và bịa 'không cần chờ về thêm'. Numeric grounded (972.000, 313) nên gate không block.
- **G-065** [thieu]: Arrival date '28-thg 11' của 225/45ZR18 tồn tại trong corpus dưới key rỗng '' (entity_name='225/45ZR18 95WXL CITYTRAXX H/P', attributes_json={"": "28-thg 11", "chunk_index": 0}) và CÓ trong chunk gửi LLM. Bot chỉ trả 'hiện đang có hàng rồi ạ' + giá/qty đúng nhưng BỎ SÓT giá trị expect 28-thg 11 (field vô danh nên LLM không surface). Khác G-063/064: không kèm claim sai 'không cần chờ về thêm' nên chỉ là thiếu, không bịa.
- **G-066** [sai_bia]: Cùng gốc rễ: '28-thg 11' của 235/60R18 dưới key rỗng '' (entity_name='235/60R18 107HXL CITYTRAXX H/T'). Chunk CÓ '| : 28-thg 11' nhưng vô danh → bot bịa 'hiện đang còn hàng, không cần chờ về thêm' (phủ nhận có lịch về hàng, mâu thuẫn corpus) và bỏ qua expect. Số grounded (1.755.000, 191) → gate không block.
- **G-068** [sai_bia]: Cùng gốc rễ: '28-thg 11' của 215/65R16 dưới key rỗng '' (entity_name='215/65R16 98H CITYTRAXX G/P'). Chunk CÓ '| : 28-thg 11' vô danh → bot bịa 'hiện đang còn hàng, không cần chờ về thêm ạ' (phủ nhận lịch về hàng) + bỏ qua expect. Số grounded (1.260.000, 28) → gate không block.

### TẦNG GUARD — block-gate (3 câu)
**Code nghi ngờ**: `numeric_fidelity.py + number_format.py:130 — '0 lốp' bị coi non-claim (min_digits), %-công-thức, world-knowledge unit nhỏ`

- **B-002** [sai_bia]: Corpus row 2-ZR... '2-R16 195/65 NEO' has NO quantity key (attributes_json ? 'quantity' = false) and price NULL (document_service_index doc 1e394215). Retrieved chunk (score 1.0) did NOT contain the 2-R16 195/65 NEO row at all (chỉ có 185/55,195/50,195/55,195/60,195/75,205/55,205/60,205/65). Bot lại khẳng định 'hiện đang còn 0 lốp trong kho' — bịa số tồn cho SKU có quantity null. Numeric gate không bắt vì '0 lốp' bị coi là non-claim (0 = disabled), numeric_fidelity n_numbers=0.
- **B-032** [lech]: Corpus [II.2] (chunk_index=2) nói 'Bồi thường theo tỷ lệ % gai CÒN LẠI so với gai mới' — là công thức theo % gai, KHÔNG phải '% giá trị lốp'. Bot suy 40% mòn → 60% gai còn (đúng), nhưng khẳng định 'được bồi thường 60% GIÁ TRỊ lốp' — gán con số 60% sang đại lượng sai (giá trị thay vì tỷ lệ gai). Corpus không hề nói 60% giá trị. numeric_fidelity n_numbers=0 chứng minh gate không trích được '60%' để chấm grounding → block mode bật nhưng không chặn con số bịa. Đáng lẽ theo note: chỉ nói rơi vào bracket còn-60% + bồi theo tỷ lệ gai, KHÔNG chốt '60% giá trị'.
- **B-035** [sai_bia]: Corpus KHÔNG có thông tin độ sâu gai lốp mới: 'độ sâu gai'/'8mm'/'9mm'/'lốp mới' = 0 hit (chỉ có ngưỡng 1.6mm/70% trong [II]). Bot bịa 'từ 8mm đến 9mm' = số world-knowledge ngoài corpus. Đúng bẫy note L-056 (corpus KHÔNG có → phải nói không có info). Block mode BẬT nhưng numeric_fidelity n_numbers=0 → gate không nhận diện '8mm/9mm' (số kèm đơn vị mm) là numeric claim để chặn → lọt câu bịa số thay vì defer.

### TẦNG UNDERSTAND/condense — coreference (3 câu)
**Code nghi ngờ**: `understand.py:173 condense — chain 'nó/SKU đó' rewritten=None, entity lượt trước không carry`

- **B-012** [lech]: Hỏi '2-R15 185/55 RVL' (Rovelo). Corpus: dòng RVL '2-R15 185/55 RVL' (ROVELO 185/55R15 A68) có price_primary=NULL/quantity trống; dòng LANDSPIDER '2-R15 185/55 LPD' = 810000/quantity 779. Bot trả 'Rovelo 185/55R15 RHP-A68 giá 810.000đ, còn 779 lốp' — lấy giá+tồn của dòng LANDSPIDER gán cho tên sản phẩm ROVELO → trộn entity LPD↔RVL. Số 810000/779 là số THẬT nhưng của dòng khác thương hiệu. numeric_fidelity báo n_grounded=1, n_misattributed=0 nên block-gate không chặn (số có tồn tại trong ngữ cảnh, chỉ sai chủ thể).
- **B-047** [lech]: Chain c46 follow-up 'Vậy SKU đó còn hàng không?' — rewritten=None, không có bước rewrite nào chạy để resolve 'SKU đó' về 225/45ZR18 (đã hỏi ở B-046). Retrieve rơi vào SKU vô quan Rovelo 175R13C (top_score chỉ 0.4047). Bot trả tồn 0 của 2-R13C 175/13 (DB: price 954000/qty 0) thay vì 225/45ZR18 tồn 39 (DB: 2-ZR18 225/45 LPD price 1440000/qty 39). Đúng entity nhưng sai dòng vì mất coreference. Số bot đưa (954.000/0) là data thật của entity SAI.
- **B-050** [lech]: Chain c49 follow-up 'Tính giúp anh giá của 1 cặp (2 chiếc)' — rewritten=None, không carry entity 205/55R16 từ B-049. Retrieve chọn chunk 155/80R13 (top_score 0.5908) → bot tính cặp trên SKU SAI: 684.000×2=1.368.000. Đúng phải là 205/55R16 (DB: 2-R16 205/55 LPD price 1044000) → cặp 2.088.000. Phép tính đúng số học nhưng grounded vào entity sai dòng do đứt coreference. Giá 684.000 là data thật của 155/80R13 (DB confirm) nên không bịa số, chỉ sai entity.

## 2. BẢNG FULL 200 CÂU (id | verdict | tầng-fix)

| id | verdict | tầng | id | verdict | tầng |
|---|---|---|---|---|---|
| B-001 | ✅refuse_dung | - | B-002 | ❌sai_bia | block-gate |
| B-003 | ✅dung | - | B-004 | ✅dung | - |
| B-005 | ✅refuse_dung | - | B-006 | ✅refuse_dung | - |
| B-007 | ✅dung | - | B-008 | ✅refuse_dung | - |
| B-009 | ✅dung | - | B-010 | ✅refuse_dung | - |
| B-011 | ❌sai_bia | grounding-nonnumeric | B-012 | ❌lech | coreference |
| B-013 | ✅dung | - | B-014 | ✅dung | - |
| B-015 | ✅dung | - | B-016 | ✅dung | - |
| B-017 | ❌refuse_oan | retrieve | B-018 | ❌refuse_oan | retrieve |
| B-019 | ✅dung | - | B-020 | ✅dung | - |
| B-021 | ❌refuse_oan | retrieve | B-022 | ✅dung | - |
| B-023 | ✅dung | - | B-024 | ✅dung | - |
| B-025 | ✅dung | - | B-026 | ✅dung | - |
| B-027 | ✅dung | - | B-028 | ✅dung | - |
| B-029 | ✅refuse_dung | - | B-030 | ✅dung | - |
| B-031 | ❌sai_bia | grounding-nonnumeric | B-032 | ❌lech | block-gate |
| B-033 | ✅dung | - | B-034 | ❌sai_bia | grounding-nonnumeric |
| B-035 | ❌sai_bia | block-gate | B-036 | ✅dung | - |
| B-037 | ✅dung | - | B-038 | ✅dung | - |
| B-039 | ✅dung | - | B-040 | ✅dung | - |
| B-041 | ✅dung | - | B-042 | ✅dung | - |
| B-043 | ✅dung | - | B-044 | ✅dung | - |
| B-045 | ✅dung | - | B-046 | ✅dung | - |
| B-047 | ❌lech | coreference | B-048 | ❌refuse_oan | retrieve |
| B-049 | ✅dung | - | B-050 | ❌lech | coreference |
| B-051 | ✅dung | - | B-052 | ❌refuse_oan | retrieve |
| B-053 | ✅dung | - | B-054 | ✅dung | - |
| B-055 | ❌sai_bia | grounding-nonnumeric | B-056 | ❌refuse_oan | retrieve |
| B-057 | ✅refuse_dung | - | B-058 | ✅refuse_dung | - |
| B-059 | ❌lech | grounding-nonnumeric | B-060 | ❌refuse_oan | retrieve |
| B-061 | ✅dung | - | B-062 | ❌thieu | retrieve |
| B-063 | ❌sai_bia | grounding-nonnumeric | B-064 | ❌sai_bia | grounding-nonnumeric |
| B-065 | ❌thieu | retrieve | B-066 | ❌sai_bia | grounding-nonnumeric |
| B-067 | ✅dung | - | B-068 | ❌thieu | retrieve |
| B-069 | ❌thieu | retrieve | B-070 | ❌lech | retrieve |
| B-071 | ✅refuse_dung | - | B-072 | ✅refuse_dung | - |
| B-073 | ✅dung | - | B-074 | ✅dung | - |
| B-075 | ✅refuse_dung | - | B-076 | ✅refuse_dung | - |
| B-077 | ✅refuse_dung | - | B-078 | ✅dung | - |
| B-079 | ✅dung | - | B-080 | ✅refuse_dung | - |
| B-081 | ✅dung | - | B-082 | ✅dung | - |
| B-083 | ✅dung | - | B-084 | ✅dung | - |
| B-085 | ✅dung | - | B-086 | ✅dung | - |
| B-087 | ✅dung | - | B-088 | ✅dung | - |
| B-089 | ✅dung | - | B-090 | ✅dung | - |
| B-091 | ✅dung | - | B-092 | ✅dung | - |
| B-093 | ✅dung | - | B-094 | ✅dung | - |
| B-095 | ✅dung | - | B-096 | ✅dung | - |
| B-097 | ✅dung | - | B-098 | ✅dung | - |
| B-099 | ✅refuse_dung | - | B-100 | ✅dung | - |
| G-001 | ✅dung | - | G-002 | ✅dung | - |
| G-003 | ✅dung | - | G-004 | ✅dung | - |
| G-005 | ✅dung | - | G-006 | ✅dung | - |
| G-007 | ✅dung | - | G-008 | ✅dung | - |
| G-009 | ✅dung | - | G-010 | ✅dung | - |
| G-011 | ✅dung | - | G-012 | ✅dung | - |
| G-013 | ✅dung | - | G-014 | ✅dung | - |
| G-015 | ✅dung | - | G-016 | ✅dung | - |
| G-017 | ✅dung | - | G-018 | ✅dung | - |
| G-019 | ✅dung | - | G-020 | ✅dung | - |
| G-021 | ✅dung | - | G-022 | ✅dung | - |
| G-023 | ✅dung | - | G-024 | ✅dung | - |
| G-025 | ✅dung | - | G-026 | ✅dung | - |
| G-027 | ✅dung | - | G-028 | ✅dung | - |
| G-029 | ✅dung | - | G-030 | ✅dung | - |
| G-031 | ✅dung | - | G-032 | ✅dung | - |
| G-033 | ✅dung | - | G-034 | ✅dung | - |
| G-035 | ✅dung | - | G-036 | ✅dung | - |
| G-037 | ✅dung | - | G-038 | ✅dung | - |
| G-039 | ✅dung | - | G-040 | ✅dung | - |
| G-041 | ✅dung | - | G-042 | ✅dung | - |
| G-043 | ✅dung | - | G-044 | ✅dung | - |
| G-045 | ✅dung | - | G-046 | ✅dung | - |
| G-047 | ✅dung | - | G-048 | ✅dung | - |
| G-049 | ✅dung | - | G-050 | ✅dung | - |
| G-051 | ✅dung | - | G-052 | ✅dung | - |
| G-053 | ✅dung | - | G-054 | ✅dung | - |
| G-055 | ✅dung | - | G-056 | ✅dung | - |
| G-057 | ✅dung | - | G-058 | ✅dung | - |
| G-059 | ✅dung | - | G-060 | ✅dung | - |
| G-061 | ✅dung | - | G-062 | ✅dung | - |
| G-063 | ❌sai_bia | ingest | G-064 | ❌sai_bia | ingest |
| G-065 | ❌thieu | ingest | G-066 | ❌sai_bia | ingest |
| G-067 | ❌sai_bia | grounding-nonnumeric | G-068 | ❌sai_bia | ingest |
| G-069 | ✅dung | - | G-070 | ✅dung | - |
| G-071 | ✅dung | - | G-072 | ✅dung | - |
| G-073 | ✅dung | - | G-074 | ✅dung | - |
| G-075 | ✅refuse_dung | - | G-076 | ✅refuse_dung | - |
| G-077 | ❌sai_bia | grounding-nonnumeric | G-078 | ❌sai_bia | grounding-nonnumeric |
| G-079 | ✅refuse_dung | - | G-080 | ✅refuse_dung | - |
| G-081 | ✅refuse_dung | - | G-082 | ✅refuse_dung | - |
| G-083 | ✅refuse_dung | - | G-084 | ✅refuse_dung | - |
| G-085 | ✅refuse_dung | - | G-086 | ✅refuse_dung | - |
| G-087 | ✅refuse_dung | - | G-088 | ✅refuse_dung | - |
| G-089 | ✅refuse_dung | - | G-090 | ✅dung | - |
| G-091 | ✅dung | - | G-092 | ✅dung | - |
| G-093 | ✅dung | - | G-094 | ✅dung | - |
| G-095 | ✅dung | - | G-096 | ✅dung | - |
| G-097 | ✅dung | - | G-098 | ✅dung | - |
| G-099 | ❌thieu | retrieve | G-100 | ✅dung | - |