# MASTER AUDIT — Ragbot core RAG · toàn bộ luồng · bug · best-practice · fix roadmap

> **Ngày:** 2026-07-13 · **Nhánh:** `fix-260623-ingest-expert` · **Anchor:** `09546f8`
> **Phạm vi:** tổng hợp toàn bộ phiên audit — deep-dive 6 luồng, audit ON/OFF, đối
> chiếu best-practice, cơ chế core RAG, đối chiếu bug QA thật (2 bot × 20 câu).
> Đây là **điểm vào duy nhất**; chi tiết ở 6 report con (liên kết cuối file).
>
> **rule#0:** con số "đo thật" = từ QA/load-test/log; con số chưa đo gắn nhãn
> **GIẢ THUYẾT**. Không tuyên bố % lift chưa đo.

---

## 0. EXECUTIVE SUMMARY (đọc cái này trước)

**Ragbot KHÔNG "bug tùm lum".** QA thật: 2 bot đều **17/20 đạt (85%)**, khung code **expert-grade** (Hexagonal + Port/DI + multi-tenant RLS + anti-HALLU deterministic — vượt SOTA phổ thông).

**Nhưng có 2 bug nặng + coverage gap**, và tất cả quy về **một meta-nguyên nhân**:

> **Hệ thiết kế theo triết lý "fail-open + defense-in-depth" — mỗi tầng ưu tiên TRẢ LỜI hơn CHẶN, tin tầng cuối bắt lỗi. Nhưng các lưới an toàn deterministic ĐÃ CÓ CODE lại để "observe" (không chặn) mặc định, và có loại lỗi (degeneration) không tầng nào bắt. → Fail-open + lưới cuối không chặn = lỗi chảy thẳng ra user.**

**Bug không nằm ở 1 node — nằm ở "chuỗi nhường trách nhiệm chưa có điểm dừng cứng".** Fix đúng = đặt lại điểm dừng cứng đúng tầng, KHÔNG phải đập code.

**Kết luận production:** khung không cần đập. Cần **đóng 3 vòng lặp vận hành** (measure→enforce · taxonomy lỗi · cấu hình chịu lỗi) + checklist bàn giao bot. **Dùng thật OK cho bot hỏi-đáp/bán lẻ đơn giản; CHƯA cho pháp lý/tải cao.**

---

## 1. BUG THẬT (từ QA 2 sheet — bằng chứng cứng)

### Bot xe (lốp) — 17/20 đạt
| # | Câu fail | Loại | Root cause |
|---|---|---|---|
| **#13** | Neoterra 195/65R16 (NULL giá) → bịa "260.000đ, tồn 26" | 🔴 HALLU | Path B raw-chunk chứa số rác `date1="26"`; numeric gate=observe không chặn |
| #2 | Info công ty chuyển khoản | coverage | Data chỉ ở sysprompt, không vào KB |
| #20 | So sánh 205/65R16 vs 235/40R18 | coverage | 235/40R18 có trong KB nhưng retrieval miss vế đó |

### Bot pháp lý (Thông tư 09/2020) — 17/20 đạt
| # | Câu fail | Loại | Root cause |
|---|---|---|---|
| **#8** | "phạm vi điều chỉnh" → **LẶP VÔ HẠN** hàng trăm lần | 🔴 nghiêm trọng | Không có degeneration guard; penalty=0 + không wire tới LLM |
| #14 | Ngày ban hành 21/10/2020 | coverage | Chunk có (score 1.0) nhưng LLM không trích ngày |

**Đang LÀM TỐT (bằng chứng QA):** chống bịa hãng ngoài KB (Bridgestone, Michelin) · bẫy phòng thủ (Điều 50/99 giả, tiền đề sai) đều giữ · tra giá đúng khi có giá (score 1.0) · ngữ cảnh hội thoại · định nghĩa pháp lý gần nguyên văn.

---

## 2. HAI BUG NẶNG — root cause (5-step)

### 🔴 #8 — LLM lặp vô hạn (degeneration)
- **Chuỗi:** LLM lặp ← penalty=0 (`_07:19-20`) ← penalty **KHÔNG wire** tới litellm (GenerationParams chỉ có temp/top_p/max_tokens, `model_runtime.py:107-111`) ← **KHÔNG có detector lặp** (grep=0) → max_tokens=450 chỉ giới hạn 450 token rác, vẫn giao.
- **Bất biến:** không tầng nào bắt output hỏng. BP cơ bản (repetition_penalty, Holtzman 2019) thiếu hẳn.
- **Fix:** (a) XÂY degeneration detector deterministic trong guard_output; (b) wire penalty.

### 🔴 #13 — Bịa giá từ số rác "26"
- **Chuỗi:** LLM bịa "260.000" ← raw chunk chứa "26" không marker (marker chỉ ở stats-synthetic `query_graph.py:414`, không ở Path B) ← size-query rơi Path B (score 0.29) ← numeric gate=**observe** (`_14:354`) chỉ log.
- **Bất biến:** (a) gate observe không chặn, (b) Path B không xử lý số rác.
- **Fix:** (a) bật `numeric_fidelity_action=block` (FP đã đo 0/84); (b) route price-ask→Path A stats; (c) `name_by_shape=True`.

---

## 3. META-ROOT-CAUSE — 3 vòng lặp vận hành CHƯA ĐÓNG

| Vòng | Trạng thái | Bug gây ra |
|---|---|---|
| **measure → enforce** | guard đo xong (numeric FP 0/84) nhưng chưa promote observe→block | #13 |
| **taxonomy lỗi** | chống "sai sự thật" (số/brand) nhưng bỏ "văn bản hỏng" (degeneration) | #8 |
| **cấu hình chịu lỗi** | failover có cơ chế, chưa seed binding dự phòng | innocom→503 |

→ Cả 3 **"chưa hoàn tất chu trình"**, không phải "code sai".

---

## 4. AUDIT ON/OFF — lưới an toàn đang TẮT

**Nhóm SAFETY (chống-HALLU) hầu hết OBSERVE/OFF:**
`numeric_fidelity_action=observe` · `grounding_confirmed_action=observe` · brand_scope+claim_fidelity=observe+phrases rỗng · `empty_answer_guard=False` · **degeneration guard KHÔNG TỒN TẠI** · penalty=0 không wire.

**Nhóm RECALL (coverage) nhiều cái OFF:**
`parent_child=False` · `neighbor_expand=False` · `hyde=False` · `bm25_substring_fallback=False` · `multi_query[factoid]=False` · `stats_brand_aware=False` · `name_by_shape=False`.

**Nhóm RESILIENCE thực chất OFF:**
`failover=True` NHƯNG cần binding dự phòng (null mặc định) → bot mới 0 failover · empty-200 sync không phòng thủ.

**Nhóm COST (tốt):** `grade_use_batch=True` · circuit breaker/retry ON · speculative OFF.

⚠️ **Vì sao TẮT? Phần lớn CỐ Ý** (observe-first để đo FP trước khi enforce). Vấn đề = chưa đóng vòng (chưa promote). Chi tiết: `FLAG_ONOFF_AUDIT_20260713.md`.

---

## 5. BEST-PRACTICE vs SOTA

**Ragbot DẪN ĐẦU:** deterministic numeric-fidelity gate · multi-tenant RLS+4-key · observe-first measured rollout · full bộ retrieval (hybrid+rerank+MMR+Contextual Retrieval+late chunking) · RAGAS+prompt-cache · Port/DI.

**Ragbot TỤT:**
1. Degeneration handling THIẾU (BP cơ bản) → #8.
2. "Vòng measure→enforce chưa đóng" → #13.
3. Consume-side idempotency + callback dead-letter thiếu.
4. RAPTOR/self-consistency thiếu (cho high-stakes/pháp lý).
5. Failover chưa seed binding.

Chi tiết + suy luận: `BEST_PRACTICE_AUDIT_20260713.md`.

---

## 6. CƠ CHẾ CORE RAG — 3 "default ẩn"

1. **Hybrid mới là chunking-default THẬT** (L5 cross-check coerce khi conf<0.6), không phải recursive.
2. **Path B (raw chunk) là đường của câu khó** — stats Path A chỉ nhận câu khớp filter; câu NULL-giá/size rơi Path B nơi số rác lọt (#13).
3. **CRAG = "lenient + 1 retry", KHÔNG corrective thật** — adequacy hiếm khi hard-fail (6 đường ép True), không web-search/KB-ngoài → coverage miss không tự sửa.

**Bẫy bảo trì:** package `crag_grader/*` = code CHẾT (đăng ký DI, không ai gọi) · 2 cách derive tool_name · 2 content_hash. Chi tiết: `CORE_RAG_MECHANICS_20260713.md`.

---

## 7. FIX ROADMAP (theo giá trị × độ khó, đã có bằng chứng)

| Ưu tiên | Việc | Loại | Chặn bug | Đo |
|---|---|---|---|---|
| **1** | XÂY degeneration detector (guard_output) + wire penalty | Code mới | #8 | red-test + 60Q |
| **2** | Promote `numeric_fidelity_action=block` (FP 0/84) | Bật flag | #13 | 60Q FP |
| **3** | `name_by_shape=True` per-bot | Bật flag | typing entity | 60Q |
| **4** | Consume-side ON CONFLICT + callback dead-letter | Code | dup + mất câu | test |
| **5** | Seed `record_fallback_model_id` per binding | Cấu hình | innocom 503 | probe |
| **6** | Bật recall (parent_child/neighbor_expand) theo intent | Bật flag | #20, spa listing | 60Q A/B |
| **7** | Corrective retrieval thật cho listing/comparison | Code (lớn) | coverage | 60Q |
| **8** | Xoá code chết crag_grader + hợp nhất tool_name/hash | Refactor (T3) | maintenance | test |

**Nguyên tắc bất di:** mỗi lần bật/sửa **1 cái** → đo lại 60Q (HALLU + coverage + FP) → **không gộp**. Guard TẮT là cố ý observe-first; bật khi đã đo, **không bật mù**.

---

## 8. SẴN SÀNG DÙNG THẬT? (theo loại bot)

| Loại bot | Được chưa? | Lý do |
|---|---|---|
| Hỏi-đáp đơn giản (câu tên trực tiếp) | ✅ | Path A/retrieval L3; HALLU=0 |
| Bán lẻ / bảng giá | 🟡 có điều kiện | numeric gate chặn bịa (nếu bật block); listing/so-sánh còn thiếu |
| Pháp lý / tuân thủ | 🔴 chưa | #8 lặp + ngưỡng/trích dẫn chưa re-verify; sai 1 số pháp lý = nặng |
| Tải cao / đồng thời | 🔴 chưa | innocom cụt/503, chưa failover |

**Điều kiện tối thiểu cho MỌI bot:** (1) fix #8 degeneration, (2) bật numeric block, (3) seed fallback binding, (4) re-verify bộ QA hội thoại đúng loại bot.

---

## 9. ĐÃ SHIP phiên này (verified)
- Reliability: cap innocom 16→6 → **latency −35%/−42% (đo)**, probe tool, migrations applied.
- Fix committed: `/chat/stream` 500, ING-04, ING-01, ADR-0008 sparse-drop, RBAC seed, chặn tiêm lệnh VN.
- Uncommitted (verified-safe): B5 gather cache, B2 bỏ dead-write, T1-4 docstring, T3-7 genericize innocom.

---

## 10. LIÊN KẾT 6 REPORT CHI TIẾT
| Report | Nội dung |
|---|---|
| `CORE_RAG_MECHANICS_20260713.md` | Cơ chế query/ingest/chunking/CRAG + suy luận |
| `FLAG_ONOFF_AUDIT_20260713.md` | Mọi công tắc ON/OFF theo luồng + rủi ro |
| `BEST_PRACTICE_AUDIT_20260713.md` | Đối chiếu SOTA + Phần II suy luận sâu |
| `CODE_DEEPDIVE_REVIEW_20260711.md` | Review 5 flow + SOLID + deep-dig corrections |
| `PERF_AUDIT_20260711.md` | Hotspot performance Tier A/B/C |
| `PERF_LATENCY_INNOCOM_CONTROL_20260711.md` | Độ trễ + xử lý lỗi innocom |
| `RELIABILITY_FIX_20260710.md` | Cap 16→6 + clean re-verify |

---

## 11. TÓM TẮT 1 CÂU

> **Ragbot là hệ RAG expert-grade với kiến trúc + anti-HALLU vượt SOTA phổ thông, nhưng lỗi vận hành vì triết lý fail-open chưa có điểm dừng cứng: lưới an toàn deterministic đã-đo-an-toàn vẫn kẹt observe, degeneration handling thiếu hẳn, CRAG không corrective thật, và failover chưa cấu hình. Gap là "làm rồi chưa đóng vòng", không phải "chưa làm" — khung không cần đập, cần kỷ luật vận hành + đặt lại điểm dừng cứng đúng tầng.**
