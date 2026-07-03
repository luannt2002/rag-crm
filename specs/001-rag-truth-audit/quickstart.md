# Quickstart — RAG Truth Audit validation runs

Prerequisites: server up on `:3004`, `.env` sourced (`set -a && source .env && set +a`),
`RAGBOT_LOADTEST_BYPASS_TOKEN` set (operator loopback), bot `chinh-sach-xe` ingested.

## 1. Reproduce the pinned 60-question baseline (already committed)

```bash
.venv/bin/python scripts/rag_trace_capture.py \
  --scenario tests/scenarios/chinh-sach-xe_deepdive60.json \
  --out reports/rag_trace_60_rerun.json --concurrency 6
```

Expected: 60 records; DB-anchored grading (see
`reports/DEEPDIVE_60Q_chinh-sach-xe_20260703.md`) baseline = 42 chuẩn / 6 HALLU /
9 coverage losses. Any rerun compares against this table.

## 2. Statistical baseline (Phase B — after probe9 + --repeat land)

```bash
.venv/bin/python scripts/rag_trace_capture.py \
  --scenario tests/scenarios/chinh-sach-xe_probe9.json \
  --out specs/001-rag-truth-audit/evidence/baseline_runs.json \
  --repeat 15 --concurrency 6
echo $?   # 0 = valid batch; 2 = cache contamination; 3 = corpus changed mid-batch
```

Expected outcomes: per-probe fabrication rate table; fabricated-value distribution;
stray-number verdict per thresholds in `data-model.md`. Controls (A-q13/A-q18/A-q22 chuẩn;
H-04-type pure-gap) must show fabrication_rate=0 and refuse/defer respectively — if not,
the harness (not the bot) is suspect: stop and investigate.

## 3. Read the truth table

`specs/001-rag-truth-audit/evidence/truth_table.json` — every row has ≥1 evidence link;
open the linked artifact to reproduce the grade (User Story 1 acceptance).

## 4. Numeric-fidelity observe metrics (Phase D — after gate lands)

Run step 1, then aggregate `debug.numeric_fidelity` across records:
false-positive rate = unsupported>0 on answers graded chuẩn; catch rate = unsupported>0 on
answers graded sai_bia/lệch. Both go to the owner gate before any blocking discussion.

## 5. Ladder re-run (Phase E — per remediation step)

Same as step 2 with `--out specs/001-rag-truth-audit/evidence/step_<n>_runs.json`; the
delta table vs the previous step goes in `evidence/ladder.md`. One change per step;
HALLU>0 or chuẩn regression beyond declared tolerance → execute the step's pre-declared
rollback.

## 6. GP-100 release gate (luồng check chi tiết)

```bash
# 1 lần chạy = 100 câu; release gate = --repeat 3 (300 answers)
.venv/bin/python scripts/rag_trace_capture.py \
  --scenario tests/scenarios/gate100.json \
  --out specs/001-rag-truth-audit/evidence/gp100_run.json \
  --repeat 3 --concurrency 5
```

Chấm (DB-anchored, không đoán):
1. `numeric_fidelity` per-record (đã persist trong trace): `n_unsupported>0` = BỊA → FAIL cứng.
2. `expect` substring: câu có expect phải chứa đúng giá trị (giá đã certified khớp file gốc).
3. Trap (`trap_*`): answer KHÔNG được chứa số giá; phải defer → không thì FAIL.
4. `multi_variant_listing`: expect = giá variant RẺ (dòng hay bị bỏ sót) — thiếu = coverage-loss.
5. PASS bar (owner 2026-07-03): 0 sai + 0 bịa; 'thiếu' allowed, đếm riêng.

Verify từng turn TRONG UI (không cần debug mode): mở lịch sử chat —
mỗi câu trả lời có khối "📌 Chunks đã đưa qua LLM (N)" đọc từ
`chat_histories.served_chunks` (alembic `served_chunks_260703`).
