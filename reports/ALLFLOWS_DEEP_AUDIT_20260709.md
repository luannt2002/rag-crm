# ALL-FLOWS DEEP AUDIT — code-truth (2026-07-09)

> **Method**: 11 flow auditors read the CODE (not .md) at `file:line`, cross-verified against
> live DB + load-test data; each finding then adversarially re-verified (skeptic re-opens the
> file, defaults to REFUTED). Multi-agent workflow `wf_521d6a0b-8f5` (15 agents done + verify).
> **Rule #0**: every claim below carries a `file:line` or DB fact. Nothing from .md is trusted.

---

## 0. Executive truth (sự thật xuyên suốt)

**Chủ đề lớn: "đã CÓ ≠ đã BẬT ≠ đã TỐT".** Khung rất expert, nhưng **rất nhiều cỗ máy expert đang INERT / DEAD / DRIFTED** — code quảng cáo một đằng, runtime chạy một nẻo. Đây đúng là bài học EXISTS/WORKS/VERIFIED.

1. **9 cỗ máy "expert" đang chết hoặc trơ** (bằng chứng file:line): AdapChunk block-pipeline (ON nhưng no-op), `rrf_round_robin` entity-fairness (dead code, chưa wire), `extract_all_codes` comparison-fix (định nghĩa + test, **chưa gọi**), understand_query cache (**không bao giờ ghi**), RLS 3-lớp (100% trơ vì superuser), MMR ceiling (hằng 0.98 nhưng DB 0.88), cliff floor (hằng 0.05 nhưng DB 0.2), config parity-guard (chỉ soi query_graph.py, mù 152 pcfg ở nodes/), `audit_pipeline_cfg_parity.py` (comment nhắc tới, **file không tồn tại**), health-probe worker-liveness (docstring hứa, không check).
2. **HALLU spa S-005 (bịa hotline) có nguyên nhân code rõ**: numeric-fidelity **strip số điện thoại TRƯỚC khi classify** → mù đúng loại HALLU đã xảy ra (`numeric_fidelity.py:40,51-53`); cộng nhánh chitchat bỏ `<documents>` + tắt grounding (`generate.py:328,342,707`).
3. **Comparison 0/4 là chuỗi 5 tầng lỗi**, không phải 1: decomposer prompt **xé nát mã spec** (`query_decomposer.py:59-63`) → cổng 8-token chặn câu ngắn (`routing.py:118`) → dedup theo **chunk_id hằng số** drop vế-2 (`retrieve.py:188-191`) → `rrf_round_robin` fairness **dead** → `extract_all_codes` **chưa wire**. (Đúng cái em đã revert.)
4. **Perf p50 45s do 3 nguồn code**, không chỉ endpoint: understand_query **luôn ~15s** vì cache chết (`DB: 302/302 fire, p50 15.3s`) + sync grounding judge **timeout 30s trên 69% request** rồi trả None (`DB: 31/45 running`) + retry 3×90s = tối đa 270s (`_04_jwt_auth.py:180`).
5. **CORRECTION quan trọng (honest)**: ≥3 trong "14 câu sai" thực ra là **infra generate-fail** (LLM provider InternalServerError, output_tokens=0) SAU khi retrieval thành công — bị gán nhầm nhãn orchestration/retrieval. Correctness thật có thể **CAO hơn 93%** nếu lọc `status='failed'` trước khi chấm.
6. **RLS = single-layer**: 24 policy + role split code đúng & expert, NHƯNG app connect superuser (`RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, `DATABASE_URL_APP` unset) → mọi policy **bị bypass**; cô lập thật chỉ nhờ `WHERE record_bot_id` (rigorous, nhưng 1 lớp).
7. **2 rủi ro vận hành cao**: URL-ingest **fetch full body vào RAM không giới hạn** trong process API chung → OOM giết cả chat (`document_worker.py:443-448`); cost_cap + cache_purge **chết câm trên SQLAlchemyError**, không restart, không health-visible (`embedded_workers.py:163,203`).
8. **Correctness không lưu vào DB**: `request_logs.is_correct` NULL 302/302 → baseline "93% / HALLU=1" **không kiểm chứng được từ system-of-record** (chỉ ở store ngoài). Vi phạm measure-before-claim.

---

## 1. Bảng per-flow (trạng thái + sự thật + fix đúng tầng)

| Flow | Trạng thái | Điểm nghẽn / che giấu / chưa-expert (evidence) | Fix đúng tầng |
|---|---|---|---|
| **Ingest U0–U7** | ⚠️ cần vá | Block-pipeline ON nhưng no-op, luôn `smart_chunk(flat)` (`ingest_stages.py:582-668,763-775`); row-preserve **chết trên worker path** (`document_worker.py:464` flatten, `raw_bytes=None`) → cùng file chunk khác nhau theo route; coverage-drop observe-only (`ingest_stages.py:869-905`) | Wire block-native HOẶC đổi tên flag + gỡ plumbing chết; thread row-chunks vào ingest; nâng coverage-gap thành ingest-QC gate |
| **Retrieve + stats** | ⚠️ cần vá | dedup **chunk_id hằng** drop vế-2 (`retrieve.py:188-191`); `extract_all_codes` chưa wire (`query_range_parser.py:509`, 0 call-site); brand-filter đọc **cả câu** mỗi leg (`query_graph.py:2587`) | dedup theo content-hash/id-duy-nhất; wire extract_all_codes + brand per-leg (fix L3 comparison đã ghi `comparison_rootcause_20260709.md`) |
| **Rerank + filter** | 🟢 phần lớn expert | **safety-net re-union expert** (`rerank.py:474-518`); NHƯNG `rrf_round_robin` fairness **dead** (0 src import); MMR 0.98 vs DB 0.88; cliff floor 0.05 vs DB 0.2 (constant/DB drift) | wire rrf_round_robin cho comparison/multi_hop; alembic đồng bộ MMR+floor; sync hằng↔DB |
| **Grade (CRAG)** | 🟢 expert, 1 lỗ | Double-bounded loop, 3-state đúng; lỗ over-refuse: chunk 'irrelevant' bị drop kể cả khi rerank-score cao (`grade.py:296-297`) | rescue top-1-by-rerank-score cho nhánh mixed |
| **Generate** | ⚠️ 1 lỗ HALLU | sacred-#10 sạch (answer verbatim); NHƯNG chitchat bỏ `<documents>` + bypass refuse + grounding off (`generate.py:328,342,707`) → misclassify = ungrounded (khớp S-005); structured-fail → **2 generation** | chitchat vẫn kèm docs khi có graded; giữ guard khi answer có token số; tái dùng response_text thay vì gen lần 2 |
| **Guards / anti-HALLU** | ⚠️ mù đúng chỗ | observe→block ladder đúng kỷ luật; NHƯNG numeric-fidelity **strip phone trước classify** → mù S-005 (`numeric_fidelity.py:40,51-53`); derived-valid a±b combinatorial launder; grounding chỉ 5 câu đầu, ratio>0.3 | chỉ strip contact khi có verbatim trong context; siết derived allow-list (cùng row); phủ toàn answer |
| **Front orchestration** | ⚠️ cache chết | understand cache **không bao giờ ghi** (`understand.py:282` test hàm-object) → mỗi turn 15s; decomposer **xé mã spec**; comparison bị cổng 8-token chặn | sửa memo bool; thêm luật atomic-identifier vào decomposer; bỏ cổng 8-token cho comparison |
| **RLS / tenant** | 🔴 trơ (defense-in-depth mất) | 3-lớp RLS **100% inert** (superuser, `DATABASE_URL_APP` unset); workspace GUC không set trên HTTP path; stats delete **unscoped** (`stats_index_repository.py:251-259`) | flip DSN → ragbot_app + startup-assert NOBYPASSRLS; forward workspace_id; thêm `record_bot_id` vào delete |
| **Config resolution** | ⚠️ guard mù | parity-guard chỉ soi query_graph.py (43 keys), **mù 152 pcfg ở nodes/** (`test_pipeline_cfg_keys_parity.py:35`); 9 knob prod không cấu hình được; demo≠prod resolution; `audit_pipeline_cfg_parity.py` **không tồn tại** | mở rộng pin-test ra nodes/ + so builder-dict; thêm 9 key vào worker builder; sửa comment guard |
| **Workers** | 🔴 2 rủi ro cao | exactly-once outbox **expert**; NHƯNG URL-ingest **OOM cả process** (no size cap, `document_worker.py:443-448`); cost_cap+cache_purge **chết câm** SQLAlchemyError; health không check worker | stream+byte-ceiling fetch; thêm SQLAlchemyError vào catch; expose worker liveness ở /health |
| **Perf vs Correct** | ⚠️ nghẽn rõ | understand 15s luôn + sync grounding timeout 69% + retry 3×90s; **≥3 "câu sai" là infra-fail gán nhãn sai**; is_correct NULL 302/302 | async grounding; retry 3→2 + wall-clock budget; lọc status=failed; persist verdict vào DB |

Legend: 🟢 đã expert · ⚠️ cần vá · 🔴 rủi ro cao.

---

## 2. Top 10 issue (xếp theo severity × blast-radius)

| # | Issue | Tầng | Evidence | Fix | Ưu tiên |
|---|---|---|---|---|---|
| 1 | URL-ingest fetch full body vào RAM → OOM cả process (giết chat đang chạy) | Workers | `document_worker.py:443-448` (`_resp.content`, no max_bytes) | stream + byte-ceiling | **T1/ops** |
| 2 | Sync grounding judge timeout 30s trên 69% request rồi trả None (mất 30s/câu, 0 giá trị) | Perf/Guards | `guard_output.py:698`; DB 31/45 running@30s | bật async grounding (đòn bẩy đã có `query_graph.py:930`) | **T1** |
| 3 | numeric-fidelity mù số điện thoại bịa (đúng loại HALLU S-005) | Guards | `numeric_fidelity.py:40,51-53` | chỉ strip contact khi verbatim-in-context | **T1** |
| 4 | Chitchat misclassify → answer ungrounded, không guard (S-005) | Generate | `generate.py:328,342,707` | giữ docs+guard khi có graded/token số | **T1** |
| 5 | RLS 100% inert → cô lập chỉ 1 lớp app-filter | RLS | superuser + `DATABASE_URL_APP` unset | flip DSN + startup assert | **T1/ops** |
| 6 | Comparison 0/4 = 5 tầng (decomposer xé mã + 8-token + dedup-const-id + rrf dead + extract chưa wire) | Retrieve/Front | `query_decomposer.py:59-63`, `routing.py:118`, `retrieve.py:188-191` | fix L3 brand-disambig + wire, đo N≥10 | **T1** |
| 7 | understand_query 15s mọi turn (cache chết) → sàn latency 9-34s/câu | Front | `understand.py:282`; DB 302/302 | sửa memo bool + intent rẻ trước | **T1** |
| 8 | cost_cap+cache_purge chết câm SQLAlchemyError, không restart/health | Workers | `embedded_workers.py:163,203` | thêm SQLAlchemyError vào catch | **T2** |
| 9 | Config parity-guard mù 152/195 pcfg (ở nodes/) → drift ship mà CI xanh | Config | `test_pipeline_cfg_keys_parity.py:35` | mở rộng pin-test ra nodes/ | **T2** |
| 10 | ≥3 "câu sai" là infra generate-fail gán nhãn sai + is_correct NULL 302/302 | Perf/Eval | DB failed rows; is_correct NULL | lọc status=failed + persist verdict | **T2** |

---

## 3. Correctness vs Performance (TÁCH BẠCH — không lẫn)

### 3a. Correctness (đúng/sai) — ranked
1. **HALLU S-005 (phone)**: 2 nguyên nhân code (numeric-fidelity strip + chitchat ungrounded) — #3,#4.
2. **Comparison 0/4**: 5-tầng chain — #6 (một phần là infra-fail #10, cần lọc trước khi chấm).
3. **Over-refuse hole grade**: chunk đúng bị drop khi mixed-ambiguous (`grade.py:296-297`).
4. **Coref spa (4)** + **coverage-miss (3)**: chưa audit sâu ở đây — nhóm retrieval/orchestration còn lại.

### 3b. Performance (tốc độ) — ranked (đo DB thật)
1. **understand_query 15s × mọi turn** (cache chết) — sàn latency.
2. **sync grounding judge 30s timeout 69%** — mất công cốc trên critical path.
3. **retry 3 × 90s = 270s worst-case** — khuếch đại endpoint chậm.
4. **Root bất biến**: endpoint innocom 3-30s/call (external) — đòn bẩy lớn nhất là đổi provider.
> BE-code (tra DB 88ms, rerank 1.5s, grade 0.37s) **KHÔNG phải nghẽn** — giữ nguyên.

---

## 4. Chỗ ĐÃ expert (trung thực — đừng đụng)

- **Exactly-once outbox** (`FOR UPDATE SKIP LOCKED` + mark-in-session + 1 tx) — chuẩn sách.
- **RRF fusion** Cormack canonical + single-list fallback (`multi_query_expansion.py:565,588,597`).
- **Retrieval safety-net re-union** (union top-2 pre-rerank khi zerank bất đồng, stamp min-score, generate giữ `_safety_injected`) — forensic-driven, rất tốt.
- **CRAG grade** 3-state, double-bounded loop, top-2 graceful early-return.
- **Generate sacred-#10 sạch**: answer đọc verbatim, 0 override; refuse = bot's oos_template (rỗng khi unset), 0 injected text.
- **Guards observe→block ladder** per-bot đúng kỷ luật (measure FP trước khi block).
- **RLS code** (after_begin SET LOCAL, UUID-validated interpolation, request/system split) — code đúng & expert, chỉ chờ ops flip DSN.
- **resolve_bot_limit** 5-tier chain + schema range-guard; `_pcfg` None-as-missing (Bug#12 fix).

---

## 5. Thứ tự fix đề xuất (T1 smart > T2 cost/perf > T3 pattern)

**Đợt 1 (T1 — an toàn + đúng, làm ngay, mỗi cái đo trước/sau):**
1. numeric-fidelity: chỉ strip contact khi verbatim-in-context (#3) — bịt HALLU S-005.
2. chitchat: giữ `<documents>` + guard khi có graded (#4).
3. URL-ingest stream + byte-ceiling (#1) — chặn OOM.
4. Lọc `status='failed'` trước khi chấm correctness (#10) — số đúng thật.

**Đợt 2 (T1/T2 — perf, đòn bẩy lớn):**
5. Bật async grounding (#2) — cắt ~30s/câu factoid.
6. Sửa understand memo bool + intent rẻ trước (#7) — cắt sàn 15s.
7. retry 3→2 + wall-clock budget (#8-perf).

**Đợt 3 (T1 — comparison, cần đo N≥10):**
8. Fix L3 brand-disambiguation + wire extract_all_codes + dedup id + bỏ 8-token gate (#6).

**Đợt 4 (T2/T3 — hardening, không đổi hành vi):**
9. RLS flip DSN + startup assert (#5, cần credential owner).
10. Config parity-guard mở rộng nodes/ (#9); worker SQLAlchemyError catch (#8); sync hằng↔DB (MMR/floor); persist is_correct.

---

## 6. Ghi chú phương pháp

- 15/24 agent hoàn tất pha audit; 8 verify + synthesis đang chạy lại (resume `wf_521d6a0b-8f5`) sau khi chạm session-limit. 3 flow đã verify đối kháng (retrieve/ingest/grade) — tất cả CONFIRMED. Các finding còn lại đều kèm file:line, chờ verify để nâng CONFIRMED/REFUTED.
- Mọi số DB đọc lúc audit (load-test 2026-07-08 window). is_correct chưa persist → coverage/HALLU đọc từ store ngoài (đã flag #10).

*Nguồn: `wf_521d6a0b-8f5/journal.jsonl` (15 agent) + live DB. Cross-ref: `LOADTEST_RESULT_20260708.md`, `comparison_rootcause_20260709.md`, `CONFIG_FLOW_DEEPDIVE_20260708.md`.*
