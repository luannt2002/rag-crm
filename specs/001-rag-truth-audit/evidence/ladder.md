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
