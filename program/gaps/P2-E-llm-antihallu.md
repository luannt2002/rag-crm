# P2-E — LLM OPS & ANTI-HALLU AUDITOR (Phase 2 gap report)

> READ-ONLY src/alembic/tests. STANCE = EVOLVE (giữ khung, nối dây, recalibrate).
> CHARTER ĐÚNG = HALLU_FABRICATE=0 sacred · no app-inject · no app-override.
> Mọi claim = `file:line` / commit / link. Input: `program/context/P1-E*.md` + `P1-SYNTHESIS.md §4 Q18-20`.

---

## 1. LABELED TABLE — LLM / guardrail components

| # | Component | Label | Evidence (file:line / commit) | Verdict 1 dòng |
|---|---|---|---|---|
| 1 | **Sacred no-inject** (sysprompt verbatim) | ✅ | `query_graph.py:6258-6292`; **9 locking tests** `tests/unit/test_generate_no_app_injection.py` (`test_system_prompt_passes_through_verbatim`, `test_no_application_keywords_anywhere`, `test_messages_first_role_is_system_with_exact_bot_prompt`, …9 total) | Bot owner sysprompt = system message verbatim; context+question ở USER turn XML-wrap. ĐÃ CHUẨN. |
| 2 | **Sacred no-override** (always-on path) | ✅ | `query_graph.py:6723-6728` (boundary comment cites MINDSET #2); grounding `severity="warn"/action="hitl"` `local_guardrail.py:524-527`; chỉ `severity=="block"` raise `query_graph.py:914` | Grounding/numeric = observability only, KHÔNG substitute answer. ĐÃ CHUẨN. |
| 3 | **math_lockdown override removed** | ✅ | override xóa ở `6e9041d`; còn lại chỉ `extract_numeric_claims` để DECIDE skip-cache (`query_graph.py:7361-7364`) — KHÔNG sửa answer | Vết tích tên module, nhưng đường override đã chết. ĐÃ CHUẨN. |
| 4 | **temp-0 generation** | ✅ | `query_graph.py:1272-1273` force `DEFAULT_GENERATION_TEMPERATURE=0.0` (`_10_rbac.py:190`) | Answer node luôn 0.0. ĐÃ CHUẨN. |
| 5 | **temp-0 deterministic transforms (via `_invoke`)** | ✅ | `query_graph.py:1274-1277` force `DEFAULT_DETERMINISTIC_TEMPERATURE=0.0` cho `DEFAULT_DETERMINISTIC_LLM_PURPOSES` (`_10_rbac.py:200-206`); fix `c6c6df4` | rewrite/condense/understand/intent/grade/reflect đi qua `_invoke` → forced 0.0. ĐÃ CHUẨN. |
| 6 | **Loop-bound counters** | ✅ | CRAG cap `query_graph.py:5209-5217` (`DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS=8` `_10_rbac.py:182`); hard backstop `8028-8031`; reflect cap `7232-7233` (`DEFAULT_MAX_REFLECT_RETRIES=1` `_15_*.py:120`) | 2 counter persist-in-state gate edges → no infinite loop. ĐÃ CHUẨN. |
| 7 | **Haiku scope separation** | ✅ | answer=gpt-4.1-mini; haiku CHỈ partial-task: slot `_20_*.py:63`, narrate `_20_*.py:55`, MQ `_11_*.py:143`; memory `feedback_haiku_partial_only` | 2 governance scope khác nhau, không vi phạm. ĐÃ CHUẨN. |
| 8 | **Grounding ≤5-sentence cap** | 🐛 | `local_guardrail.py:413` `max_sentences=5`; `:445` `sentences=sentences[:max_sentences]` | Câu 6+ KHÔNG bao giờ vào judge → tail-claim unverified trên answer multi-fact dài. §2. |
| 9 | **temp-0 coverage gap (direct `llm.complete`)** | 🐛 | `multi_query` `2797/4152`, `grounding` `1041/6834`, `decompose` `7858` gọi `llm.complete(cfg,…)` KHÔNG truyền `temperature` → `complete_runtime:562-563` dùng `cfg.params.temperature` = `binding.temperature` (`model_resolver.py:882`) | Trong `DEFAULT_DETERMINISTIC_LLM_PURPOSES` nhưng bypass override `1274`. Determinism phụ thuộc cột DB. §2. |
| 10 | **Grounding silent-degrade** | 🐛 (HALLU-adjacent) | timeout/error → `_run_structured_judge` return `(0,0)` (`local_guardrail.py:553-555`); `checked==0` → return `None` (`509-510`); except → `None` (`505-507`); `None` = treated grounded | Judge chết âm thầm = answer "pass" → degraded judge = silent HALLU net OFF. §2/§4-Q19. |
| 11 | **nano-as-grounding-judge** | 🕰 | alembic 0195 swap grounding gemma→gpt-4.1-nano (`20260609_0195*.py:37`); docstring `:23` "A/B-gated (load test must hold HALLU=0)" — REQUIREMENT not RUN | 2026 chuẩn = claim-level NLI/MiniCheck, không single-call sentence-cap LLM-judge. §3. |
| 12 | **CRAG grader** (DB-verify + LLM-judge) | 🕰/✅ | `cb9c3b1` "robust grading: DB-verify + LLM-judge (no string-match)"; grade node `5331/5341/5448`; model gpt-4.1-mini (0195) | Đúng hướng 2026 (corrective routing) — nhưng grader=mini single-judge, không claim-decompose. §3. |
| 13 | **self-RAG critique override** (opt-in) | ↔️ | `query_graph.py:6650-6717`; replace answer bằng `oos_answer_template` khi LLM tự `[Unsupported]` ratio ≥ threshold (`6708-6712`); default OFF `DEFAULT_SELF_RAG_ENABLED` | App-side substitution NHƯNG driven by model self-critique + bot template → sacred-borderline. Default OFF = an toàn. Giữ flag-OFF tới khi verifier có. |
| 14 | **doc-drift 04-D "math lockdown"** | ↔️ | `docs/master/04-D-pipeline-orchestration.md:114` GENERATE diagram còn "math lockdown" vs code override removed `6e9041d` | Confirm P2-A từ góc anti-hallu: doc trỏ override đã chết. §3 cuối. |

**Đếm:** ✅ = 7 · 🐛 = 3 (#8,#9,#10) · 🕰 = 2 (#11,#12) · ↔️ = 2 (#13,#14). (#12 lai ✅).

---

## 2. 🐛 — HOLES với repro sketch (KHÔNG commit, chỉ mô tả)

### 🐛-1 · Grounding ≤5-sentence → tail-claim unchecked (Q18 = CÓ THẬT)

**Hole:** `local_guardrail.py:445` `sentences = sentences[:max_sentences]` cắt cứng 5 câu đầu. `max_sentences=5` là **function-default hardcode** (`:413`), KHÔNG config-driven. Câu 6+ KHÔNG vào judge.

**Tại sao reachable (phản biện mitigant "answer luôn ngắn"):**
- `generate_max_tokens` mặc định nhỏ NHƯNG **aggregation/comparison/multi_hop** intent dùng schema `sub_answers` (`query_graph.py:544-550`, `_resolve_generate_schema:554`) = reasoning-first liệt kê TỪNG fact → answer dài, nhiều câu.
- alembic 0193 nới sysprompt cho-phép-grounded-compute → answer multi-fact giờ ĐƯỢC dài hơn (enumerate + compute).
- Grounding intents bao gồm đúng các intent multi-fact này (`DEFAULT_GROUNDING_INTENTS=factoid,comparison,aggregation,multi_hop` `_15_*.py:112-117`).

**Repro sketch (mô tả, không chạy):**
1. Chọn bot corpus có ≥7 fact rời (vd `dia-ly-vn`: diện tích + dân số + bờ biển + đỉnh núi + 3 vùng).
2. Hỏi 1 câu aggregation buộc liệt kê ≥7 câu: *"Liệt kê diện tích, dân số, chiều dài bờ biển, đỉnh cao nhất và diện tích 3 vùng kinh tế lớn của VN"* → intent=aggregation → `sub_answers` schema → answer 7-9 câu.
3. Cấy 1 fact SAI ở **câu 7** (vd diện tích vùng thứ 3 bịa) bằng cách dùng corpus thiếu chunk đó (retrieval miss) → LLM lấp.
4. **Kỳ vọng hole:** `llm_grounding_check` chỉ chấm câu 0-4; câu 7 bịa KHÔNG vào `sentence_list` (`:462-464`) → `unsupported` không đếm câu 7 → `ratio` không vượt threshold → KHÔNG warn. Tail-claim hallucination đi qua đài quan sát.
5. **Assert:** `grounding_check_result` log (`:513`) có `checked ≤ 5` dù answer 9 câu → bằng chứng tail bỏ sót.

**Lưu ý sacred:** đây là **observability hole**, KHÔNG phải override gap — grounding chỉ warn nên dù bắt được câu 7 cũng không block. Tác hại thật = **dashboard/HITL mù tail-claim**, không phải user thấy số sai-vì-app-sửa. Nhưng vì charter ĐÚNG = HALLU=0, một quan-trắc mù 50% answer dài là rủi ro phải đóng.

### 🐛-2 · temp-0 coverage gap — 3 direct call bypass override (Q gap #1 confirm)

**Hole:** `c6c6df4` chỉ vá đường `_invoke_llm_node:1274`. 3 purpose gọi `llm.complete(cfg,…)` TRỰC TIẾP, KHÔNG truyền `temperature=`:
- `multi_query` `query_graph.py:2797`, `4152`
- `grounding` `query_graph.py:1041`, `6834`
- `decompose` `query_graph.py:7858`

`complete(cfg,msgs)` → `complete_runtime` (`dynamic_litellm_router.py:864`) → `temperature = temperature if temperature is not None else cfg.params.temperature` (`:562-563`). `cfg.params.temperature = float(binding.temperature)` (`model_resolver.py:882-884`), fallback `model.default_temperature`.

**Immutable cause:** 3 purpose này NẰM TRONG `DEFAULT_DETERMINISTIC_LLM_PURPOSES` (`_10_rbac.py:201-205`) — ý-đồ là 0.0 — nhưng cơ chế ép 0.0 sống ở node-helper, không ở router. Nếu cột `bot_model_bindings.temperature` của các binding này = NULL/0 thì OK; nếu = `llm_default_temperature` seed **0.3** (`init_system_config.py:29`) thì chạy **non-deterministic**, đúng triệu chứng `c6c6df4` định fix (spa Q7 refuse↔answer flip do sub-query đổi).

**Repro/đo (mô tả):** `psql -c "SELECT purpose, temperature FROM bot_model_bindings WHERE purpose IN ('multi_query','grounding','decompose');"` — nếu bất kỳ row > 0 → fix `c6c6df4` KHÔNG phủ → flip vẫn còn. (CHƯA chạy được — DB cần password; đây là GIẢ THUYẾT có-điều-kiện, không phải SỰ THẬT.)

**Expert fix (đúng tầng, KHÔNG commit):** đẩy override vào `complete_runtime` — nếu `purpose in DEFAULT_DETERMINISTIC_LLM_PURPOSES and temperature is None: temperature=0.0`. 1 chỗ, bao mọi callsite (cả direct lẫn `_invoke`), Open-Closed, hết drift node-vs-router. Đây là tầng đúng (router là single choke-point), khớp expert-solution mandate.

### 🐛-3 · Grounding silent-degrade = silent HALLU-net-OFF (Q19 root)

`local_guardrail.py:553-555` timeout → `(0,0)`; `:505-507` Exception → `None`; `:509-510` `checked==0` → `None`. `None` ở `guard_output` = "không có hit" = answer pass. Judge timeout/lỗi/empty = **âm thầm coi answer là grounded**.

Đây vừa là **graceful-degrade đúng pattern** (transport error không giết app chính — CLAUDE.md claude-mem) VỪA là rủi ro: với nano-judge mới (0195) chưa A/B, nếu nano hay timeout/trả rỗng trên answer dài thì grounding-net im lặng OFF mà KHÔNG ai biết (chỉ `_logger.warning` `:506/554`). Cần **counter "grounding_degraded_total"** để phân biệt "judge PASS" vs "judge chết". Hiện chỉ có `grounding_fail_total` (`:523`) đếm fail, KHÔNG đếm degrade — degrade vô hình trên metric.

---

## 3. 🕰 — "chuẩn 2026 là gì" + nguồn (≤3 web search)

### 🕰-A · Grounding judge: sentence-cap LLM-judge vs claim-level NLI/entailment

**Hiện tại Ragbot:** 1 LLM-call, gửi ≤5 câu nguyên-văn + context, hỏi SUPPORTED/NOT_SUPPORTED mỗi câu (`local_guardrail.py:466-485`). Đây là **sentence-level single-judge**, KHÔNG decompose claim, KHÔNG NLI model.

**Chuẩn 2026:** pipeline 2 bước — (1) **claim decomposition**: LLM tách answer thành atomic claims; (2) **per-claim NLI entailment** check từng claim với context, score = #entailed/#claims. RAGAS faithfulness, FACTSCORE đi hướng này. SummaC (sentence-NLI) đã bị thay bằng **claim-level** vì 1 câu có thể chứa nhiều claim (câu đúng-một-nửa lọt sentence-judge). Fine-tuned **MiniCheck-7B** thắng GPT-4-judge ở claim-wise classification và rẻ/nhanh hơn; **DeBERTa-MNLI** NLI nhẹ rẻ hơn LLM-judge.

**Khoảng cách cho Ragbot:** (i) sentence-cap bỏ tail (🐛-1) — claim-decompose phủ toàn answer không cần cap; (ii) "1 câu = 1 verdict" bỏ sót sub-claim trong câu phức (số đúng + tên dịch vụ sai cùng 1 câu → judge dễ gọi SUPPORTED); (iii) nano-judge zero-shot kém ổn định hơn NLI-model chuyên dụng → false-PASS. **Evolve (không rewrite):** judge đã đi qua Port (`structured_judge_fn`/`llm_complete_fn` `:415-417`) → có thể SWAP sang MiniCheck/DeBERTa adapter qua ADR mà KHÔNG đụng orchestrator — đúng tinh thần "thay engine qua Port" của charter.

Nguồn: [123ofAI NLI faithfulness](https://123ofai.com/qnalab/system-design/blocks/faithfulness) · [Benchmarking LLM Faithfulness in RAG (arXiv 2505.04847)](https://arxiv.org/html/2505.04847v2) · [RAGAS Faithfulness docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) · [FutureAGI NLI-evaluation 2026](https://futureagi.com/glossary/nli-evaluation/)

### 🕰-B · nano-as-judge reliability + CRAG grader vs 2026

**nano judge:** gpt-4.1-nano cho SUPPORTED/NOT_SUPPORTED là task phân-loại nhỏ — nano "đủ" cho factoid đơn nhưng **chuẩn 2026 ưu tiên fine-tuned faithfulness model** (MiniCheck) hơn frontier-mini zero-shot vì reliability + cost. LLM-as-judge 2026 vẫn dùng rộng nhưng khuyến nghị **dedicated NLI** cho entailment thay vì general LLM. Rủi ro nano: false-PASS trên answer dài/đa-claim = silent HALLU (warn-only nên không có safety-net đỡ).

**CRAG grader:** Ragbot `cb9c3b1` DB-verify + LLM-judge = đúng hướng **corrective RAG 2026** (dynamic routing retrieval→grade→rewrite_retry thay vì static). 2026 thêm: multi-hop retrieval, query reformulation, self-correction loops (Ragbot có reflect + rewrite_retry). Khoảng cách: grader=single mini-judge, chưa claim-decompose; có thể nâng grader confidence-aware (CRAG gốc: correct/incorrect/ambiguous 3-mức) thay vì binary adequate.

Nguồn: [LLM-as-a-Judge 2026 Guide (Label Your Data)](https://labelyourdata.com/articles/llm-as-a-judge) · [Snowflake LLM-judge RAG-triad](https://www.snowflake.com/en/engineering-blog/benchmarking-LLM-as-a-judge-RAG-triad-metrics/) · [CRAG Medium](https://dkaarthick.medium.com/corrective-rag-crag-taking-retrieval-augmented-generation-to-the-next-level-191349990356) · [RAG state-of-the-art 2026](https://github.com/dpsdagain/RAG/blob/main/rag_state_of_the_art_2026.md)

### ↔️-doc-drift 04-D "math lockdown" (confirm từ góc anti-hallu)

`docs/master/04-D-pipeline-orchestration.md:114` GENERATE diagram: `LLM call → citation parse → math lockdown`. Code: math_lockdown OVERRIDE đã xóa `6e9041d` ("remove dead math_lockdown config/constants — override already gone"); module còn sống CHỈ làm `extract_numeric_claims` cho skip-cache decision (`query_graph.py:7361-7364`), KHÔNG sửa answer. **Doc trỏ một override không-còn-tồn-tại** → đọc-giả tưởng app vẫn regex-replace số = hiểu sai sacred posture. P2-A đã thấy; xác nhận từ anti-hallu: 04-D last-updated 2026-05-12 predates `6e9041d` + 0193 allow-grounded-compute. Sửa diagram: bỏ "math lockdown", đặt "grounding warn-only (observe)".

---

## 4. ANSWERS Q18-Q20

### Q18 — Grounding ≤5 câu để tail-claim unchecked? → **CÓ, là HALLU-observability hole thật**

CÓ. `local_guardrail.py:445` cắt cứng 5 câu; answer aggregation/comparison/multi_hop dùng `sub_answers` schema (`query_graph.py:544-550`) có thể >5 câu; 0193 nới allow-compute làm answer multi-fact dài thêm. Câu 6+ KHÔNG vào `sentence_list` → unverified. **Nhưng caveat sacred:** grounding warn-only (`:524-527`) — nó KHÔNG block, nên hole = **đài quan-trắc mù tail**, không phải bot-trả-sai-vì-app. Tác động: HITL/dashboard không thấy tail-hallucination trên answer dài. Fix đúng tầng: claim-level judge (bỏ cap) > nâng `max_sentences` thành config scale theo độ-dài (band-aid). Repro = §2 🐛-1.

### Q19 — nano judge đủ mạnh? 0195 có chạy A/B HALLU=0 thật? → **A/B CHƯA RUN (requirement, không evidence)**

- **0195 A/B:** docstring `20260609_0195*.py:23` viết *"A/B-gated (load test must hold HALLU=0)"* — đây là **yêu cầu/expected**, KHÔNG phải kết quả đã chạy. `reports/FLOW_ANALYSIS_20260609.md:79` ghi explicit *"nano grounding **cần verify** giữ HALLU=0"* = mục mở chưa làm. Các report HALLU=0 (`FIX_VERIFY_REPORT_20260609.md:42` "HALLU 1-2→0"; `LOADTEST_REPORT_20260609e_FULL.md:1699`) chứng minh **answer** không bịa (root cause = cliff-filter cắt chunk + sysprompt, KHÔNG phải grounding judge). **KHÔNG report nào phân lập "grounding nano bắt được NOT_SUPPORTED đúng không"** = grounding chưa được A/B độc lập. → **SỰ THẬT: HALLU=0 ở answer-level verified; GIẢ THUYẾT chưa kiểm: nano-judge accuracy.**
- **nano đủ mạnh?** Vì grounding warn-only, một nano false-PASS KHÔNG tạo HALLU thấy-được trong load test (answer đã đúng từ retrieval+sysprompt). Nghĩa là **load test HALLU=0 KHÔNG chứng minh nano judge tốt** — nó chỉ chứng minh answer-path tốt. Judge chỉ quan-trắc. Để biết nano đủ mạnh phải A/B riêng: tiêm answer-có-claim-sai, đếm nano recall NOT_SUPPORTED. Chưa làm.
- **Silent-degrade (§2 🐛-3):** nano timeout/empty → `None` → "grounded" âm thầm. Không có `grounding_degraded_total` metric → không phân biệt "PASS thật" vs "judge chết". Rủi ro: net OFF vô hình.
- **Verdict:** nano-judge = acceptable cho observability nhẹ; KHÔNG đủ làm safety-net (mà nó cũng không được thiết kế làm net — chỉ warn). Muốn nâng grounding thành net thật → claim-NLI model + block-on-high-ratio (đổi posture, cần ADR, đụng sacred override → cân nhắc kỹ).

### Q20 — Numeric aggregation: ranh giới extract-then-compute vs sacred #5 (app-no-override)

**Trạng thái hiện tại (đúng sacred):** App KHÔNG cộng. Aggregation/comparison/multi_hop → `sub_answers` reasoning-first (`query_graph.py:544-550`) → **LLM tự liệt kê + tự tính**; `stats_index_repo` route (`1161`, `6fcf899`) phục vụ stats **pre-indexed** (số đã có sẵn trong corpus, không tính runtime). KHÔNG app-side sum, KHÔNG override khi LLM tính sai. alembic 0193 nới sysprompt cho-phép grounded-compute → đẩy việc tính cho LLM (verify: `FIX_VERIFY_REPORT:55` spa q07 "1.2M và 3M, chênh 1.8M — tính đúng"). **Sacred #5 GIỮ NGUYÊN.**

**Ranh giới (recommendation):**
- **2026 chuẩn:** "Đừng bắt LLM làm máy tính — bắt nó plan + gọi tool". LLM kém arithmetic (pattern-match, sai middle-digit). Offload sang deterministic calculator.
- **Nhưng đối với sacred #5:** câu hỏi là *"app đưa giá-trị-đã-tính cho LLM có phải inject/override không?"*. **PHÂN BIỆT 2 hướng:**
  - ❌ **Override (CẤM):** LLM trả answer → app regex bắt số → thay bằng số app tính → đẩy ra user. Đây là sacred #2/#5 vi phạm (math_lockdown cũ — đã xóa `6e9041d`).
  - ✅ **Tool-use TRƯỚC answer-path (ĐƯỢC):** app/LLM trích các addend GROUNDED từ chunk → calculator tool tính sum → **đưa sum như một retrieved-fact vào context** để LLM diễn đạt. Đây KHÔNG phải inject-platform-rule (đó là data, giống chunk), KHÔNG phải override-answer (chạy TRƯỚC khi LLM sinh answer, LLM vẫn tự do dùng/không dùng). Ranh giới sacred: **tool-output = data trong USER turn (như `<documents>`), KHÔNG phải instruction trong SYSTEM turn, KHÔNG sửa text answer sau-sinh.**
- **Khuyến nghị boundary cụ thể:** nếu Phase 4 muốn safety-net arithmetic → ship **calculator-tool node TRƯỚC generate**, output đặt vào context block (`query_graph.py:6287` USER turn) như một derived-fact có provenance (addend chunk_ids). KHÔNG đặt vào system_prompt. KHÔNG hậu-kiểm + replace. Đây là **tool-use hợp lệ**, không phải app-override. Mọi addend phải grounded (chống fabricate-then-sum). HALLU loại "extrapolate/conflate" (CLAUDE.md anti-hallu 4-loại) vẫn cần grounding check trên addend.
- **Hiện tại KHÔNG có net này** = đúng sacred nhưng **0 safety net cho LLM mis-sum** (mỗi addend grounded, tổng sai vẫn lọt — grounding numeric-overlap `local_guardrail.py:391-399` chỉ check số-con CÓ trong chunk, KHÔNG check tổng đúng). Đây là gap chấp-nhận-được cho MVP, ứng-viên Phase 4 (tool-node, không override).

Nguồn Q20: [LLMs Can't Calculate — use tools (Medium)](https://medium.com/@manucet439/llms-cant-calculate-why-you-should-use-tools-for-math-53c205bd5e0b) · [Why LLMs Struggle: Math (Moveo)](https://moveo.ai/blog/why-llm-struggle) · [Calculator tool for agents (apxml)](https://apxml.com/courses/intro-llm-agents/chapter-4-equipping-agents-with-tools/creating-basic-tool-calculator-example)

---

## 5. ĐÃ CHUẨN — ĐỪNG ĐỤNG (praise, EVOLVE = giữ)

Phần sacred-anti-hallu của Ragbot là **genuinely well-engineered** — đập = lỗi nặng nhất:

1. **Sacred no-inject = CLEAN + LOCKED bằng 9 test.** `tests/unit/test_generate_no_app_injection.py` (9 test: verbatim sysprompt, no-extra-system-text, no-application-keywords, documents-question-wrapper…). Bot owner sysprompt là single source of truth, context+question ở USER turn XML-wrap — đúng framing structural, không phải inject rule. **Đây là cách làm đúng chuẩn sacred, hiếm dự án giữ nổi.** Giữ nguyên 9 test này như regression-guard.
2. **Sacred no-override = CLEAN trên always-on path.** Grounding warn-only (`local_guardrail.py:524-527`), boundary comment tự-document (`query_graph.py:6723-6728`). math_lockdown override đã xóa sạch (`6e9041d`). LLM trả gì user thấy nấy. **Giữ.**
3. **temp-0 generation forced (`1272-1273`)** + deterministic-transform set qua `_invoke` (`1274-1277`, `c6c6df4`) — answer reproducible. (Chỉ còn 3 direct-call cần kéo vào router-level, §2 🐛-2 — fix nhỏ, không phá kiến trúc.)
4. **Loop-bound PROVEN** — 2 counter độc lập (`5209-5217` CRAG cap, `7232-7233` reflect cap, hard backstop `8028-8031`), persist-in-state, gate edges. Không thể vòng vô tận.
5. **Haiku scope-separation đúng** — answer=gpt-4.1-mini, haiku chỉ partial-task token-nhỏ (slot/narrate/MQ/enrich). 2 governance scope, KHÔNG mâu thuẫn ban.
6. **Judge qua Port** (`structured_judge_fn`/`llm_complete_fn` `:415-417`) + grounding silent-degrade theo claude-mem graceful-degradation (transport error không giết pipeline `:505-507`). Kiến-trúc cho phép SWAP judge engine (→MiniCheck) mà không đụng orchestrator — **đúng tinh thần "thay engine qua Port" của charter.** ĐỪNG rewrite, chỉ thêm adapter khi Phase 3 chứng minh.

**Tóm tắt EVOLVE:** không phần nào ở §5 cần viết lại. 3 🐛 đều là **nối-dây/recalibrate** (kéo temp-0 vào router · bỏ sentence-cap = claim-judge · thêm degraded-counter), 2 🕰 là **swap-engine-qua-Port khi có ADR**, 2 ↔️ là **sửa doc + giữ flag-OFF**. Sacred core đứng vững.
