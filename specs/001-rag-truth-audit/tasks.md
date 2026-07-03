# Tasks: RAG Truth Audit — phased stage-hardening + deepest test flows

**Input**: Design documents from `/specs/001-rag-truth-audit/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (all present)

**Tests**: MANDATORY per constitution P-VI — every code phase opens with RED tests
reproducing the real bug (fixture = real DB rows captured in evidence/), then the fix, then
the pinned re-run with deltas. One toggle per ladder step.

**QA BAR (owner mandate, 2026-07-03)** — encoded as the program's release gate:
- Gate set = 100 questions (GP-100). Target: **0 sai + 0 bịa (cấm tuyệt đối)**.
- **"Thiếu" / honest-refuse ("em chưa có thông tin…") ALLOWED** — counted as coverage loss,
  never as failure of the safety gate.
- Baseline hôm nay: QA 40 câu sai 10 (~30/100 điểm). Program done = GP-100 has 0 wrong/0
  fabricated, and a NEW bot onboarded with a messy sheet reproduces 0 fabrication (multi-bot
  proof).

**Strategic stance (CLAUDE.md, binding)**: EVOLVE — KHÔNG rewrite khung (Hexagonal/Port-DI đã
chuẩn). "Viết lại" = viết lại RUỘT các stage đã chứng minh hỏng (extract / reconcile /
serve / format / guard), giữ contract giữa các stage. Đập khung = lỗi nặng nhất.

## Format: `[ID] [P?] [Story] Description` — [P] = parallelizable, Story = US1–US6 (spec.md)

---

## Phase 0: Measurement Foundation (blocking — no fix ships before this)

**Purpose**: US1 + US2 — the truth table and the statistical baseline every later delta
compares against. Constitution P-III hard-orders this first.

- [ ] T001 [US2] Freeze probe set `tests/scenarios/chinh-sach-xe_probe9.json`: A-q20
      (fabrication/stray-26), H-01+H-02+H-05 (Rovelo conflation), H-03+H-04 (pure-gap
      controls), A-q13+A-q18+A-q22 (chuẩn controls). Schema per data-model.md ProbeQuestion.
- [ ] T002 [US2] Extend `scripts/rag_trace_capture.py` per `contracts/harness-cli.md`:
      `--repeat N` (unique connect_id per iteration), per-run cache-status assertion
      (abort exit 2), corpus-version stamp start+end (abort exit 3), in-harness number
      extraction + grounded/derived/unsupported verdicts vs served chunks + stats DB.
      **Tests (write FIRST)**: `tests/unit/test_trace_harness_repeat.py` — (a) cache status
      "hit" → batch aborts, no partial file; (b) corpus drift → exit 3; (c) verdict
      classifier pure-function cases: grounded literal, grounded via parsed-value
      (1.242.000đ ↔ 1242000), derived_valid |a−b|, unsupported; (d) `--repeat 3` produces
      3 RunRecords per question with distinct connect_ids.
- [ ] T003 [US2] Run baseline: `--repeat 15` → `evidence/baseline_runs.json` + aggregate →
      per-probe fabrication rate + fabricated-value distribution + stray-number verdict
      (thresholds in data-model.md). Controls must show rate=0 / refuse — otherwise STOP,
      harness is suspect.
- [ ] T004 [P] [US1] Build `evidence/truth_table.json` (12 stages, research.md D5 method:
      trace → request_steps → code-pins). Two rows already evidenced this session:
      `cross_doc_reconcile = L1-not-L2` (dead for corpus — parse_code_query keyword "2-R15"
      < 5 digits), `stats-extract noise gate = L2-not-L3` (runs but mints 'chủ'/'đủ'/'Mép').
- [ ] T005 [P] [US1] Snapshot `evidence/shell_entities.json` (69 rows chinh-sach-xe +
      per-bot shell/garbage table for all 6 bots — the multi-bot blast-radius record).
- [ ] T006 Build GP-100 gate set `tests/scenarios/gate100.json`: pinned 60 + QA's 40 câu
      (dedupe overlap; giữ nguyên verbatim câu QA — xin file gốc từ QA). Every question
      carries expect / must_refuse ground truth. **This is the release gate set.**

**OWNER GATE 0**: baseline report + truth table reviewed; shell-entity option chosen
((b) filter khuyến nghị / (a) marker nếu cần existence-lead). Chốt xong mới sang Phase 2+.

---

## Phase 1: INGEST/EXTRACT hardening — entity quality at the source

**Purpose**: stop minting garbage + shell noise at ingest (multi-bot root: bots 111/123 =
100% shell, syllable entities). Fix here prevents every downstream layer from ever seeing
the noise. All rules schema/shape-keyed — zero brand/bot literals (P-VII).

- [ ] T010 [US5] RED tests first `tests/unit/test_stats_extract_noise.py`: fixtures = REAL
      rows captured from DB (bot 123 'chủ'/'đủ'/'tục', spa 'Mép'/'Mặt', xe 'GR' header-row
      entity) → assert extractor rejects each; plus positive controls (real catalog rows
      still extracted). Fixture data goes in the test file, anonymized shape-preserving.
- [ ] T011 [US5] Per workflow-Q1 finding (SỰ THẬT): garbage rows bots 111/123 là TÀN DƯ
      TRƯỚC-GATE (ingest 2026-06-30 08:33 UTC; `_is_noise_entity` sinh ở commit `166a14c`
      2026-07-01 — HEAD hiện tại drop 58/62). Task: (i) **DATA** — re-extract stats index
      bots 111/123 qua `scripts/db/backfill_stats_index.py` (delete_by_document trước,
      canonical, không psql hotfix); (ii) **GATE gap A** — price-less entity với attrs RỖNG
      (has_col_n=False) đang PASS ở `document_stats.py:255-256` → reject khi không có
      value-bearing field; (iii) **GATE gap B** — pseudo-header key từ dòng prose thỏa
      has_real_label (`:262-265`) → chỉ nhận label từ header structurally-confirmed;
      (iv) `_is_prose_row` (`:865-882`): thêm ';'/no-terminator (PDF hard-wrap). KHÔNG
      thêm min-name-length ('Mép'/'Mặt' là tên ô hợp lệ có giá — proven).
- [ ] T012 [US5] Per workflow-Q4 finding (SỰ THẬT): extraction UNGATED — chạy mọi doc mọi
      mime (`ingest_stages_final.py:442`); "table" = bất kỳ line có ≥1 dấu phẩy
      (`document_stats.py:1021-1026`); `_is_prose_row` chỉ bắt dòng kết thúc `.!?…。`.
      Fix: **positive-table-evidence gate** — chỉ mint entity khi (a) header
      structurally-detected (separator-backed / `_is_header_row` + ≥2 data rows đồng dạng)
      HOẶC (b) row có ≥1 value/price cell parse được; XÓA path positional-minting
      no-header-no-price. RED test: fixture chunk 'chủ'/'đủ'/'cứu' THẬT → 0 price-less
      entities; markdown table thật → entities giữ nguyên.
- [ ] T013 [US5] Re-ingest wipe+apply bots 111/123/test-spa-id (canonical
      `init_bots_from_urls.py --wipe --apply`), then DB assertion script: garbage(≤3-char
      names)=0, per-bot entity counts recorded as golden pins in
      `evidence/post_phase1_entities.json`.
- [ ] T014 Ladder step 1 close-out: pinned-60 re-run → deltas to `evidence/ladder.md`
      (expect: no behavior change on chinh-sach-xe — its entities are not garbage-class;
      HALLU unchanged pending Phase 3). Blast-radius statement: extract path consumed by
      ALL bots at ingest-time only (no runtime read change).

---

## Phase 2: INDEX integrity — reconciler resurrection with brand guard

**Purpose**: `_reconcile_cross_doc` was built for exactly the split-sheet HALLU but is dead
(L1-not-L2) AND brand-blind if revived. Revive it CORRECTLY — this is the true multi-doc
fix (same product across docs → one complete record).

- [ ] T020 [US5] Per workflow-Q3 finding (SỰ THẬT): coverage duy nhất =
      `tests/unit/test_crossdoc_reconcile.py` 4/4 GREEN vì fixture đặt tên SIZE-FIRST
      ('235/40ZR18 …' → digkey 7 chữ số → fold chạy); corpus thật PREFIX-CODE-FIRST
      ('2-R15 185/55 RVL' → `_CODE_QUERY_RE.search` leftmost-only trả '2-R15' → 3 digits →
      chết). RED tests bổ sung vào file đó: (a) DEAD-PATH — fragment tên
      'PREFIX-CODE SIZE BRAND' cùng-brand PHẢI fold (hiện fail); (b) BRAND-GUARD —
      `2-R15 185/55 RVL` KHÔNG ĐƯỢC fold vào anchor LPD dù trùng size digits (hiện sẽ fail
      sau khi (a) fix nếu thiếu guard); (c) giữ pin: 2 priced anchors never merge.
      Fix `_spec_key`: `re.finditer` TẤT CẢ code-token (không leftmost-only) / union digits
      ≥5; thêm brand-guard = non-numeric-token overlap fragment↔anchor.
- [ ] T021 [US5] Fix `_spec_key`/matching in `src/ragbot/orchestration/query_graph.py:339`:
      digit-key derived from FULL name (not parse_code_query keyword), minimum-length guard
      kept; add brand-guard = non-numeric token-set overlap requirement between fragment
      name and anchor name/productname (shape-only, no brand literal — P-VII).
- [ ] T022 Ladder step 2: pinned-60 re-run + deltas. Blast-radius: runtime formatter path,
      all bots with stats entities; pin tests = T020 suite + existing formatter tests.

---

## Phase 3: SERVE policy — shell entities out of customer answers (owner option (b))

**Purpose**: US3 decision executed. Structural removal of the fabrication surface on the
stats path (P-V: one WHERE clause beats three patch tiers). Conditional on OWNER GATE 0
choosing (b); if (a) chosen, swap T031 for the formatter-marker task variant.

- [ ] T030 [US3] RED tests `tests/unit/test_stats_serve_value_filter.py`: repository
      customer-path queries exclude entities with no value field (price_primary AND
      price_secondary AND value-bearing attributes all absent) when
      `stats_serve_require_value=true`; admin/internal path unaffected; per-bot opt-out via
      `plan_limits` honored (resolve chain per `shared/bot_limits.py`).
- [ ] T031 [US3] Implement filter in
      `src/ragbot/infrastructure/repositories/stats_index_repository.py` (customer-facing
      query methods only) + constant `DEFAULT_STATS_SERVE_REQUIRE_VALUE` in
      `shared/constants.py` + PLAN_LIMIT_SCHEMA knob. Default per owner decision.
- [ ] T032 Ladder step 3: probe re-run N=15 (expect: H-01/H-02/H-05 conflation rate → 0 on
      stats path) + pinned-60 re-run with special attention to existence questions
      (xe-exist-01/02) — coverage delta table. Rollback criteria: existence-question chuẩn
      rate drops >2 câu → rollback + escalate to owner for option (a) hybrid.

---

## Phase 4: GUARD — deterministic numeric-fidelity (the hard gate)

**Purpose**: US4 — the LLM-independent layer covering the RAW-DOCUMENT path (q20 went
score=0.667 through document chunks; Phases 1–3 don't touch that path). Contract:
`contracts/numeric-fidelity-event.md`.

- [ ] T040 [US4] RED tests `tests/unit/test_numeric_fidelity_gate.py` (pure functions
      first): token extraction reuses `shared/number_format.py`; classification cases —
      grounded-literal, grounded-parsed-value (formatting drift), derived_valid |a−b| and
      a+b recompute, unsupported (the 26.000.000 case verbatim from evidence);
      normalization matrix (1.242.000đ / 1242000 / 1,242,000 / 1242k); min_digits guard
      excludes sizes/ordinals.
- [ ] T041 [US4] Implement observe-mode check in
      `src/ragbot/orchestration/nodes/guard_output.py`: constants (event name, caps) in
      `shared/constants.py`; structlog event + `debug.numeric_fidelity` field; **answer
      NEVER modified** (sacred #10 pin test: output state equality in observe mode).
- [ ] T042 [US4] Measure on pinned-60 + probe set: false-positive rate (unsupported>0 on
      chuẩn answers) + catch rate (unsupported>0 on sai-bịa/lệch answers) →
      `evidence/numeric_fidelity_observe.md`. **OWNER GATE**: blocking-mode discussion only
      after these numbers reviewed (FR-010: never in the same step as anything else).

---

## Phase 5: COVERAGE recovery — the 9 lost answers (US6)

**Purpose**: "thiếu" is allowed but tracked — recover what the corpus CAN answer. Same
red-test + one-step ladder discipline; HALLU must stay 0 on every re-run.

- [ ] T050 [US6] Root-cause the 5 deflect-oan (A-q10/A-q12/B-q06/B-q09/D-xe-list-02):
      per-case trace autopsy (chunks served vs answer) → named failing stage with evidence.
      GIẢ THUYẾT hiện tại: prompt-build serves list/aggregation intents a single stats row
      → LLM defers. Verify before fixing.
- [ ] T051 [US6] Root-cause 3 retrieval size-miss (A-q09 'Quang Minh' corpus-gap?, A-q27
      235/40R18, D-xe-compare-01 195/55R15) — exact-size match priority in stats lookup vs
      embedding similarity; A-q09 may be pure corpus gap → label thiếu-corpus, notify owner
      to add doc (NOT a code fix).
- [ ] T052 [US6] Fix per root-cause, one ladder step each, pinned re-run each. Rollback:
      any HALLU>0 or chuẩn regression.

---

## Phase 6: RELEASE GATE — GP-100 + multi-bot proof (the QA bar)

- [ ] T060 Run GP-100 (`tests/scenarios/gate100.json`) with `--repeat 3` (300 answers):
      **PASS = 0 sai + 0 bịa across all runs**; thiếu/refuse-honest counted + reported.
      Compare vs QA's 30/100 baseline → delta report for QA team.
- [ ] T061 **Multi-bot no-regression proof**: onboard a FRESH test bot with a deliberately
      messy sheet (empty price cells, mixed brands same size, stray date column, prose
      paragraphs) — the "new tenant" simulation. Run probe-class questions `--repeat 10`.
      PASS = 0 fabrication, 0 conflation, garbage entities 0. This answers "thêm bot mới
      không bị bug tái lại".
- [ ] T062 Cross-tenant spot-check: test-spa-id (31% shell) — 5 probe questions on its
      shell entities, expect honest-defer not fabrication.
- [ ] T063 Final: update truth_table.json grades (target: stats-extract, reconcile, serve,
      guard reach L3 with linked run evidence), STATE_SNAPSHOT.md session record, ladder.md
      complete. Grep gates: zero-hardcode, no version-ref, no bot-literal in src/ — all 0
      hits.

---

## Dependencies

```
Phase 0 (T001→T002→T003; T004,T005,T006 [P])
  └─ OWNER GATE 0 (baseline + option chọn)
      ├─ Phase 1 (T010→T011→T012→T013→T014)   ← independent of Phase 2
      ├─ Phase 2 (T020→T021→T022)              ← after Phase 1 lands (re-ingest stability)
      ├─ Phase 3 (T030→T031→T032)              ← needs owner option; independent of Phase 2
      ├─ Phase 4 (T040→T041→T042)              ← independent, may run parallel with 1–3
      │                                           but ENABLED as its own ladder step
      ├─ Phase 5 (T050,T051 [P] → T052)        ← after Phase 3 (serve policy changes context)
      └─ Phase 6 (T060→T061→T062→T063)         ← last, needs all enabled steps settled
```

Ladder invariant (P-VI): dù các phase CODE song song được, việc **ENABLE** lên môi trường
test đo lường là TUẦN TỰ từng step một, mỗi step 1 re-run 1 delta.

## Test-flow tổng (per phase — "sâu nhất, chi tiết nhất")

1. **RED**: unit test tái tạo bug bằng fixture THẬT (row DB / trace verbatim) — fail trước.
2. **GREEN**: fix tối thiểu, schema-keyed, constants SSoT.
3. **PIN**: property/invariant tests (never-merge-priced, sacred-#10 answer-untouched,
   4-key identity untouched).
4. **RE-RUN**: probe N=15 + pinned-60 (+ GP-100 ở Phase 6) — delta bảng 8 cột
   (chuẩn/sai-bịa/lệch/thiếu/refuse-đúng/refuse-oan/deflect-oan/p95).
5. **GATE**: owner đọc delta, approve enable step kế.
```
