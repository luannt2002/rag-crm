# Load-test batch-10 mode

User-driven addition (2026-04-30): instead of running 75 or 150 questions
end-to-end and looking at one final aggregate, the harness can split the
round into fixed-size batches (e.g. 10 questions each) with a per-batch
checkpoint. This pinpoints which slice broke (topic cluster, latency
outliers, refuse spike) without re-running.

## Live mode — `scripts/test_75q_load.py --batch-size N`

`--batch-size 0` (default) preserves the prior single-shot behavior.
Any positive integer N enables batch mode:

```bash
.venv/bin/python scripts/test_75q_load.py \
  --bot-id "$LOADTEST_BOT_ID" \
  --tenant-id "$LOADTEST_TENANT_ID" \
  --channel-type "$LOADTEST_CHANNEL_TYPE" \
  --rooms 1,2,3,4,5 \
  --batch-size 10 \
  --output /tmp/round9_$(date +%s).json
```

Artefacts produced alongside `<output>.json`:

| File | Purpose |
| --- | --- |
| `<output>.json` | Aggregate run JSON (existing format, unchanged). |
| `<output>.batch_<NN>.json` | One per batch — turns + per-batch summary. |
| `<output>.batch_log.md` | Append-only markdown log with bucket counts + p50/p95 + worst REFUSE_NO_DOCS. |

The markdown log is the operator's `tail -F` companion during a long run.

### Per-batch summary fields

- `counts` — `{PASS, REFUSE_NO_DOCS, REFUSE_WITH_DOCS, FAIL, ERROR}`
- `top_score_avg_pass` — mean retrieval score among PASS turns
- `latency_ms_p50`, `latency_ms_p95`
- `cost_usd_total`
- `worst_refuse_no_docs` — top-3 (preview-truncated) for fast triage

## Post-hoc analyser — `scripts/loadtest_batch_analyze.py`

Re-analyse any historical aggregate JSON at batch granularity without
re-running the round. Useful for inspecting R1-R8 results.

```bash
.venv/bin/python scripts/loadtest_batch_analyze.py \
  --input /tmp/mega_round8_OLD_2026XXXXXX.json \
  --batch-size 10 \
  --output /tmp/round8_old_batch_breakdown.md
```

Omitting `--output` prints to stdout.

## Constants

Defaults live in `scripts/_loadtest_common.py`:

- `DEFAULT_LOADTEST_BATCH_SIZE = 0`
- `DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE = 3`
- `DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS = 80`

These are scoped to the test harness, NOT `shared/constants.py`, because
they never affect the production pipeline.

## App-mindset compliance

Batch mode is pure tooling:

- No injection into the LLM prompt
- No override of the LLM answer
- Refuse classification is heuristic regex on the test side only
- Domain-neutral throughout — no brand or industry literal
