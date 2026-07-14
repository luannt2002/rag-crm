> # ⚠️ ĐÃ BỊ THAY THẾ bởi plan-v3.md
> Tính trên baseline `71682a2` (đã lệch). **18/20 fix trong đây bị verify SAI/CHƯA ĐỦ.**
> Dùng: [plan-v3.md](plan-v3.md) (HEAD 5fd6ecd, đã verify + red-team). Giữ file này làm lịch sử.

---

# [T1-Smartness] Expert-gap remediation — 17 fix thật / 12 fix bị loại

**Ngày**: 2026-07-14 · **Nhánh**: `fix-260623-ingest-expert`
**Evidence**: [`reports/TRUTH_VERIFICATION_20260713.md`](../../reports/TRUTH_VERIFICATION_20260713.md) — 29 mục verify bằng code + `request_steps` + `system_config` + `EXPLAIN` + `git blame`
**Nguồn cáo buộc**: `reports/EXPERT_AUDIT_MASTER_20260713.md` — **12/29 mục SAI**, đã loại.

---

## 0. LUẬT CỦA PLAN NÀY (rút từ đợt verify — vi phạm = reject)

| # | Luật | Vì sao |
|---|---|---|
| **L1** | Mỗi task PHẢI khai **`CONSTANT hay DB?`** | `system_config` **thắng** `constants.py`. B3 chứng minh: constant đã đúng 0.98 từ 9 ngày trước, DB ghim 0.88 → runtime vẫn sai |
| **L2** | Mỗi task PHẢI khai **`ĐÃ TỪNG FIX CHƯA?`** | B1 floor tune **3 lần** · D4 **ship→mất→vớt** · F1 **7 patch/12 ngày** · A1 fix `de89da8` **bị chính bug đó nuốt** |
| **L3** | **Số liệu hành vi PHẢI từ runtime**, không từ đọc code | B4: đọc constant ra "57.7%", runtime là **0.0%** |
| **L4** | **Không đo được ⇒ không ship** | B2 thiếu telemetry · F4 thiếu failing test → **làm telemetry/test TRƯỚC** |
| **L5** | **1 fix = 1 lần đo.** Không gộp | Gộp = không quy được nhân quả |
| **L6** | **Tái dùng pattern đã có**, không phát minh | B1 back-fill = pattern `mmr_filter` (002-D) đã ship |
| **L7** | **CẤM tính p95 trên mẫu bị cắt cụt** | `5c4fdda` thất bại vì survivorship bias |
| **L8** | Grep theo **tên METHOD**, không theo tên thuộc tính đoán | A4: grep `_idempotency` (sai) → tên thật `_idem` → false "dead code" trên hạ tầng chịu tải |

---

## 1. 🚫 DANH SÁCH CẤM — 7 thứ KHÔNG ĐƯỢC ĐỘNG VÀO

> Ship bất kỳ mục nào dưới đây = **gây hại thật**. Ghi ở đây để phiên sau không đào lại.

| Mục | Vì sao CẤM | Bằng chứng |
|---|---|---|
| **B4** gỡ `factoid` khỏi rerank skip-list | **no-op** trên traffic (0/741) + **vỡ ~10 assertion**/3 file | `request_steps`: `intent_skip_set = 0` |
| **A4** xóa/wire `IdempotencyService` | **Đang chạy đầy đủ** trên cả chat + ingest. Xóa = **phá retry-safety BE-to-BE** | 6 call site qua `self._idem` |
| **A9** bật `reflect` | **Tái tạo regression ĐÃ ĐO: 3.57s/turn** | `routing.py:201-206` — *"Production audit (req 9cf611b5) found reflect firing 2x per turn (3.57s wasted)"* → **GIỮ NGUYÊN COMMENT NÀY** |
| **A9** bật `graph_retrieve` | Tắt ở **3 tầng**, **không có KG nào để retrieve**. Bật = LLM call **per chunk lúc ingest** | `graph_rag_entity_extraction_model = ""` |
| **A8** bật `critique_parse` bằng flag | **KHÔNG BẬT ĐƯỢC BẰNG FLAG** — cần bot owner tự thêm rule vào `system_prompt`. **Sacred #10 CẤM app inject rule đó** | `critique_parser.py:1-21` |
| **F1d** sửa `test_crossdoc_reconcile.py:68` | Test **ĐÚNG** — nó ghim chống brand-conflation (ADR-0008 B5), không ghim bug | 2 row là **2 sản phẩm khác nhau** |
| **F1c** khôi phục `superseded_by`/`authority_score` | **Giàn giáo chưa từng có logic** — không có migration ADD nào tồn tại. Recency đã có sẵn `documents.updated_at` + `version` | migration 0010 docstring: *"drops **wired-but-unused** columns"* |

⚠️ **C1**: fix theo audit cũ (segment query-side) sẽ **GIẢM RECALL** (`&` → `<->` phrase). Fix đúng **ngược 180°** — xem T1.3.

---

## 2. ĐỢT 0 — CHẶN (làm ngay, song song)

### T0.1 🚨 E1 — cache hit bỏ qua `guard_output` **[LỖ HỔNG AN NINH LIVE]**

| | |
|---|---|
| **Bug** | `_cache_route` (`nodes/routing.py:56-59`) → `persist` → `END`. `guard_output` nằm **hẳn trên nhánh kia**. Cache hit **thực thi ZERO output guard** |
| **Cache key** | `cache_port.py:90` — **không có thành phần guardrail**. `bot_version` chỉ gồm `system_prompt` + `oos_answer_template` + `custom_vocabulary` |
| **Đóng vòng** | `GuardrailRuleLoader.invalidate()` **không bao giờ đụng** `semantic_cache` |
| **Cửa sổ** | **TTL 3600s** — owner thêm rule BLOCK → nội dung bị cấm vẫn được phục vụ **1 tiếng, không qua guard** |
| **CONSTANT hay DB?** | Code (topology graph) |
| **ĐÃ TỪNG FIX?** | ❌ **Chưa từng.** KẾ THỪA `cd08119`. Refactor 06-19 chỉ move code |
| **Fix** | **(1)** định tuyến cache hit **QUA `guard_output`** rồi mới `persist` — *guard mà bỏ qua được thì không phải guard* · **(2)** nhét hash ruleset guardrail vào `_compute_bot_cache_version` |
| **Blast** | (2) → flush cache lạnh **1 lần** khi deploy. (1) → thêm latency guard vào fast-path, nhưng guard chủ yếu regex/local → nhỏ |
| **Test đỏ trước** | `test_cache_hit_passes_guard_output.py` — assert cache-hit path chạm `guard_output`; assert sửa guardrail rule → cache key đổi |

### T0.2 🚨 A5 — CRAG grade: **fix `5c4fdda` (hôm qua) THẤT BẠI, đang gây REGRESSION**

| | |
|---|---|
| **Bug** | `DEFAULT_GRADE_TIMEOUT_S` 2.0→3.0 **KHÔNG cứu được lần grade nào**: **30 attempt @3.0s → 30 TIMEOUT, 0 success** (avg 3015ms). Chỉ **tăng thời gian đốt** 2s→3s/query |
| **Root cause của FIX SAI** | **SURVIVORSHIP BIAS.** "p95 = 2.56s" tính **chỉ trên grade HOÀN TẤT** = chỉ những lần **thắng cap 2.0s cũ**. 306 lần timeout là **dữ liệu bị kiểm duyệt phải** (latency thật ≥2.0s, không biết). **Không thể ước lượng p95 từ mẫu cắt cụt tại p5** |
| **Trạng thái** | 741 grade: **418 skip · 306 timeout (đốt 647s, vứt kết quả) · 17 thật (2.3%)**. `rewrite_retry` chạy **1 lần EVER**, và lần đó **rewrite ra query GIỐNG HỆT byte gốc, trong 5ms** → **CRAG CHƯA BAO GIỜ chạy thông** |
| **Hiện tại TỆ HƠN CẢ 2 phương án** | Tệ hơn CRAG chạy (cùng chi phí, 0 lợi ích) · Tệ hơn không CRAG (thuế latency thuần) |
| **CONSTANT hay DB?** | Constant (`grade_timeout_s` **không có** trong `system_config`) → sửa constant CÓ tác dụng |
| **ĐÃ TỪNG FIX?** | 🚨 **CÓ — HÔM QUA, VÀ THẤT BẠI.** `cd08119` → `24f2451` → `5c4fdda` |
| **Fix — 2 BƯỚC, CẤM ĐẢO** | **B1**: đo latency grader **KHÔNG TIMEOUT** (1 load-test) → lấy phân phối THẬT. **B2**: theo số thật → nâng cap đủ **HOẶC** `grade_timeout_s = 0` (tắt, thu hồi 647s) |
| **Điều tra kèm** | `model_used = "openai/claude"` — cặp provider/model **dị dạng**, nghi là nguồn latency · grade row có `input_tokens=0, cost_usd=0` kể cả path thành công → **chi phí CRAG đang VÔ HÌNH** |
| **Chi phí CRAG nếu chạy (ĐO, không đoán)** | `grade_use_batch=true` → **1 LLM call batch/query** (≤50 chunk), **không phải 1/chunk**. `max_grade_retries=1` → correction thêm tối đa **+3 LLM call**, typical **+1** |

### T0.3 🔧 X2 — **1 test đang ĐỎ tại HEAD**

```
tests/unit/orchestration/test_per_intent_caps.py::test_default_constant_aggregation_loosens_threshold
E   AssertionError: aggregation must get a LOOSER MMR threshold than the default
E   assert 0.98 > 0.98
```
Vỡ do `9f93804` fix nửa vời. **Không ship thêm lên nền đỏ.** Xử lý cùng T2.2 (cùng vùng MMR).

---

## 3. ĐỢT 1 — A1 & bạn bè **(ĐỔI CORPUS → VÔ HIỆU MỌI PHÉP ĐO TRƯỚC NÓ)**

> ⚠️ **3 fix này ĐỀU cần re-ingest/reindex → GỘP THÀNH 1 LẦN.** Làm xong phải **ĐO LẠI BASELINE** — mọi số trước đợt này chết.

### T1.1 🔥 A1 — worker không truyền `raw_bytes` → **parser registry CHẾT trên luồng production** [BUG #1]

| | |
|---|---|
| **Bug** | `ingest_core.py:317` gate `if raw_bytes is not None:` · worker `document_worker.py:514` **tự parse rồi flatten** `"\n\n".join(...)`, gọi `ingest(content=full_text)` — **KHÔNG có `raw_bytes`** → `parser_row_chunks = None` → **luôn rơi vào `smart_chunk`** |
| **Vì sao trốn được lâu** | 🎯 **UI test nội bộ TRUYỀN `raw_bytes`** (`test_chat/document_routes.py:521`). **API production B2B thì KHÔNG.** → dev thấy chạy đúng, khách nhận chunking phẳng |
| **Runtime** | **0/583 chunk** được row-parse. 5 doc CSV đều `recursive`, dù `GoogleSheetsParser.supports("text/csv") = True` và `google_sheets ∈ _ROW_PRESERVE_PROVIDERS` |
| **ĐÃ TỪNG FIX?** | ⚠️⚠️ **CÓ — VÀ BỊ CHÍNH BUG NÀY NUỐT.** `de89da8` (07-01) fix `col_N` gate trên `_parser_row_shaped(parser_row_chunks)` — **luôn `None` trên worker → fix ĐÓ CŨNG LÀ CODE CHẾT.** Bằng chứng: 3 doc ingest **07-06 (5 ngày SAU fix)** vẫn `recursive`; doc được **nêu đích danh trong commit message đó** vẫn chưa fix |
| **Tác động T1** | `col_N` corruption → **mất binding header per-row** → chính là **lớp bug bịa số** mà ADR-0008 đang đuổi |
| **CONSTANT hay DB?** | Code (wiring) |
| **Fix** | worker truyền `raw_bytes=_raw`, **bỏ `"\n\n".join(...)` flatten** |
| **Blast** | **5 doc CSV / 583 chunk** (corpus live không có xlsx/sheets mime). Re-ingest idempotent. Rủi ro: chunk count tăng (63 hàng → 63 chunk) → tăng chi phí embed/doc |
| **Test đỏ trước** | assert doc CSV qua `POST /documents/create` → `chunking_strategy == "parser_preserve"` |

### T1.2 F5 — intro/footer bảng: **flag LIVE-TRUE nhưng TRƠ** + 🔴 **DRIFT DB prod vs fresh**

| | |
|---|---|
| **Bug** | Feature **tồn tại & bật** (`table_csv_emit_header_footer_chunks_enabled = true`) nhưng **strategy live là `table_dual_index`**, mà `_chunk_table_dual_index` **không nhận** `header_footer_enabled` và cắt `lines[header_idx : last_data_idx+1]` → **`pre`/`post` bị loại trừ VỀ CẤU TRÚC** |
| **ĐÃ TỪNG FIX?** | ⚠️ **Một fix CỐ Ý đã ÂM THẦM regress cái này.** `20260612_0209` lật `table_csv` → `table_dual_index` để **fix aggregation recall** — **quên port logic pre/post** |
| **Test tạo niềm tin GIẢ** | `test_chunk_table_csv_header_footer.py` gọi **TRỰC TIẾP** `_chunk_table_csv_with_context`, **không qua dispatch live** → **6/6 test XANH trong khi prod vứt intro/footer** |
| 🔴 **DRIFT NGHIÊM TRỌNG** | **KHÔNG có migration ACTIVE nào seed `chunking_policy`.** Seed chỉ nằm trong archive pre-squash; `20260618_squash_baseline.py` **không mang theo** → **DB fresh rơi về `table_csv`** → **header/footer CHẠY trên dev, HỎNG trên prod. Dev/CI KHÔNG THỂ tái hiện table-chunking của prod** |
| **CONSTANT hay DB?** | **DB** (`system_config.chunking_policy`) — và **thiếu seed** |
| **Fix** | (1) port `region.pre`/`region.post` vào `_chunk_table_dual_index`, **tái dùng** param + constants đã có (~10 dòng) · (2) **mở rộng test chạy qua DISPATCH LIVE** · (3) 🔒 **alembic ACTIVE seed `chunking_policy`** để fresh DB khớp prod |
| **Blast** | ⚠️ **cần RE-INGEST** — chính migration 0209 đã cảnh báo |

### T1.3 C1 — gỡ segmentation ở **INGEST** (🔴 **NGƯỢC 180° so với audit cũ**)

| | |
|---|---|
| 🔴 **Audit cũ SAI** | Nói *"index lưu `chăm_sóc`, query tìm `chăm AND sóc` → miss"*. **SAI.** Postgres coi `_` là **`blank` = dấu phân cách**, nó **XÓA** underscore: `to_tsvector('simple','chăm_sóc da mặt')` → `'chăm':1 'da':3 'mặt':4 'sóc':2`. **ZERO lexeme từ ghép VN tồn tại trong index.** Query hiện tại **ĐANG ĐÚNG** (live: 20 hit). **Segment query sẽ biến `&` → `<->` (phrase) → GIẢM recall** |
| 🔥 **Bug THẬT (ngược chiều)** | Segmentation ở **INGEST** **PHÁ HỦY token**. Đo live: 1 brand token có **28 chunk chứa nó** → index hiện tại tìm ra **4** → không segment thì **28**. **24/28 chunk BẤT KHẢ TRUY CẬP.** Toàn corpus **436/906 chunk** có `to_tsvector(content) ≠ to_tsvector(content_segmented)`; token mất là loại đắt nhất: **tên thương hiệu + token đơn-giá** |
| **Comment nói dối** | `pgvector_store.py:406-408` bảo *"query phải mirror content_segmented"* — **sai sự thật, parser xóa `_`** |
| **Test ghim BUG** | `test_bm25_symmetric_segment.py` assert query phải segment giống ingest → **test đang BẢO VỆ bug** |
| **ĐÃ TỪNG FIX?** | ⚠️ `be94f58` "expert remediation (Wave2)" có fix (một phần) + test → **CHƯA MERGE**, kẹt trên `integ-260624-wave1`. **KHÔNG merge gate của nó** — gate cho call sắp xóa là vô nghĩa |
| **Fix** | (1) trigger index `NEW.content` thay vì `COALESCE(NEW.content_segmented, NEW.content)` · (2) **xóa** 2 call `segment_vi_compounds` query-side (`pgvector_store.py:409,417`) *(= T2.4, gộp vào đây)* · (3) **nghỉ hưu** `test_bm25_symmetric_segment.py` |
| **Blast** | ⚠️ **CẦN REINDEX tsvector toàn corpus** (trigger-materialised). **KHÔNG cần re-ingest** (source text không đổi). `content_segmented` để nguyên (vô hại) |

### T1.4 ⚠️ **RE-INGEST + REINDEX 1 LẦN** → **ĐO LẠI BASELINE**

Mọi số liệu trước đợt này **CHẾT**. Không được so sánh chéo qua ranh giới này.

---

## 4. ĐỢT 2 — QUERY-SIDE (an toàn, không đụng corpus) · **1 fix = 1 lần đo**

### T2.1 B1 — cliff back-fill `min_keep` (**bug THỨ TỰ, KHÔNG phải số**)

| | |
|---|---|
| **Runtime** | **134/741 = 18.1%** query vào LLM với **đúng 1 chunk**. Nhánh gap-cut (nhánh **duy nhất** `min_keep` bảo vệ) chỉ bắn **3/741 = 0.4%** |
| **Code** | `retrieval_filter.py:127` cắt floor **trước**, `:130`/`:139` trả **1 chunk**, `:154` `min_keep` chỉ gác gap-cut |
| **Ý định tác giả** | comment `_01:164-169`: *"Default 3 (không phải 1): một lần reranker chấm sai KHÔNG được làm sập tập chunk xuống một"* → **code làm ngược ý định của chính nó** |
| **CONSTANT hay DB?** | **DB** — `rerank_cliff_absolute_floor`/`min_keep` **có trong `system_config`** → sửa constant = **0 tác dụng** |
| **ĐÃ TỪNG FIX?** | ⚠️ **CÓ — 3 LẦN**: `0.15` (alembic 0068) → `0.05` (gây REFUSE_GAP, có load-test) → `0.2` (`c0c0dea`). `test_cliff_floor_calibrated.py` **đang canh** window `[0.0, 0.20]`, cảnh báo >0.20 tái hiện REFUSE_GAP. **0.2 nằm ĐÚNG TRÊN TRẦN** |
| **Fix** | 🔒 **KHÔNG ĐỘNG VÀO SỐ.** Đổi **thứ tự**: sau khi cắt floor, nếu `len(floor_kept) < min_keep` → **back-fill** từ `sorted_chunks`. **Tái dùng pattern `mmr_filter`** (`DEFAULT_MMR_MIN_KEEP`, 002-D đã ship y hệt) |
| **Test đỏ trước** | `len(out) >= min_keep` khi đầu vào đủ chunk — **invariant MỚI hợp lệ**, không phải gaming |
| **Phát hiện kèm** | 🔴 **`rerank_cliff_gap_ratio` DRIFT**: DB = **0.5**, constant = **0.35**. Không document → cần quyết định |

### T2.2 B3 — **ALEMBIC** MMR 0.88 → 0.98 (+ fix test đỏ X2)

| | |
|---|---|
| 🔴 **Constant ĐÃ ĐÚNG (0.98)** | Sửa `constants.py` = **0 tác dụng**. `system_config` ghim **0.88** bằng **alembic ĐÃ APPLY** (`20260709_seed_cliff_floor_mmr_parity.py`) — docstring: *"chỉ ghim giá trị production hiện tại (0.88); việc nâng lên 0.98 là **một quyết định đo-lường RIÊNG**"* |
| **Runtime** | `factoid`: **4.77 → 3.19 chunk (−33%)** @ threshold 0.880 |
| **Số 0.98 ĐÃ ĐƯỢC ĐO** | `9f93804` commit body: *"zembed-1, cosine giữa các section KHÁC NHAU CÙNG doc (p50 0.975, max 0.990) chồng gần hoàn toàn lên dải near-duplicate; **0.88 cũ dedup nhầm 100% cặp section phân biệt**, làm sập doc 6→1 và bỏ đói generate → bịa"*. **KHÔNG cần đo lại** |
| **ĐÃ TỪNG FIX?** | ⚠️ **fix NỬA VỜI** — đo xong, sửa constant, **quên flip DB + quên map per-intent** |
| **Fix** | **Alembic mới**, update **CẢ HAI**: (1) `mmr_similarity_threshold` 0.88→0.98 · (2) **`mmr_similarity_threshold_by_intent.factoid` 0.88→0.98** ← **thiếu cái này thì (1) VÔ NGHĨA** (map per-intent thắng ở `mmr_dedup.py:37`). Rồi sync `_14` |
| **Test** | `test_per_intent_caps.py:242` đổi `aggregation > global` → `>=` — **HỢP LỆ**: invariant cũ là workaround cho phân phối embedding TRƯỚC swap; global recalibrate đúng thì nới per-intent là **thừa theo cấu trúc**. **Đây cũng là fix cho test đỏ X2** |
| **KHÔNG động** | `DEFAULT_MMR_MIN_KEEP = 3` — đã live, đang bảo vệ sàn |

### T2.3 C2 — NFC normalize cho dense query

| | |
|---|---|
| **Bug** | Ingest normalize ✔ · sparse query normalize ✔ · **dense query KHÔNG**. `_embed_query` (`query_graph.py:1553`) đưa `query_text` thẳng vào `embed_one`. `grep normalize_vn src/ragbot/infrastructure/embedding/*.py` → **0 hit** → query NFD (macOS/iOS) **embed lệch không gian** với corpus NFC |
| **CONSTANT hay DB?** | Code |
| **ĐÃ TỪNG FIX?** | ❌ Chưa. KẾ THỪA. Dòng `:391` (sparse) **chưa từng đụng lại** |
| **Fix** | `normalize_vn(query_text)` **1 lần ở đầu `_embed_query`**, **TRƯỚC cache lookup** (`:1606`) → cả cache key lẫn wire text đều canonical |
| **KHÔNG** | Nhét vào embedder adapter — đẩy mối bận tâm VN-specific vào provider strategy = **vi phạm domain-neutral** |
| **Blast** | **Thấp.** NFC idempotent → no-op với traffic NFC. Cache key NFC/NFD sẽ gộp (đổi key, không phải rủi ro correctness). **Không reindex** |

### T2.4 C3 — xóa `segment_vi_compounds` query-side → **gộp vào T1.3**

### T2.5 F1 — multi-doc provenance + conflict event (**mở rộng ADR-0008, KHÔNG phải patch điểm thứ 8**)

| | |
|---|---|
| **Bug (a)** | `query_graph.py:2620` `_key = (_name, price)` → **cùng giá thì GỘP, KHÁC GIÁ thì CẢ HAI SỐNG** → dedup làm **đúng ngược** conflict resolution. **Không test nào ghim** → fix không vỡ gì |
| **Bug (b)** | Synthetic chunk `"document_name": ""` (`:2649`), không ngày, `"score": 1.0` (`:2650`) → **LLM thấy N dòng giá mâu thuẫn với ZERO quy kết nguồn** |
| 💡 **Fix RẺ HƠN TƯỞNG** | `stats_index_repository.py:57` **ĐÃ CÓ** `_DOC_LIVE_JOIN = "JOIN documents AS d ON d.id = dsi.record_document_id"`, và **mọi SELECT đã trả `record_document_id`**. → **Provenance chỉ cách 1 CỘT SELECT. KHÔNG migration. KHÔNG re-ingest** |
| **ĐÃ TỪNG FIX?** | ⚠️⚠️ **CAO NHẤT AUDIT — 7 patch/12 ngày**: `949a3a4 · aa029ec · d4de411 · ec4a335 · eb750f0 · 2ad4df7 · d495db2` + `ed26e1b` (explored+reverted). **Vòng thrash ĐANG CHẠY.** Patch thứ 8 mà phớt lờ ADR-0008 → lặp lại |
| **ADR-0008 ĐANG QUẢN** | **B4**: *"synthetic chunk KHÔNG ĐƯỢC đè bẹp raw chunk đúng khi confidence thấp: score phải phản ánh match confidence"* → **`score: 1.0` CHÍNH LÀ B4, chưa fix** · **B5**: cross-doc merge phải khớp identity |
| **Fix (Tầng 1 — 0 migration, 0 re-ingest)** | (1) thêm `d.document_name` + `d.updated_at` vào SELECT/JOIN đã có · (2) luồn vào entity dict → điền `document_name` synthetic chunk · (3) **GROUP BY `_name`, phát hiện >1 giá phân biệt = CONFLICT** → phục vụ **CẢ HAI** row **KÈM tên doc + ngày** · (4) phát `stats_price_conflict` event (**hôm nay mâu thuẫn diễn ra HOÀN TOÀN IM LẶNG**) · (5) bỏ `score: 1.0` vô điều kiện |
| 🔒 **SACRED GUARD** | **CẤM hardcode "mới nhất thắng" trong app** — đó là **app-override answer (QG#10)**. Ưu tiên độ mới thuộc `system_prompt` của owner. **App cấp DỮ LIỆU (tên + ngày). LLM QUYẾT ĐỊNH** |
| **Tầng 2 (chỉ khi owner cần)** | authority/validity → **manifest per-file của ADR-0008**, **KHÔNG hồi sinh 4 cột bespoke**. `documents.updated_at` + `version` **đã có** → recency **không cần cột mới** |
| **Test** | Có thể phải sửa format: `test_stats_synthetic_null_price_marker.py`, `test_stats_serve_value_filter.py`, `test_stats_query_attributes_selected.py`. **`test_crossdoc_reconcile.py` KHÔNG ĐỘNG — nó đúng** |

---

## 5. ĐỢT 3 — DỌN RÁC + LAN CAN (không đổi hành vi)

| # | Task | Nội dung |
|---|---|---|
| **T3.1** | **D1 — XÓA 3 comment NÓI DỐI + thêm lan can HNSW** | 🔴 Root cause em nói **SAI**: không phải opclass/cột/filter. Agent **phản chứng quyết định**: bỏ hẳn filter → planner **VẪN Seq Scan**. **Thật là cost model**: 906 row → seq-scan cost **285** vs HNSW startup **5475** (thừa 19×) → **planner ĐÚNG**. **KHÔNG mất recall hôm nay** (seq scan = exact). ⚠️ **`plans/20260709-remediation-donow/plan.md:13` ĐÃ TRIAGE ĐÚNG** — nhưng **quên xóa comment dối** → chẩn đoán sai sống mãi (nó vừa lừa cả em). **Fix**: (1) xóa/sửa comment `:4`, `:226-238`, `:257` ← **đây MỚI là defect** · (2) `SET LOCAL hnsw.iterative_scan = 'relaxed_order'` cạnh `SET hnsw.ef_search` (`:322`, `:404`), lấy từ `system_config` — **no-op hôm nay**, là lan can khi tới ngưỡng (~17k chunk, **ngoại suy**) |
| **T3.2** | **D3 — dim-guard per-vector** | Dim check **chỉ có ở `health_check` (warmup)**, KHÔNG ở hot path. `:555-560` chỉ đọc `embed_results[0]`, nuốt `TypeError`, **chỉ là metadata audit**. Wire dim = **ctor 1280**, `spec.dimension` **không bao giờ đọc**. 2 default xung khắc: `DEFAULT_EMBEDDING_DIM=1024` vs `DEFAULT_ZEROENTROPY_EMBEDDING_DIM=1280`. **Fix**: (1) dim check per-vector sau count-guard `:521`, dùng lại đường soft-delete + `ExternalServiceError` · (2) wire dim từ `spec.dimension`. ⚠️ **CHẶN: audit `ai_models.dimension` TRƯỚC** — row nào ≠1280 sẽ bị cột `vector(1280)` từ chối |
| **T3.3** | **D2 — cột provenance embedding** | Cơ chế thật (`20260626_embed_swap_to_openai` **chỉ UPDATE 3 dòng config, KHÔNG re-embed vector nào**; docstring tự thú *"REQUIRES re-embedding"* — **cưỡng chế bởi KHÔNG GÌ CẢ**). 🔴 **Lỗ hổng sâu hơn**: `_check_embed_model_consistency` so config-với-config → **sau swap chúng KHỚP** → **bất lực về cấu trúc** trong việc phát hiện vector cũ. 🟢 **Thiệt hại live REFUTED** (906/906 chunk sau ngày swap; dim 1280 nhất quán) — **nhưng chỉ biết nhờ MAY MẮN** (`created_at` tình cờ sau swap). **Fix**: thêm `embedding_model` + `embedding_dim` vào `document_chunks`, so model query với **model GHI TRÊN ROW**, fail-loud (gate warn→block per-bot) |
| **T3.4** | **F2/F3 — xóa nhánh + node chết** | **F2**: 2 coroutine vô dụng (`_run_router_select_model` = *"purely observability"* nhưng **có resolver round-trip THẬT**; `_run_semantic_cache_preflight` = *"Returns `{}` always"*) → **2 row `request_steps` + 1 DB round-trip/query, zero tác dụng**. *(Sửa số em nói: **2 row, không phải 3** — row thứ 3 được `_complexity_route` dùng thật)*. **F3**: `pipeline_merge_condense_router = TRUE` live, **0 bot override**, `query_router_provider = "null"` → `condense_question` + `router` **chết trên MỌI query >2 tháng**. ⚠️ **KHÔNG xóa `_router_route`** — vẫn sống, gọi bởi `_understand_query_route` + `_complexity_route`. 🔴 **Zero-hardcode violation**: default `True` inline ở **3 chỗ**, không constant. Xóa row `system_config` mồ côi bằng **alembic, KHÔNG psql** |
| **T3.5** | **A3 — XÓA `null_embedder`** | 🔴 **Tác động em nói SAI**: registry **ĐÃ degrade an toàn** không cần Null Object — `registry.py:93` `cls = _REGISTRY.get(key, _REGISTRY[DEFAULT_EMBEDDING_PROVIDER])` → rơi về embedder **THẬT**, không bao giờ raise. Và bản commented **RAISE `EmbeddingError` mọi call** → **vi phạm chính hợp đồng Null-Object của CLAUDE.md**. File có sẵn **DEAD-CODE NOTICE** cho phép xóa. **Wire nó vào = CÓ HẠI** |
| **T3.6** | **D4 — sửa locator coverage gate** | 🔴 **Thủ phạm em đổ SAI**: **KHÔNG phải `proposition`** (không live). Là **`_chunk_hdt` — 217 chunk LIVE** prepend `[path]\n` → `find() == -1` → **`coverage_ratio = 0.0000` dù KHÔNG mất gì**. Repro: prefix → 0.0000 · verbatim → 0.8462. ⚠️ **CẤM tune `DEFAULT_COVERAGE_TOL`** — ratio **vô nghĩa về cấu trúc**, không số `tol` nào sửa được `find() == -1`. **ĐÃ TỪNG FIX**: ship→mất→**"salvaged from Wave-1"**. **Fix**: strategy trả path prefix **qua METADATA**, không nối vào chuỗi chunk. **Sửa locator TRƯỚC; CHỈ SAU ĐÓ mới bàn cưỡng chế gate** (gate cưỡng chế trên corpus báo 0.000 sẽ **từ chối gần như mọi doc `hdt`**) |

---

## 6. ĐỢT 4 — QUYẾT ĐỊNH (0 code, hoặc cần A/B)

| # | Task | Nội dung |
|---|---|---|
| **T4.1** | **E3 — 1 ADR, 0 code, ~1-2h** | 🔴 **Cáo buộc REFUTED — em VU OAN CODE TỐT.** Default là **`observe`**, không phải block. `system_config` **không có key**; 1 bot duy nhất set nó = **`observe`**. Commit bị vu oan (`c0c0dea`) để lại comment: *"Default 'observe' để **không bot nào bị đổi refuse-rate mà không opt-in tường minh**; owner chỉ flip 'block' **SAU KHI ĐO**"* → **đây chính xác là kỷ luật CLAUDE.md yêu cầu**. **Phát hiện THẬT**: **8 commit guard / 7 ngày, 0 ADR** — bề mặt app-override đang **nới rộng từng bot một**, "owner-approved" **chỉ nằm trong commit message**. **Fix**: **1 ADR** phủ cả họ (`grounding_confirmed_action`, `grounding_failure_mode`, `numeric_fidelity_action`, brand-scope block, empty-answer guard): dùng **`oos_answer_template` của CHÍNH BOT**; **per-bot opt-in, default observe**; **owner approval ghi TRONG ADR**, không chỉ commit message |
| **T4.2** | **E2 — ADR-0009 + 🔒 GIẾT gate-theo-NGÀY-SINH** | ✅ CONFIRMED cả 3: (a) `XML_WRAP_DEFAULT_ON_FROM_DATE = "2026-05-18"` · (b) **KHÔNG** hiện trong `GET /admin/bots/{id}/effective-prompt` (nó chỉ render **system** prompt; XML wrap inject vào **user** message) · (c) **0 ADR**. 🔴 **LIVE: 4/6 bot đang bị XML-wrap, KHÔNG owner nào set** — bật **chỉ vì NGÀY TẠO ROW BOT**. → **2 bot giống hệt nhau, khác ngày sinh, nhận PROMPT KHÁC NHAU**. ⚖️ **Công bằng**: `<documents>`/`<question>` envelope là **hợp đồng cấu trúc** (sysprompt template **tham chiếu nó**), không phải rule lậu. Token **thật sự mang rule** là `trust="data_only"`. **Fix = QUYẾT ĐỊNH**: **Option A (khuyến nghị)** viết ADR-0009 theo đúng 4 điều kiện ADR-W1-S10 (seed migration tracked · domain-neutral · per-bot opt-out **đã có** · **owner XEM ĐƯỢC** → mở rộng `effective-prompt` render CẢ user message) + **XÓA gate-ngày**, thay bằng default per-bot ghi lúc tạo bot. **Option B** gỡ wrap → **rủi ro regression chất lượng trên 4 bot live, PHẢI A/B**. 🔒 **KHÔNG THƯƠNG LƯỢNG bất kể A hay B: prompt của bot KHÔNG BAO GIỜ được phụ thuộc NGÀY SINH của nó** |
| **T4.3** | **B2 — telemetry TRƯỚC, fix seam SAU** | ✅ Seam thật (`0.45 < 0.6` → fallback recursive phát confidence mà L5 **chắc chắn từ chối**). 🔴 **2 vế em nói SAI**: recursive **KHÔNG** unreachable (thắng được bằng max-score ≥0.6); override target là **`hybrid`**, **không phải `proposition`** (DB xác nhận: live = `recursive` 689 + `hdt` 217, **`proposition` KHÔNG LIVE**). Gốc rễ: `0.6` **có nguồn** (Databricks 2024, ghi trong comment), `0.45` **KHÔNG có bất kỳ lời giải thích nào** — 2 số từ 2 nguồn, **chưa bao giờ đối chiếu**. ⚠️ **CHẶN (L4)**: strategy **chỉ log structlog**, `metadata_json` null 902 row, `audit_log` 0 event → **KHÔNG ĐO ĐƯỢC**. → **BƯỚC 1: persist `chunk_strategy` vào metadata. BƯỚC 2: đo. BƯỚC 3 mới fix contract selector** (trả confidence THẬT, KHÔNG nhích 0.45→0.6 — chỉ che bug) |
| **T4.4** | **A7 — A/B `neighbor_expand`** (CHỈ SAU T1) | 🧪 **Thí nghiệm T1 tốt nhất hiện có**: docstring — *"context rộng hơn cho LLM **KHÔNG cần** thêm embedding hay LLM call — chi phí là **1 SQL round-trip batched**"* → **+0 LLM call**. Corpus đang `recursive` ~700-1400 char với **cắt giữa bảng** — **đúng điều kiện fragmented-context mà neighbor-expand sinh ra để vá**. ⚠️ **LÀM A1 TRƯỚC** — A1 đổi chunking thì phải chạy lại. Rủi ro: pha loãng context (đã có `DEFAULT_NEIGHBOR_TOKEN_BUDGET`) |
| **T4.5** | **A2 — `rrf_round_robin`: ĐO rồi WIRE hoặc XÓA** | Docstring tự thú: *"safe to wire into the retrieve node **later** (S2 owns query_graph.py)"* → **bàn giao bị bỏ rơi**. ⚠️ **Rule #0: hiện KHÔNG có bằng chứng runtime nào cho thấy entity-starvation đang hại answer.** → chạy **1 load-test intent `comparison`**; có starvation → wire ở `retrieve.py:1448` với `per_entity_quota` default 0 (**no-op**); không có → **XÓA** (đừng để test-dead-code mục thêm 26 ngày). **KHÔNG wire mù.** Cần `entity_of(chunk)` supplier → luồn brand-scope signal đã có (`d495db2`) |
| **T4.6** | **F4 — failing test TRƯỚC, rồi mới flip atomic protect** | `DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED = False`, key **vắng** khỏi `system_config`, **0 bot override**. Sắc thái: flag gate `_split_into_blocks_with_atomic` — table fast-path **return TRƯỚC** → chỉ áp cho strategy **KHÔNG-phải-bảng**. ⚠️ **CHƯA VERIFY hậu quả** — chưa ai chạy chunker trên doc formula/code để **quan sát** một lần cắt giữa block. **Rule #0: cần failing test** (fenced code block > `chunk_size` → assert không split) **TRƯỚC** khi tuyên bố defect và **TRƯỚC** khi flip |

---

## 7. Định nghĩa DONE

Mỗi task chỉ được đánh ✅ khi đủ **4 điều**:

1. **Red test trước** — test fail tái hiện bug, **trước** khi sửa code
2. **Fix tối thiểu** — mọi dòng trace ngược về task; không drive-by refactor
3. **Đo sau** — số runtime thật (`request_steps` / load-test / pytest output), **1 fix = 1 lần đo**
4. **Khai đủ 3 cột** — `CONSTANT hay DB?` · `ĐÃ TỪNG FIX CHƯA?` · `Blast radius`

**Quality Gate 11/11 + sacred rule** check trước mỗi commit.

---

## 8. Trạng thái

| Đợt | Task | Status |
|---|---|---|
| 0 | T0.1 E1 cache→guard · T0.2 A5 grade re-measure · T0.3 test đỏ | ⬜ TODO |
| 1 | T1.1 A1 raw_bytes · T1.2 F5 table · T1.3 C1 unsegment · T1.4 re-ingest+baseline | ⬜ TODO |
| 2 | T2.1 B1 · T2.2 B3 · T2.3 C2 · T2.5 F1 | ⬜ TODO |
| 3 | T3.1 D1 · T3.2 D3 · T3.3 D2 · T3.4 F2/F3 · T3.5 A3 · T3.6 D4 | ⬜ TODO |
| 4 | T4.1 E3 ADR · T4.2 E2 ADR · T4.3 B2 · T4.4 A7 · T4.5 A2 · T4.6 F4 | ⬜ TODO |
