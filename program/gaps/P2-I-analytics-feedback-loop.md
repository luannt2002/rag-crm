# P2-I — ANALYTICS & FEEDBACK-LOOP (D12 + D13) — Phase 2 gap report

> READ-ONLY src/alembic/tests. STANCE = **EVOLVE** (giữ khung, nối dây). No-premature-observability (V18): KHÔNG add Prometheus collector mới.
> Mọi claim = `file:line` / commit / grep. SỰ THẬT vs GIẢ THUYẾT gắn nhãn.
> Anchor `7dd1f84` · alembic head 0195 · branch `fix-260604-action-slotmachine-dead-key`. Re-verified 2026-06-10.
> Input: `program/00-charter.md` (trục ĐỦ Coverage≥0.95; trục KIỂM SOÁT) · `decisions/00 D12/D13` · `gaps/P2-E` (grounding observe-only, Coverage metric) · `gaps/P2-G` (33-step request_steps, cost read-query thiếu, eval 3-run flip).

---

## 1. LABELED COMPONENT TABLE (✅ / 🕰 / ↔️ / 🐛)

| # | Component | Label | Evidence (`file:line` / grep) | Verdict 1 dòng |
|---|---|---|---|---|
| 1 | **2 feedback write-paths coexist** | ✅ | chat.py `/feedback` → `request_logs` inline (`give_feedback.py:42-60` `attach_feedback_by_message`); `/feedback/thumbs` → `message_feedback` table (`feedback.py:131-138`, `router.py:53` wired) | Cả 2 path đều LIVE + wired. Docstring `feedback.py:1-11` "not yet wired" = **STALE** (router.py:53 đã include). |
| 2 | `message_feedback` table + RLS-scoped repo | ✅ | model `message_feedback_model.py:47-89` (4-key identity, alembic 0074); `record`/`aggregate_per_bot` qua `session_with_tenant` `message_feedback_repository.py:71,124` | Schema + write + RLS đúng. Insert LIVE từ route. |
| 3 | **`aggregate_per_bot` (thumbs read) — 0 consumer** | 🐛 | `message_feedback_repository.py:89` def; grep `aggregate_per_bot` toàn `src/` = **chỉ 1 hit (def)**, 0 caller route/service/worker | Thumbs verdict INSERT-rồi-nằm-chết. Không dashboard/eval/FAQ đọc. **D12 loop CỤT tại thumbs.** §2 🐛-1. |
| 4 | `FeedbackGiven` outbox event | 🐛 | `give_feedback.py:62-75` emit `FeedbackGiven`; grep consumer `FeedbackGiven` = chỉ def `chat_events.py` + emit-site, **0 handler subscribe** | "downstream analytics can consume" (`give_feedback.py:6-7 docstring`) = aspirational. Event đi vào outbox, 0 reader. §2 🐛-1. |
| 5 | **FAQ-from-refuse (closing-the-loop)** `FAQCandidateService` | 🐛 | `faq_candidate_service.py:111` mines `refusal_reason IS NOT NULL` (`_REFUSED_SQL:319-341`), embed+greedy-cluster; grep `FAQCandidateService\|find_candidates\|SqlRefusedQuestionRepo` ngoài file = **0 hit** (route/bootstrap/worker) | Service viết tốt, đúng app-mindset (operator-fill, no LLM-inject `:16-26`), NHƯNG **0 callsite** = built-not-wired. Loop refuse→FAQ KHÔNG chạy. §2 🐛-2. |
| 6 | Refuse-suggestion analytics route | 🐛 | `admin_refuse_suggestions.py:44` GET `/admin/bots/{bot_id}/refuse_suggestions` (SQL `answer_type IN no_context,blocked`); **KHÔNG import trong `router.py`** (grep `admin_refuse` trong router.py = 0) | Route tồn tại + RBAC fix (`:39`), nhưng **chưa include vào app** = unreachable. §2 🐛-2. |
| 7 | Tenant analytics: refuse-rate + cost + latency + drift | ✅ | `tenant_analytics_service.py:205-267` pass/refuse/hallu; `:272-313` cost; `:427-473` latency p50/95/99; `:639-716` drift 2-window; routes `admin_analytics.py:179-265` wired `router.py:71` | **REFUSE rate CÓ tính** (`refusal_reason IS NOT NULL` `:235`). Tenant-scoped, RLS-correct. ĐÃ CHUẨN — đừng đụng. |
| 8 | **Coverage metric (trục ĐỦ charter) — KHÔNG tính trong analytics** | 🐛 | `tenant_analytics_service.py:212-221` chỉ PASS/REFUSE/HALLU; **không có** "corpus-có-đáp-án-mà-bot-refuse" (silent-miss). HALLU = `is_correct IS FALSE` (`:237-239`) phụ thuộc cột hiếm khi set | Refuse-rate ≠ coverage. Bot refuse SAI (corpus có) đếm chung với refuse ĐÚNG (OOS). **Charter Coverage≥0.95 không observable từ analytics live.** §2 🐛-3. |
| 9 | `request_logs` "câu user hỏi mà corpus thiếu" | ↔️ | `admin_refuse_suggestions.py:78` + `faq_candidate_service.py:338` cùng đọc refuse rows — NHƯNG cả 2 unwired (#5,#6). Top-questions `admin_metrics.py:66` `only_failed` đọc được | Dữ liệu CÓ trong `request_logs` (refusal_reason, answer_type); **read-path để surface ra operator = orphan**. §5. |
| 10 | `metrics_top_questions only_failed` | ✅ | `admin_metrics.py:60-77` GET `/metrics/top-questions?only_failed=true`; wired `router.py:63` | Operator XEM ĐƯỢC câu fail. Đây là phần KIỂM SOÁT duy nhất live cho refuse-surfacing. Giữ. |
| 11 | **Eval ground-truth `must_contain` — ai gán nhãn?** | 🐛 (D13) | gold facts ở `scripts/qa_prod/<bot>.json` (`loadtest_graded.py:243`); key `must_contain`/`fact_id` (`qa_prod/tin-hoc-co-ban.json`); **DB-verify chống fact-test-sai** `_db_has:69`, label CORPUS-GAP nếu fact không có (`GRADING_SOP.md:21`) | Có chốt chặn fact-test-sai (DB ground-truth). NHƯNG **không có evidence ai-viết-gold + người-đó-không-biết-hệ-thống** (D13 §9.3). §2 🐛-4. |
| 12 | **LLM-judge độc lập với answer-model?** | 🐛 (D13) | judge `JUDGE="gpt-4.1-mini"` `loadtest_graded.py:36`; answer-model bot cũng **gpt-4.1-mini** (P2-E #12 grade node + 0195) | Judge = cùng model-family với answer-gen → **self-preference bias** (chuẩn 2026: KHÔNG dùng cùng model judge+gen). §2 🐛-4 + §3 🕰-C. |
| 13 | `RagasMetricAdapter` — đo gì thật? | 🐛 | `ragas_metric_adapter.py:58-88` trả `stub_score` CỐ ĐỊNH cho mọi metric (`:81 clamped`), chỉ empty-context→faithfulness=0; grep consumer = chỉ `scripts/eval_ragas_metrics.py:195` (offline script, default stub) | **Stub đo HẰNG SỐ, KHÔNG đo gì.** Port-shape đúng (swap real ragas qua Strategy `:39-55`) nhưng chưa có engine thật. §2 🐛-5. |
| 14 | `PersonaQualityGate` (sysprompt anti-pattern audit) | 🐛 | `persona_quality_gate.py:87` `audit_system_prompt` regex oversized/pollution/conflict (audit-only, no override `:2-6`); grep `persona_quality_gate\|audit_system_prompt` ngoài file = **0 hit** | Audit tool đúng mindset (warn-only, bot-owner đọc), NHƯNG **0 callsite** = không bot-owner nào thấy warning. Built-not-wired. §5. |
| 15 | 3-run flip / determinism trong eval | ✅ | `loadtest_graded.py:37` `RUNS=3`; `deterministic` flag `:182`; flip-detect `GRADING_SOP.md:50,61` | Đo non-determinism = ahead của RAGAS-only (P2-G #5 cũng praise). Giữ. |
| 16 | No-premature-observability (V18) compliance | ✅ | grep `prometheus_client\|Counter(\|Histogram(\|start_http_server` trên 7 file scope = **0 hit** | KHÔNG add collector mới. Tất cả aggregate qua SQL trên `request_logs`/`request_steps` + structlog. **Tuân V18.** |

**Đếm:** ✅ ×7 (#1,#2,#7,#10,#15,#16, + #13-Port-shape-borderline) → chốt **✅ ×6** · **🐛 ×8** (#3,#4,#5,#6,#8,#11,#12,#13,#14 → gộp wiring = chốt 8 distinct: thumbs-loop-cụt[#3+#4], FAQ-orphan[#5+#6], coverage-gap[#8], gold-authorship[#11], judge-self-bias[#12], ragas-stub[#13], persona-orphan[#14]) · 🕰 ×3 (§3) · ↔️ ×1 (#9).

---

## 2. 🐛 — HOLES với repro sketch (KHÔNG commit, chỉ mô tả)

### 🐛-1 · D12 feedback loop CỤT tại thumbs — INSERT rồi nằm chết (câu hỏi cốt lõi D12)

**Hole:** `/feedback/thumbs` ghi `message_feedback` row (`feedback.py:131-138`) + `/feedback` ghi `request_logs.feedback_score` (`give_feedback.py:42-51`) + emit `FeedbackGiven` outbox (`:62-75`). NHƯNG:
- `message_feedback_repository.aggregate_per_bot` (`:89`) = **0 caller** (grep toàn src). Không route, service, worker, eval nào đọc thumbs counts.
- `FeedbackGiven` event = **0 subscriber** (grep consumer = chỉ emit-site). Docstring "downstream analytics can consume" (`give_feedback.py:6-7`) chưa thành sự thật.
- `request_logs.feedback_score`/`is_correct` được ghi, nhưng `pass_rate_per_bot` HALLU dùng `is_correct IS FALSE` (`tenant_analytics_service.py:237`) — cột này chỉ set bởi feedback path hiếm khi gọi → HALLU-count gần như luôn 0 trên live traffic.

**Immutable cause:** không có **vòng học**: thumbs → (eval dataset / FAQ / sysprompt-suggest) chưa nối. Charter D12 yêu cầu "thumbs → vòng học"; hiện chỉ có **vế ghi**, thiếu **vế đọc-để-cải-thiện**.

**Repro sketch (mô tả):** POST 50 `/feedback/thumbs` thumbs_down cho bot X → query `SELECT count(*) FROM message_feedback WHERE verdict='thumbs_down'` = 50 (ghi OK) → gọi mọi admin route → **không endpoint nào trả con số này**; `aggregate_per_bot` không reachable từ HTTP. Thumbs vô hình với operator + eval.

**Expert fix (đúng tầng, EVOLVE):** thêm **1 read-endpoint** `GET /analytics/bots/{id}/feedback` gọi `aggregate_per_bot` (Q chuẩn 2026: thumbs phải surface lên dashboard + feed eval-dataset). Mid-term: subscribe `FeedbackGiven` → enrich eval golden set (production-trace→dataset flywheel, §3 🕰-A). KHÔNG cần table mới — repo đã có. Effort ~2h (1 route + DI wire).

### 🐛-2 · Closing-the-loop (refuse→FAQ) built-but-not-wired — 2 orphan

**Hole:** `FAQCandidateService` (`faq_candidate_service.py:111`) + `admin_refuse_suggestions` route (`:44`) đều mine refuse rows để đề xuất FAQ/surface refuse-intent — **đúng tinh thần closing-the-loop** (docstring `:10-15` "load test → REFUSE rows → clusters → operator fill → re-test"). NHƯNG:
- `FAQCandidateService`: grep ngoài file = **0 callsite** (không route/bootstrap/worker).
- `admin_refuse_suggestions.router`: **không trong `router.py`** (grep `admin_refuse` = 0) → endpoint unreachable.

**Immutable cause:** "built-but-not-wired" meta-pattern (P2-G §6 confirm). Code-loop tồn tại trên giấy, dây HTTP/DI chưa nối.

**Repro:** start app → `GET /api/ragbot/admin/bots/{id}/refuse_suggestions` → **404** (route chưa include). FAQ candidate: không có cách nào gọi từ ngoài.

**Expert fix (EVOLVE):** (a) `router.include_router(admin_refuse_suggestions.router, prefix=f"{BASE}/admin")` — 1 dòng. (b) wire `FAQCandidateService` vào bootstrap + 1 admin route `GET /admin/bots/{id}/faq-candidates`. Effort ~3h. Đây là **dây thiếu**, không phải khung sai — giữ nguyên service logic.

### 🐛-3 · Coverage (trục ĐỦ charter) KHÔNG observable từ analytics live

**Hole:** `pass_rate_per_bot` (`tenant_analytics_service.py:205-267`) tính PASS/REFUSE/HALLU nhưng **refuse-đúng (OOS) lẫn refuse-sai (corpus-có-đáp-án-mà-miss)** đếm CHUNG vào `refuse_count` (`:235`). Charter ĐỦ = `Coverage = answer_correct_when_corpus_has_answer / total_corpus_has_answer` — phân biệt 2 loại refuse này. Live analytics KHÔNG có DB-ground-truth join (chỉ eval offline `loadtest_graded.py` mới có `_db_has`).

**Immutable cause:** coverage cần "corpus-có-đáp-án-không?" = cần join `document_chunks` + judge, **đắt + không khả thi trên mọi turn live**. Analytics live chỉ count `request_logs` cột rẻ. Đây là gap **kiến trúc giữa offline-eval (có coverage) và online-analytics (chỉ refuse-rate)**.

**Repro:** bot refuse 30% — analytics báo `refuse_rate=30%` nhưng KHÔNG biết bao nhiêu % là silent-miss (corpus có) vs honest-OOS. Charter "Coverage≥0.95" không gate được từ dashboard.

**Expert fix (EVOLVE, mid-term):** sample 1-5% production trace → chạy `loadtest_graded` attribution (CORPUS-GAP vs RETRIEVAL vs PASS) như online-eval (§3 🕰-B chuẩn 2026: 1-5% sampling + 100% low-confidence). KHÔNG tính coverage mọi turn (đắt). Surface "estimated coverage" từ sample lên drift dashboard. Hiện tại: coverage = **chỉ đo được offline qua GRADED_** (which is fine cho gate, nhưng không live).

### 🐛-4 · D13 — eval governance: gold-authorship + judge-self-bias chưa thỏa "labeler không biết hệ thống"

**Hole-a (gold authorship):** `must_contain`/`fact_id` ở `qa_prod/<bot>.json` — **không có evidence ai viết gold, người đó có biết hệ thống không** (D13 §9.3: người gán nhãn KHÔNG biết hệ thống để tránh thiên vị). GRADING_SOP có **DB-verify** (`loadtest_graded.py:69` `_db_has`, label CORPUS-GAP `GRADING_SOP.md:21`) = chống fact-test-sai — TỐT, nhưng đó là "gold có trong corpus không", KHÔNG phải "gold được người độc-lập gán".

**Hole-b (judge self-preference):** `JUDGE="gpt-4.1-mini"` (`loadtest_graded.py:36`) = **cùng model bot dùng để answer + grade** (P2-E #12, 0195). Chuẩn 2026: LLM judge cùng model với generator → self-enhancement bias (~10% GPT, ~25% Claude — §3 🕰-C). Agent (cùng model) đang **gần như tự chấm answer của chính nó** = đúng cái D13 cấm.

**Immutable cause:** eval harness tối ưu reproducibility/cost (1 judge model), chưa tách judge-model ≠ answer-model + chưa có human-label process độc lập.

**Repro/đo:** chạy `loadtest_graded` với `JUDGE=gpt-4.1-mini` rồi `JUDGE=<model-khác-family>` trên cùng GRADED set → so agreement-rate; nếu mini-judge PASS cao hơn systematically = self-preference (chuẩn 2026: multi-judge agreement surface bias, no ground-truth needed). CHƯA chạy = **GIẢ THUYẾT có-điều-kiện**, không phải SỰ THẬT.

**Expert fix (EVOLVE):** (1) judge-model ≠ answer-model (đổi `JUDGE` sang model khác family — 1 dòng config). (2) Reference-Guided Verdict: đưa gold `must_contain` làm reference cho judge (ĐÃ có `_judge_prompt:124` truyền facts — đúng hướng, giảm self-bias). (3) D13 process doc: ai viết gold + reviewer ≠ author, ghi vào `program/eval/`. Effort: (1)+(2) ~1h; (3) = con-người, song song.

### 🐛-5 · `RagasMetricAdapter` = stub đo hằng số

**Hole:** `ragas_metric_adapter.py:70-88` trả `stub_score` (`DEFAULT_RAGAS_STUB_SCORE`) cho cả 4 metric, chỉ empty-context→faithfulness=0. **Không đo faithfulness/precision/recall thật** — mọi answer cùng điểm. Consumer duy nhất = offline `scripts/eval_ragas_metrics.py:195` (dùng stub default).

**Immutable cause:** Port-shape có (`RagasMetricPort:39-55`, swap real ragas qua Strategy) nhưng **engine thật chưa wire** — scaffold chờ nối (docstring tự thừa nhận `:8-11` "dev-tool scaffold, deterministic stub today").

**Expert fix (EVOLVE):** wire real `ragas` package qua Strategy (Port đã sẵn, KHÔNG đụng call-site) HOẶC dùng `loadtest_graded` claim-level coverage làm faithfulness-proxy (đã có DB-verify). Effort ~4h (real ragas provider + clamp test). Đây là engine-swap-qua-Port, đúng charter.

---

## 3. 🕰 — "chuẩn 2026 là gì" + verdict EVOLVE (3 web search)

### 🕰-A · Feedback loop: thumbs-INSERT vs production-trace→dataset flywheel
**Ragbot hiện tại:** ghi thumbs (`message_feedback`) + request_logs feedback_score, **0 read-loop** (🐛-1).
**Chuẩn 2026:** thumbs + implicit signals (abandonment, follow-up rephrase) + LLM-judge trên sampled queries → **continuous improvement flywheel**: "every failure becomes a test case, every fix validated against actual usage". Khuyến nghị: **tag negative feedback bằng lý do** ("Outdated/Missing-Context/Wrong-Source") → +5× feedback rate + root-cause. Generic thumbs-only = <0.1% interaction.
**Verdict EVOLVE:** nối read-loop (🐛-1 fix) + thêm `reason` column vào `message_feedback` (alembic) cho structured feedback. KHÔNG rewrite — table + RLS đã chuẩn, chỉ thiếu vế đọc + 1 cột reason.
Nguồn: [567-labs Systematically Improving RAG ch3.1 Feedback](https://567-labs.github.io/systematically-improving-rag/workshops/chapter3-1/) · [apxml User Feedback in RAG](https://apxml.com/courses/optimizing-rag-for-production/chapter-6-advanced-rag-evaluation-monitoring/user-feedback-rag-improvement)

### 🕰-B · Coverage / answerability + gap detection
**Ragbot hiện tại:** coverage chỉ offline (`loadtest_graded` attribution); live analytics = refuse-rate thô (🐛-3).
**Chuẩn 2026:** retrieval fail ~40% naive → **retrieval quality gates** assess answer-completeness + trigger additional retrieval khi gap. Production: **sample 1-5% traces qua full eval, 100% cho low-confidence outputs + new deploy**, dùng tracing (Langfuse). Baseline RAGAS golden-set → document gap faithfulness/recall/precision/context-recall.
**Verdict EVOLVE:** Ragbot có substrate (request_logs + GRADED golden) — thêm **sampled-online-eval** (1-5%) feed coverage estimate vào drift dashboard (🐛-3 fix). 33-step `request_steps` (P2-G #2) đã có low-confidence signal (top_score) để trigger 100%-sample. KHÔNG import Langfuse (V18 no-premature-obs) — dùng SQL sampling trên request_logs.
Nguồn: [datavlab RAG Evaluation 2026](https://datavlab.ai/post/rag-evaluation-methods-metrics-2026-guide) · [Lushbinary RAG Production Guide 2026](https://lushbinary.com/blog/rag-retrieval-augmented-generation-production-guide/)

### 🕰-C · LLM-judge self-preference (D13 core)
**Ragbot hiện tại:** judge=gpt-4.1-mini = answer-model family; gold-author không tách (🐛-4).
**Chuẩn 2026:** LLM tự chấm output mình → **self-enhancement bias** (GPT-4 +10% win-rate own output, Claude ~25%); bias gốc ở perplexity-familiarity. **Mitigation:** (1) judge-model ≠ generator-model; (2) **Reference-Guided Verdict** (đưa gold làm reference — align về objective criteria); (3) multi-judge agreement-rate surface bias không cần ground-truth.
**Verdict EVOLVE:** Ragbot ĐÃ làm đúng (2): `_judge_prompt` truyền `must_contain` gold làm reference (`loadtest_graded.py:124`) → giảm self-bias đáng kể. Còn thiếu (1) tách judge-model + (3) multi-judge cross-check. Fix (1) = 1 dòng config. D13 human-label process = con-người song song agent.
Nguồn: [arXiv Self-Preference Bias in LLM-as-a-Judge 2410.21819](https://arxiv.org/pdf/2410.21819) · [Adaline LLM-as-Judge reliability/bias](https://www.adaline.ai/blog/llm-as-a-judge-reliability-bias)

---

## 4. TRẢ LỜI 5 CÂU (evidence-driven)

**Q1 — Feedback loop thật hay cụt?** → **CỤT.** `message_feedback` ghi LIVE (`feedback.py:131`, RLS-scoped) + `request_logs.feedback_score` (`give_feedback.py:42`) + `FeedbackGiven` outbox (`:62`). NHƯNG vế ĐỌC chết: `aggregate_per_bot` 0 caller (#3), `FeedbackGiven` 0 subscriber (#4). KHÔNG vòng học (thumbs→eval/FAQ/sysprompt). **D12 = vế-ghi-có, vế-học-thiếu.** Đây là câu cốt lõi: feedback INSERT rồi nằm chết.

**Q2 — Refuse/miss/coverage analytics + FAQ-from-refuse?** → **Refuse-rate CÓ** (`tenant_analytics_service.py:235` tenant-scoped RLS-correct ✅). **Coverage KHÔNG** live (#8 — refuse-đúng lẫn refuse-sai, charter ĐỦ không observable). **Câu corpus-thiếu**: dữ liệu CÓ (`request_logs.refusal_reason/answer_type`) nhưng read-path orphan (`admin_refuse_suggestions` unwired #6, `metrics_top_questions only_failed` ✅ là cái duy nhất live #10). **FAQ-from-refuse**: `FAQCandidateService` logic ĐÚNG closing-the-loop nhưng **0 callsite** (#5) = không chạy.

**Q3 — Eval governance: ai chấm? self-grade? ragas đo gì?** → judge `gpt-4.1-mini` (`loadtest_graded.py:36`) = **cùng model-family với answer-gen → self-preference bias** (#12, vi phạm tinh thần D13). **Gold-author** (`qa_prod/*.json`) không có evidence người-độc-lập-không-biết-hệ-thống (#11) — nhưng có DB-verify chống fact-test-sai (`_db_has:69`, tốt). **Mitigant đúng**: gold làm reference cho judge (`_judge_prompt:124`) giảm bias. **ragas**: STUB đo hằng số, không đo gì thật (#13). Eval thật chạy bằng `loadtest_graded` claim-coverage (đo thật) chứ không phải ragas-adapter.

**Q4 — KIỂM SOÁT: quyết định pipeline surface cho bot-owner?** → **MỘT PHẦN.** Operator XEM ĐƯỢC: pass/refuse/cost/latency/drift (`admin_analytics` wired ✅), top-failed-questions (`metrics_top_questions` ✅), step-timing (`metrics/steps` ✅, P2-G 33-step). **KHÔNG xem được**: thumbs-counts (#3 orphan), refuse-intent-suggestions (#6 unwired), FAQ-candidates (#5 orphan), persona-quality-warnings (#14 orphan). "Lý do refuse vì sao / chunk nào" = nằm `request_logs.refusal_reason` + `request_steps` nhưng **chưa có endpoint surface per-decision** cho owner. Khớp P2-G "cost read-query thiếu" — pattern observability-DB-có-nhưng-read-path-orphan lặp lại ở analytics.

**Q5 — No-premature-observability (V18) vi phạm?** → **KHÔNG.** grep `prometheus_client/Counter(/Histogram(/start_http_server` trên cả 7 file scope = **0 hit** (#16). Mọi analytics = SQL aggregate trên `request_logs`/`request_steps` + structlog. **Tuân V18 tuyệt đối.** (Cảnh báo ngược: thiếu read-path, không thừa collector.)

---

## 5. ĐÃ CHUẨN — ĐỪNG ĐỤNG (praise, EVOLVE = giữ)

1. **`TenantAnalyticsService` refuse/cost/latency/drift** (`tenant_analytics_service.py:205-716`) — tenant-scoped `WHERE record_tenant_id=:tid` mọi query, `_ensure_tenant` reject None ở boundary (`:192-200`), 2-window drift, percentile_cont. RLS-correct, domain-neutral, no-LLM-inject. **Đập = lỗi nặng.** Đây là phần KIỂM SOÁT live tốt nhất.
2. **`message_feedback` table + RLS repo** (`message_feedback_model.py`, `message_feedback_repository.py`) — 4-key identity, `session_with_tenant` mọi read/write, verdict enum SSoT. Schema CHUẨN — vấn đề chỉ là vế-đọc chưa nối (🐛-1). Giữ table, thêm read-route.
3. **DB-ground-truth-verify trong eval** (`loadtest_graded.py:69` `_db_has` + `GRADING_SOP.md:21` CORPUS-GAP label) — chống fact-test-sai (lesson spa "10 vs 21 bước"). Đây là chốt chặn quý, hiếm eval-harness có. Giữ.
4. **3-run flip / determinism** (`loadtest_graded.py:37,182`) — đo non-determinism, ahead RAGAS-only (P2-G #5 đồng ý). Giữ.
5. **Reference-Guided judge** (`_judge_prompt:124` truyền gold facts) — đã giảm self-preference bias đúng chuẩn 2026. Giữ, chỉ thêm tách judge-model.
6. **App-mindset trong mọi service**: `FAQCandidateService` operator-fill no-LLM-inject (`:16-26`), `PersonaQualityGate` audit-only no-override (`:2-6`), `RagasMetricAdapter` read-only observation (`:13-17`). **Sacred #10 GIỮ NGUYÊN** kể cả ở code orphan. Khi wire, giữ posture warn-only này.

**Tóm tắt EVOLVE:** không component nào cần rewrite. 8 🐛 đều là **nối-dây** (read-route cho thumbs/FAQ/refuse/persona · include orphan router · tách judge-model · swap ragas-stub→real qua Port · sampled-online-coverage). Framework analytics + feedback-table + eval-harness CHUẨN; vấn đề = **"vết đứt giữa write-có-sẵn và read/loop chưa nối"** — đúng meta-pattern "built-but-not-wired" toàn dự án. D12 loop wire ~5h · D13 judge-model + process ~2h+con-người.

---

## 6. MAP VỀ DECISION REGISTER

| Phát hiện | D12 | D13 | Wave |
|---|---|---|---|
| 🐛-1 thumbs INSERT-chết, FeedbackGiven 0-subscriber | ✅ core | — | W6 |
| 🐛-2 FAQCandidate + refuse_suggestions orphan (closing-loop) | ✅ | — | W6 |
| 🐛-3 Coverage không live (chỉ offline) | ✅ (analytics câu corpus-thiếu) | — | W6 |
| 🐛-4 judge=answer-model self-bias + gold-author không tách | — | ✅ core | trước W5 eval |
| 🐛-5 ragas stub | — | ✅ (đo thật) | trước W5 eval |
| 🕰-A structured-feedback reason column | ✅ | — | W6 |
| 🕰-B sampled-online-eval coverage | ✅ | ✅ | W6 |
