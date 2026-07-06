# Remediation Ladder — append-only log

## Step 0 — Measurement infra (2026-07-03, no bot-behavior change)
- Change: harness `--repeat` + cache-assert + corpus-stamp + verdicts; retry-on-RATE_LIMITED.
- Tests: tests/unit/test_trace_harness_repeat.py 8/8 PASS.
- Evidence: baseline_runs.json (135 runs), baseline_report.md, primacy_runs.json.
- Primacy verdict (N=45): first-row brand 15/15 đúng; second-row brand sai 12/30 (40%),
  100% lỗi lấy giá dòng đầu → PRIMACY = SỰ THẬT.

## Step 1 — DATA: purge stale pre-gate stats rows, bots 111/123 (2026-07-03)
- Change: scoped re-extract via canonical parse_table_chunks + StatsIndexRepository
  (delete_by_document unconditional — tracked script's `if not entities: continue` gap noted
  for T013). NO code change, NO chinh-sach-xe touch (corpus stamp unaffected).
- Delta: bot 123 entities 62→19, bot 111 117→81; garbage(≤3-char names) 5+4→**0**;
  'chủ/đủ/tục/cứu' = 0 rows.
- Residual: remaining price-less prose entities = T012 positive-table-evidence gate (RED test pending).
- Rollback: re-run scripts/db/backfill_stats_index.py (idempotent) — not needed.
- Blast-radius: bots 111/123 stats retrieval only; no shared-code change.

## Step 2 — NEXT: Phase 3 option (b) serve filter (RED test first)
- Planned per decision_shell_entities.md; one change, re-run probe9 N=15 + pinned-60 after.

## Step 2 — CODE: serve-side shell filter, option (b) (2026-07-03)
- Change (ONE toggle): `stats_serve_require_value` — repo `_value_bearing_predicate()` gated
  vào 3 customer path (forward keyword + reverse fallback + list_all) + knob plan_limits +
  constant DEFAULT_STATS_SERVE_REQUIRE_VALUE=True + wiring _do_stats_lookup + 2 builders.
- RED→GREEN: tests/unit/test_stats_serve_value_filter.py 6 fail trước → 6 pass sau;
  regression 23+96 pass; parity builders pass; grep-proof 0 bot/brand literal.
- Delta probe9 N=15 (step2_runs.json vs baseline_runs.json):
  * Lệch-GIÁ P-02/03/04: 45/45 → 10/45 (-78%). P-04 12/15 refuse-đúng.
  * P-01 unchanged (by design — date-bearing row + raw-chunk path → Phase 4).
  * P-07 6/15→15/15 KHÔNG attributable (filter không đụng row có giá; 3-batch variance
    6/15→12/15→15/15) — cần brand-priority fix + đo lặp riêng.
  * F-NEW: fabricated STOCK-STATUS ("hết hàng") ~11 runs P-02 — LLM đánh đồng absence↔0;
    không phải bịa số → numeric-gate không bắt; cần status-claim handling (Phase 4/5).
- Residual lệch-giá (10/45) = raw-document-chunk path — đúng phạm vi Phase 4.
- Rollback: flip DEFAULT_STATS_SERVE_REQUIRE_VALUE=False hoặc per-bot plan_limits;
  criteria: existence-questions pinned-60 rớt >2 câu (đang đo: step2_pinned60.json).
- Blast-radius: mọi bot có stats index (6 bots); paths: stats keyword/list/count;
  raw-chunk path KHÔNG đổi; pin tests: test_stats_serve_value_filter.py + parity suite.

## Step 3 — CODE: T012 positive-table-evidence gate (2026-07-03)
- Change (ONE): parse_table_chunks minting gate — PRICE-LESS entity chỉ được mint khi
  row là pipe/tab HOẶC header structural (_is_header_row token/separator; _is_shape_header
  heuristic KHÔNG tính — chính nó promote prose thành pseudo-header, gap B).
- RED→GREEN: test_stats_extract_noise.py — 2 prose fixtures THẬT (empirical mint-scan,
  bot-123 raw chunks) fail trước → pass sau; 3 positive controls (pipe delivery-sheet
  2-row-merged header + priced catalog + comma-CSV vocab-header) pass cả trước lẫn sau.
  Regression: 816 pass / 0 fail.
- Delta (re-extract canonical): bot 123: 19 → 0 (prose thuần) · bot 111: 81 → 62
  (19 prose killed; 62 giữ = bảng lịch-về pipe THẬT — đúng thiết kế) ·
  chinh-sach-xe: 242 unchanged (blast-radius held).
- Residual-gap A+B của _is_noise_entity: đóng luôn bằng gate này (prose không còn tới
  được _extract_entity_from_row với evidence pass).
- Rollback: revert commit (gate là additive condition, không đổi schema).
- Blast-radius: ingest-time extraction mọi bot; serve-time không đổi; pin =
  test_stats_extract_noise.py + 816 stats/chunk suite.

## Step 4 — CODE: numeric-fidelity gate OBSERVE (2026-07-03)
- Change (ONE): shared/numeric_fidelity.classify_answer_numbers (pure; tokenizer SSoT
  chung với find_dropped_numbers) + guard_output tính 1 lần trên answer GỐC trước mọi
  branch block + structlog NUMERIC_FIDELITY_EVENT + debug.numeric_fidelity + harness field.
  OBSERVE-ONLY: sacred-#10 pin test cấm verdict gate answer.
- RED→GREEN: test_numeric_fidelity_gate.py 9/9 (fail trước vì module absent);
  regression 22+109 pass incl. AST state-key pin.
- T042 metrics (N=15 probe batch, journal server-side):
  * CATCH: 9/9 fabrication events flagged đúng token ["26.000.000"] — 100%
  * FALSE-POSITIVE: 0/82 events sạch trên mọi control/lệch runs — 0%
  * Lệch (P-02/03/04) flagged 0/15 mỗi probe — ĐÚNG THIẾT KẾ (số thật, gate mù với
    wrong-entity → attribution-check là lớp riêng, Step 5 design)
- Bug tìm thấy khi đo: in-place state write bị LangGraph drop qua node boundary →
  fix: cả 6 return dict của guard_output mang "numeric_fidelity"; smoke end-to-end:
  FAB flag đúng, CTRL sạch.
- OWNER GATE chờ: blocking-mode chỉ được bàn với 2 số trên (FR-010) — khuyến nghị:
  đủ điều kiện kỹ thuật (FP=0) nhưng chờ thêm pinned-60 observe trước khi đề xuất.
- Blast-radius: guard_output mọi bot (observe, không đổi answer); pin =
  test_numeric_fidelity_gate + grounding_confirmed_action + graphstate_key_pin.

## GP-100 lần đầu — corpus re-upload sạch (2026-07-04, evidence/gp100_run.json)
- 100 câu × 3 run = 300 answers, exit 0 (cache bypassed, corpus stable md5=055392ff73).
- PER-RUN: ĐÚNG 233 + refuse-đúng 43 = 276/300 (92%) · SAI 16 · LỆCH 6 · BỊA 2.
- PER-QUESTION grader-v1: 90/100 chuẩn-trọn-3-run · 10 fail cứng.
- AUTOPSY 10 fail (rule #0 — không nhận số đẹp không đổ oan số xấu):
  * REAL 3 câu: G-075 bịa Neoterra 2/3 run (gate flag sống cả 2 — chờ blocking-mode);
    G-090 LỆCH size (row R14 729k liệt kê thành variant R13 — detector TP#1);
    G-097 LỆCH giá (195/60R15 thật 963k, bot 810k — detector TP#2, DB-confirmed).
  * 6 câu arrival_date = LỖI THIẾT KẾ BỘ CÂU HỎI (size vừa có lịch-về vừa còn hàng;
    bot trả in-stock+giá thật, 0 bịa ngày; G-064 2/3 run vẫn nói 28-thg-11) → THIẾU-ý.
  * 1 câu G-070 = detector FP (token phone '0988' + 'kho') — hotline trả ĐÚNG;
    tune: misattr chỉ xét value price-scale (min digits ≥5/6).
- Misattr detector scoreboard GP-100: 2 TP / 1 FP — bắt được 2 lệch mà numeric-gate
  mù (số thật sai chỗ), đúng mục đích thiết kế.
- HONEST QA-COMPARABLE: 90 chuẩn + 6 chưa-đúng-ý + 1 oan-FP + 3 SAI THẬT
  (baseline QA cũ: 30/100). HALLU=chỉ còn Neoterra class, bị flag 100%.

## Step 6 (002-A) — CODE: condense-gate drift fix (2026-07-04)
- Change (ONE): shared/condense_gate.has_meaningful_history (pure, semantics 2026-05-27
  `>= min_turns`) + wire understand.py (bỏ hand-rolled `>` — bug: turn-2 mất history) +
  condense_question.py dùng chung; pin cũ update sang drift-proof (assert 2 node gọi helper).
- RED→GREEN: test_condense_gate_parity 3 fail trước → pass; regression 113 pass
  (1 pin cũ pin literal string được update có chú thích — behavior giữ nguyên).
- ĐO N=10 × 3 chain từng fail (step1_chains_n10.json):
  * Turn-2 coreference: K-462 9/10 (baseline 0), K-492 8/10 (baseline 0),
    K-512 đúng-size 10/10 → CLASS A FIXED (~90-95% vs 0%).
  * Residual class KHÁC: bịa-link URL turn-3 (K-463) — numeric-fidelity gate flag 10/10
    (deterministic catch ✓) → issue mới "link-fidelity" vào Phase-4 scope;
    1 primacy 155/80R13 (cluster C); K-512 expect quá hẹp (lỗi bộ đo, không phải bot).
- Blast-radius: understand_query mọi bot (condense giờ FIRE ở turn-2 — thêm 1 LLM call
  condense cho first-follow-up: đúng thiết kế 05-27, cost đã được chấp nhận từ fix cũ);
  pin = test_condense_gate_parity + test_condense_rewrite_multi_turn.
- Rollback: revert commit (helper additive).

## Step 7 (002-C, 2 sub-changes cùng class — attribution per-request qua structlog) — 2026-07-04
- Changes: (1) speculative composition-aware (`_speculative_keep_allowed` — sub_queries ≥2
  không short-circuit fan-out); (2) stats-per-sub-query join (`_stats_chunks_for_sub_queries`
  — mỗi leg so-sánh có point-lookup, synthetic chunks NHẬP fan-out set, không short-circuit).
  +2 hotfix khi wire: scope `_routing_signals`, alias `_get_routing_signals`.
- RED→GREEN: test_speculative_composition_gate (2) + test_stats_per_subquery (3) fail trước
  → pass; regression 413 pass (1 test decompose bắt được scope-bug thật của em trước khi ship).
- ĐO N=10 × 4 probe (step23_cprobe_n10.json), attribution: speculative_skipped=30, stats_joined=30:
  * C-2 (225/45ZR17 vs 225/50ZR17): đủ-2-leg **10/10** (baseline L-014 refuse-oan)
  * C-3 (LPD vs RVL 195/65R15): đủ-2-leg **10/10**, 2 giá thật (baseline L-005 BỊA 1.050.000)
  * C-4 control: 10/10, nf sạch 40/40 run
  * C-1 residual: leg-1 fixed 10/10 (1.170.000 ✓); leg-2 chọn near-size 255/40 (rank-pick;
    row 235/40 ĐÃ được serve — capture 500-char che giá; sub-stats leg-2 không hit) → điều tra
    ở D-step (mmr/rank) + B-step (capture cap).
- Blast-radius: retrieve fan-out mọi bot khi decompose active; pin = 5 test mới + suite retrieve.

## Step 8 (002-D) — CODE: mmr survivor floor + threshold recalibrate (2026-07-04)
- ĐO TRƯỚC KHI CHỐT SỐ (đúng plan): zembed-1 same-doc distribution — distant-section
  p50=0.975/max=0.990 vs adjacent p50=0.982 → CHỒNG LẤN, không threshold nào tách được;
  0.88 cũ dedup oan 100% distinct-section pairs. → floor là fix chính, không phải threshold.
- Changes (ONE class): mmr_filter(min_keep) — dưới floor thì force-keep theo relevance;
  DEFAULT_MMR_MIN_KEEP=3 + knob mmr_min_keep (_pcfg chain); DEFAULT_MMR_SIMILARITY_THRESHOLD
  0.88→0.98 (comment ghi measurement + bài học threshold-drift-post-migration).
- RED→GREEN: test_mmr_min_keep_floor 4 test; 1 contract-test cũ update có chú thích
  (dedup chỉ thể hiện TRÊN floor); mmr suite 60 pass; regression 443 pass
  (1 fail test_generate_intent_max_tokens = PRE-EXISTING trên HEAD, stash-proof, ngoài scope).
- ĐO N=10 × 4 câu bảo hành (step4_dprobe_n10.json):
  * D-2 mòn↔còn: 8/10 đúng "60%", 0/10 sai-chiều · D-4 control 1.6mm: 10/10 · nf sạch 40/40
  * D-1 xe-tải-scope: VẪN BỊA — mechanism RECLASSIFIED: chunk [I. Phạm vi] KHÔNG được
    retrieve 0/10 (top=III/II/VII) → tầng lỗi retrieval/rerank ranking, KHÔNG phải mmr
    → OPEN follow-up (section-ranking cho câu scope-exclusion).
  * D-3 bịa 8-9mm: 10/10 — world-knowledge unit-number, gate mù (min_digits=4 bỏ '8mm')
    → OPEN follow-up: numeric-fidelity unit-token extension (mm/kg/inch nhỏ).
- Blast-radius: mmr_dedup mọi bot (floor có thể tăng nhẹ context size khi trước đây
  collapse — chủ đích); pin = test_mmr_min_keep_floor + test_node_mmr_dedup.

## Step 9 (002-B) — HARNESS: capture cap 500→2000 + truncated flag (2026-07-06)
- Change (ONE, measurement-infra — KHÔNG đổi bot behavior): `_record` dùng
  TRACE_CHUNK_CAPTURE_MAX_CHARS=2000 (SSoT constants) thay literal `[:500]`;
  mỗi chunk capture thêm field `truncated: bool` — verdict KHÔNG được phép dựa
  trên chunk bị cắt mà không biết.
- WHY (evidence): 4 án oan đã xác nhận (315/35ZR20, 285/45ZR21, 235/65R16C, C-1
  leg-2 235/40) — row đúng ĐÃ được serve nhưng nằm sau alias-megacell >500 chars
  → grader mù → kết án sai_bia. Docstring của capture hứa "grader sees exactly
  what the LLM saw" — 500 chars phá vỡ lời hứa đó.
- RED→GREEN: test_trace_harness_repeat.py 9/9 pass (pin mới: cap + truncated flag).
- Blast-radius: chỉ scripts/rag_trace_capture.py (eval harness); zero src/ path.
- Residual (queued): re-grade audit các verdict sai_bia/lech cũ bằng capture
  không-cắt — chạy cùng Step 7 re-run.

## Step 10 (002-E) — INGEST: continuation-merge cho pipe-row bị bẻ gãy (2026-07-06)
- Change (ONE): `_merge_wrapped_pipe_rows` trong shared/document_stats.py — cell
  chứa newline (converter giữ nguyên từ sheet) làm 1 dòng bảng thành 3 dòng vật
  lý, dòng 1 đứt TRƯỚC cột giá → entity mint không giá. Pre-pass nối fragment
  về dòng pipe thiếu cột (đếm pipe so với header đầu tiên), wire ngay sau
  `_premerge_split_headers`.
- WHY (đo được): 2/173 giá nguồn mất ở chinh-sach-xe — `2-R16 265/70 LPD` (SP
  BRANDA 265/70R16 112H⏎SAMPLETRAXX H/T → 1.944.000 rơi) + `235/65R16C`
  (1.872.000 rơi) = đúng bug UI user report ("tìm ra được hơn 5 data mà chỉ trả
  lời 1 dòng" — variant thiếu giá bị serve-filter Step 2 loại).
- RED→GREEN: test_stats_extract_noise.py 7/7 (fixture THẬT neutralized shape
  265/70: price_primary==1944000 + quantity=="12" hồi sinh; control normal-row
  không đổi); regression 825 pass / 21 skip / 0 fail.
- Blast-radius: ingest-time parse mọi bot (pre-pass chỉ kích hoạt khi dòng pipe
  thiếu cột so với header — bảng lành lặn không đổi); serve-time không đổi;
  pin = test_stats_extract_noise.py.
- Kích hoạt data: cần re-ingest chinh-sach-xe (wipe+apply) — corpus stamp SẼ ĐỔI,
  mọi so sánh pinned sau đó phải ghi stamp mới.
- Rollback: revert commit (pre-pass additive).
- RE-INGEST ĐO ĐƯỢC (2026-07-06): lần 1 KHÔNG hồi sinh — root cause: ragbot-py.service
  start 07-04 02:08:11 < mtime document_stats.py 02:13:42 → worker chạy code cũ
  (bài học: ingest-fix PHẢI restart service trước khi đo). Restart → re-ingest lần 2:
  `2-R16 265/70 LPD` price NULL→**1.944.000**, qty=**12** ✓; priced 172→**173**;
  242 entities / 403 chunks / 403 embedded / 4 docs active.
  Nguồn giờ đủ 173/173 giá (235/65R16C 1.872.000 đã có từ re-extract Step 1-3).
  69 price-less còn lại = hợp lệ: 55 xe-2 (bảng lịch-về không có cột giá) +
  14 xe-3 (ô giá trống trong source — shell thật, Step-2 filter xử lý đúng).
- Probe closure N=10 (step10_e_probe_n10.json, corpus stamp mới
  md5=6e6c0774…f34f3 / 403 chunks / 2026-07-06 08:54): **E-01 10/10** run liệt
  kê ĐỦ 2 variant + 2 giá (1.944.000 H/T còn 12 + 2.133.000 A/T còn 22 — khớp
  DB từng số) · E-02 giá H/T hồi sinh 10/10 · E-03 control 10/10 · numeric-
  fidelity 0 unsupported/30 run. → Bug UI gốc ("tìm ra 5+ data mà trả lời 1
  dòng") **CLOSED — VERIFIED** (không còn ở mức giả thuyết).
