# Kiểm chứng bản critique của reviewer — evidence-only re-check — 2026-07-13

> Reviewer viết 1 bản phản biện report load-test + audit. Em verify TỪNG claim
> đối chiếu **code thật + log thật** (`reports/loadtest_innocom_20260713/server_log_window.jsonl`,
> 1727 dòng). Phương pháp: 10 agent read-only (mỗi cái 1 nhóm claim) + 3 agent phản biện
> cố BÁC BỎ 3 claim quan trọng nhất. Mọi verdict kèm evidence verbatim (rule#0).
>
> **Line-number của reviewer là GẦN ĐÚNG — nhiều cái sai** (vd `retry_policy.py` sai path,
> `document_stats.py` nhãn dòng bị hoán). Em neo lại từng claim vào vị trí code THẬT.

---

## 0. CHỐT 1 DÒNG

**Reviewer ĐÚNG ~90% ở phần cơ chế** (root-cause latency, understand double-call, fail-open, cap/retry/failover). **Nhưng nói QUÁ ở ~7 chỗ magnitude** và bỏ sót ~4 nuance quan trọng cho việc *ưu tiên fix*. Và có 1 chỗ reviewer **KHEN QUÁ** (RLS) mà thực tế deploy này đang **TẮT**. Report cũ của em (`RESULTS.md`) **sai 1 chỗ nặng** (dòng 41) — reviewer chỉ đúng.

---

## 1. BẢNG CHẤM ĐIỂM TỪNG CLAIM

| # | Claim của reviewer | Verdict | Bằng chứng thật (file:line / log) |
|---|---|---|---|
| A | understand double-call: `UnderstandOutput` cấm field `query`, model trả `{"query":...}` → fail lượt 1 → gọi lại | ✅ **ĐÚNG** | `llm_schemas.py:74` `model_config=_STRICT_JSON_SCHEMA_CONFIG` (=`ConfigDict(extra="forbid")` l.19). Fields chỉ có `condensed_query`/`intent`/`confidence`. Log: **56/62** repair_retry là `UnderstandOutput`, **53** lỗi `query extra_forbidden` (input_value = câu hỏi gốc verbatim) |
| B | latency KHÔNG do độ dài sinh; giảm max_tokens vô ích | ✅ **ĐÚNG (mạnh hơn reviewer nói)** | **57/57** generate breach SLA 8s (`_15:131`). Pearson r(token,thời-gian)=**−0.174** (âm nhẹ). 4 token→75s; 248 token→33s; nhanh nhất 10.2s@89token |
| C | transport_error 6.7% = client bỏ ở 180s trên request CÒN SỐNG, không phải chết | ✅ **ĐÚNG** | `0b372e10` status **200 @201.1s**; `9c5670ff` status **200 @193.2s**. Probe timeout=180 (`reliability_probe.py:69`) → bucket transport_error |
| D1 | cap innocom = 6 | ✅ **ĐÚNG** (nuance: DB-tuned, không phải constant) | `router:731` lấy `cfg.provider.max_concurrent` từ DB `ai_providers`=6; **constant code=16** (`_10_rbac.py:52`). BG lane tách riêng |
| D2 | retry 3 lần | ✅ **ĐÚNG** (+ litellm tự retry thêm) | `DEFAULT_RETRY_MAX_ATTEMPTS=3` (`_04:180`). NHƯNG log có **244** dòng `Retrying request` = litellm retry NỘI BỘ chồng lên → tổng wire-attempt > 3 |
| D3 | timeout per-call = 90s (constant là 30s) | ✅ **ĐÚNG cả hai** | Áp dụng: `router:712` `cfg.provider.timeout_ms/1000`; DB innocom=90000; constant default=30000 (`_10:65`) |
| D4 | 500 CÓ retry → worst-case ~210–270s | ⚠️ **ĐÚNG-MỘT-PHẦN** | 500 retryable (`router:139`). NHƯNG **270s chỉ là trần lý thuyết — KHÔNG xảy ra**: 0 TimeoutError trong cả run; 14 fail đều InternalServerError trả về ~8–71s. *(call đơn ~87–91s CÓ thật ở trace 228s)* |
| D5 | failover cần `record_fallback_model_id` (mặc định None) → bot mới không failover | ✅ **ĐÚNG** | `_failover_eligible` `router:643-657` = False nếu `fallback_model_row_id is None` |
| D6 | CB fail_max=5, cooldown 30s, chỉ mở khi 5 fail LIÊN TIẾP, success reset → gần như không mở | ✅ **ĐÚNG** | `_08:16-17` fail_max=5/cooldown=30. Log: **0** sự kiện breaker-open (91.7% success xen kẽ reset) |
| D7 | penalty KHÔNG wire tới litellm | ✅ **ĐÚNG** (thực tế còn tệ hơn) | `GenerationParams` (`model_runtime.py:36-40`) chỉ có temp/top_p/max_tokens; router chỉ forward **temperature + max_tokens** — **top_p cũng KHÔNG tới wire** |
| E1 | pipeline_timeout_s=30 không enforce | ⚠️ **ĐÚNG-MỘT-PHẦN** | Đúng trên path load-test (test_chat HTTP): parse ở `_pipeline_config.py:835` nhưng không wrap `graph.ainvoke`. **NHƯNG worker path CÓ enforce** (`chat_worker/pipeline.py` wait_for) |
| E2 | LLMError→503, else→500 | ✅ **ĐÚNG** | `chat_routes.py:510` LLMError→503; `:525` Exception→500 (+ ExternalServiceError→503, GuardrailBlocked→refuse mềm) |
| F1 | ~6 đường ép retrieval_adequate=True | ✅ **ĐÚNG (reviewer đếm THIẾU)** | Thực tế **8 đường** trong `grade.py`, không phải 6 — reviewer sót 2, trong đó **grade-timeout fallback** là đường CHI PHỐI run thật |
| F2 | CRAG chỉ lenient+rewrite→re-query cùng store, max_grade_retries=1 | ✅ **ĐÚNG** | `DEFAULT_CRAG_MAX_GRADE_RETRIES=1` (`_10:125`); không có web/KB ngoài |
| F3 | structured repair cap=1 (cite :706) | ✅ **ĐÚNG (line CHÍNH XÁC)** | `structured_output_helper.py:706` `cap=DEFAULT_STRUCTURED_OUTPUT_REPAIR_RETRIES` (=1) |
| F4 | `crag_grader/*` là dead code | ✅ **ĐÚNG** | `bootstrap.py:435` build factory; **0** call-site trên live path |
| G1 | grounding wired mà timeout → None → fail-open (dù default fail_closed) | ✅ **ĐÚNG** | `local_guardrail.py:565-572` & `602-609` timeout→`(0,0)`→None→pass. fail_closed chỉ áp cho case grounder CHƯA wire |
| G2 | 28 timeout + 51 degraded grounding | ⚠️ **ĐÚNG-MỘT-PHẦN** | Số đúng nhưng **KHÔNG cộng dồn** (timeout ⊂ degraded). Đúng: **51 câu (85%)** grounding trả None; 28 trong đó là timeout. Đọc 28+51=79 = double-count |
| G3 | numeric/grounding/brand/claim=observe; empty_guard off; citation off | ✅ **ĐÚNG cả 6** | `_14`: numeric 354, grounding_confirmed 327, brand 363, claim 388 = observe; empty_answer_guard=False; citation_marker_required=False (inline) |
| G4 | "FP 0/84" chỉ là comment, không có test | ✅ **ĐÚNG** | 2 comment (`guard_output.py:184`, `_14:351`); `grep 0/84 tests/` = **0** |
| G5 | KHÔNG có degeneration detector | ✅ **ĐÚNG** | grep `repetition\|degenerat\|ngram\|loop-detect` trong guard/generate = **0** |
| G6 | innocom flaky ↔ grounding fail-open tương quan dương → #13 không bắt được dưới tải | ✅ **ĐÚNG (cơ chế)** | Nhất quán G1-G2. Caveat: "bug#13" nằm trong report, KHÔNG trong log — không verify được 1 lần bịa cụ thể đã xảy ra trong run này |
| H1/H3 | 2 path: stats-synthetic có marker / raw chunk KHÔNG marker → shell entity lọt số bịa | ✅ **ĐÚNG (cơ chế code)** | Path A marker `query_graph.py:415/422`; Path B raw `c.text` no marker (`retrieve.py:1079-1091`). *Nhưng run này 40/40 stats_route đều linked_chunks=1 → chưa quan sát firing* |
| H2 | 4 đường silent-drop trong document_stats.py | ✅ **ĐÚNG (nhãn dòng bị hoán)** | 4 cơ chế đều live nhưng reviewer gán sai số dòng (hoán col_N ↔ name-lead ↔ prose) |
| I | `extract_all_codes` định nghĩa + test nhưng 0 caller production | ✅ **ĐÚNG** | `query_range_parser.py:509` định nghĩa; grep caller = chỉ test, **0** production |
| J1 | RLS 3-lớp chắc (điểm mạnh) | ⚠️ **KIẾN TRÚC ĐÚNG nhưng deploy này TẮT** | 21 policy/20 bảng + role `ragbot_app` NOBYPASSRLS + `SET LOCAL` (`session.py:145`) — CÓ THẬT. NHƯNG runtime connect bằng **superuser `postgres`**, `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, không có `DATABASE_URL_APP` → **RLS KHÔNG enforcing trong deploy này** |
| J2 | 71/175 config key chưa seed; gate advisory | ⚠️ **71 ĐÚNG, mẫu số SAI** | 71 unseeded chính xác (`test_config_completeness_baseline.py:37` `_BASELINE_MAX=71`); mẫu số thật = **172** (không phải 175); gate advisory đúng (grep `.github`=0) |

**Tổng: 19 ✅ ĐÚNG · 6 ⚠️ ĐÚNG-MỘT-PHẦN · 0 ❌ SAI.** Không claim nào của reviewer bị bác bỏ hoàn toàn.

---

## 2. CHỖ REVIEWER NÓI QUÁ / NÓI THIẾU (giá trị của lần re-check này)

1. **D4 — "worst-case 210–270s"**: chỉ là trần lý thuyết. Run thật: **0 TimeoutError**, 14 fail đều là InternalServerError proxy trả về trong ~8–71s. Đòn "cắt retry 500" vẫn đáng làm nhưng lý do là **giữ slot lâu**, KHÔNG phải "bò tới 270s".
2. **G2 — 28+51 KHÔNG cộng dồn**: đúng là **51 câu (85%)** grounding fail-open, không phải 79. Vẫn đủ mạnh cho luận điểm.
3. **F1 — reviewer đếm THIẾU**: có **8** đường ép adequate, không phải 6. Đường **grade-timeout fallback** (reviewer bỏ sót) mới là đường chi phối run tải.
4. **D2 — retry amplification lớn hơn**: ngoài 3 lần của mình, **litellm tự retry** (244 dòng `Retrying request`) → tổng round-trip/câu cao hơn con số reviewer.
5. **D7 — còn tệ hơn**: không chỉ penalty, **top_p cũng không tới wire** (chỉ temperature + max_tokens).
6. **D1/D3 — "6" và "90s" là giá trị DB-tuned, KHÔNG phải constant code** (constant = 16 và 30s). → sửa = đổi row DB `ai_providers`, KHÔNG cần deploy code.
7. **E1 — chỉ HTTP path thiếu enforce; WORKER path CÓ enforce** pipeline_timeout. Bug scope hẹp hơn reviewer nói (chỉ ảnh hưởng đường test-harness, không phải production async).
8. **H — cơ chế đúng nhưng KHÔNG firing trong run này** (40/40 stats_route linked_chunks=1). #13 là cơ chế thật, chưa quan sát trong log tải này.
9. **A6 — fix understand cần cẩn thận hơn**: phải dùng `AliasChoices("condensed_query","query")` + `populate_by_name`, **KHÔNG** rename thô, **KHÔNG** `extra="ignore"` mù (mất condense của model + vỡ OpenAI strict-json_schema).
10. **J1 — KHEN QUÁ**: RLS kiến trúc chuẩn nhưng **default-OFF** trong deploy này → không được coi là "cô lập đa tenant chắc" ở trạng thái hiện tại.
11. **J2 — mẫu số 175 sai** (thật 172).

---

## 3. CHỖ REPORT CŨ CỦA EM (RESULTS.md) SAI — reviewer đúng

- **`RESULTS.md:41`**: "`structured_output_repair_retry` = 62 → innocom trả JSON hỏng" → **SAI**. Thật ra **56/62 (90%)** là **lỗi hợp đồng schema BÊN MÌNH** (`UnderstandOutput` cấm `query`). Đây là app-side deterministic, sửa free, không phụ thuộc innocom. → **quick-win #1 mà cả 2 report của em bỏ sót.**
- **`RESULTS.md:109`** ngụ ý đòn bẩy "giảm số call" — đúng hướng nhưng thiếu đòn bẩy LỚN nhất (understand double-call) và không nói "giảm max_tokens vô ích".

---

## 4. CHỐT HÀNH ĐỘNG (đã hiệu chỉnh theo bằng chứng — mỗi bước đo lại 60Q)

| Ưu tiên | Việc | Vì sao (bằng chứng) | Chi phí |
|---|---|---|---|
| **1** | Fix `UnderstandOutput` nhận `query` alias của `condensed_query` (`AliasChoices`+`populate_by_name`) | 56/62 repair_retry = lỗi này; ~1 call thừa/câu | Free, 1 file, low-risk |
| **2** | Cắt retry cho understand/rewrite/MQ/decompose khi 500 (fail nhanh sang degrade) | Call phụ giữ slot cap=6 lâu → HOL blocking | Low |
| **3** | Enforce pipeline_timeout end-to-end trên HTTP path (giống worker) | E1: HTTP path không có wall-clock kill | Low |
| **4** | Seed `record_fallback_model_id` per binding | D5: bot mới 0 failover → 500=503 | Config/DB |
| **5** | Xây degeneration detector DETERMINISTIC trong guard_output + wire penalty | G5+G1: grounding LLM fail-open dưới tải, penalty không wire | Mid (code mới) |
| **6** | Promote `numeric_fidelity_action=block` — NHƯNG đo FP thật trước | G4: "0/84" chỉ là comment, 0 test | Mid |
| **7** | Wire `extract_all_codes` → sửa comparison (QA#20) | I: 0 caller production | Low-mid |
| **8** | (nếu cần đa-tenant thật) bật DATABASE_URL_APP + gỡ superuser runtime | J1: RLS đang TẮT trong deploy này | Ops |

**Không đụng**: cap=6/timeout=90s là DB row (đổi khi cần, không phải bug); crag_grader dead code (gỡ sau, không gấp).

---

## 5. Phương pháp (rule#0)

- 10 agent read-only, mỗi cái 1 bucket, verdict CONFIRMED/PARTIAL/REFUTED/UNVERIFIABLE + evidence verbatim.
- 3 agent phản biện A/B/D (cố bác bỏ) — **đã xong, KHÔNG lật được verdict nào** (13/13 agent, 0 lỗi).
  - **Tự đính chính 1 điểm (rule#0)**: note ban đầu bảo repair-retry understand "mostly wasted / không recover" là **SAI**. Agent phản biện A5 chứng minh **51/56 trace (91%) RECOVER** sau lần gọi 2 (ra được đáp án). → 56 call thừa KHÔNG phí về mặt đúng/sai; lợi ích của fix understand-alias là **giảm 1 round-trip latency/cost mỗi câu**, KHÔNG phải "cứu câu hỏng". Vẫn đáng fix (đòn bẩy latency), nhưng framing chính xác lại.
- Nguồn: code branch `fix-260623-ingest-expert` + `reports/loadtest_innocom_20260713/server_log_window.jsonl`.
- Cấm suy diễn: chỗ nào không verify được từ code/log (vd "#13 có firing trong run này không") → gắn nhãn UNVERIFIABLE, không lấp.
