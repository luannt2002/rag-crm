# DIAGNOSTIC_GUIDE — p95 latency + cache_check root-cause

> Operator + dev guide for `scripts/diagnose_p95_bottleneck.py`.
> Read-only diagnostic — safe against prod replica.

This script answers "21s p95 đang ở node nào?" with **evidence from
`request_steps` + `request_logs` + `model_invocations` + `semantic_cache`**,
not guesses. It also surfaces the diagnostic facts behind Bug #3
(cache_check ≈ 1.21s p95 with 100 % miss rate).

---

## 1 — Quick start

```bash
# from repo root
set -a && source .env && set +a
python scripts/diagnose_p95_bottleneck.py --hours 24
```

That prints 8 sections:

1. End-to-end (`request_logs` n / avg / p50 / p95 / p99 / max + success / refused / failed counts)
2. Per-step latency (sorted by p95 desc)
3. Per-bot p95 (cross-bot breakdown)
4. Grade retry distribution
5. LLM calls per turn
6. Bots opt-in for Reflect / async-grounding
7. Dead-path flags (`metadata_extraction_enabled` …)
8. **Semantic_cache diagnostic** (indexes / config / size — answers Bug #3)

Filter to a single bot slug:

```bash
python scripts/diagnose_p95_bottleneck.py --hours 168 --bot some-bot-slug
```

---

## 2 — Window selection — sample size vs trend stability

| `--hours` | Coverage | Use when |
|---|---|---|
| `24` (default) | last day | "Did the last deploy regress p95?" — fresh signal, but small n on low-traffic bots |
| `168` (7d) | last week | Weekly health review — smooths spikes, surfaces weekday cycle |
| `720` (30d) | last month | Baseline / capacity planning — large n; trend stable; misses recent regressions |

**Rule of thumb**: if a bot has fewer than 100 requests in the chosen window,
its p95 is noisy. Either widen the window or scope to the busier bot.

`--top` (default `30`) — number of `step_name` rows to print. Increase when
you suspect a rare step is contributing tail latency.

`--top-bots` (default `10`) — number of bot rows in the per-bot section.

---

## 3 — Per-step latency interpretation

Read the per-step table from the **highest p95 down**. Common patterns:

- **`generate` p95 > 5 s** → LLM model bottleneck. Check the model tier
  bound to the bot (`bot_model_bindings.purpose='generate'`), provider
  status, and `model_invocations.duration_ms` for that step.
- **`retrieve` / multi-query expand p95 > 4 s** → embedding LLM slow,
  multi-query fan-out unbounded, or vector store slow.
  Cross-check with `model_invocations` filtered to embedding model.
- **`grade` p50 = 0 ms but p95 > 2 s** → CRAG grader is short-circuiting
  for most turns (top_score gate working) but a long tail still retries.
  Look at the **Grade retry distribution** section: high `retries=2/3`
  count = wasted LLM calls.
- **`cache_check` p95 > 1 s with miss-rate 100 %** → Bug #3 path active.
  Jump to section 4 below.
- **`refusal_check` / `output_guardrail` p95 > 500 ms** → guardrail regex
  panel may be running an N-gram shingle scan over very long answers;
  size by `chars` not regex count.

The `err` column at the right edge counts non-`success` rows for that step
in the window. Non-zero is a flag — pair it with an `audit_log` search
(`step_name + error_type`) for the real error class.

---

## 4 — Cache_check Bug #3 diagnostic

The `semantic_cache` section answers three orthogonal questions:

| Sub-output | What it shows | Diagnostic value |
|---|---|---|
| `semantic_cache_index_query` | `pg_indexes` rows on `semantic_cache` | Missing HNSW on the embedding column = seqscan over the whole table |
| `semantic_cache_config_query` | `system_config` keys matching `semantic_cache%` / `cache_similarity_threshold%` / `cache_ttl%` | Confirm the **active** threshold + TTL **before** chasing index issues |
| `semantic_cache_size_query` | row-count + active / expired / oldest / newest | A bloated table (millions of rows, no LRU eviction) amplifies every miss |

The script flags a missing HNSW on `embedding` automatically:

```
Indexes:
  semantic_cache_pkey                      CREATE UNIQUE INDEX … (id)
  ix_semantic_cache_record_bot_id          CREATE INDEX … (record_bot_id)
  ← MISSING: no HNSW index on embedding column. Add migration:
    CREATE INDEX … USING hnsw (query_embedding vector_cosine_ops)
```

The three diagnostic causes — listed in order of frequency:

1. **HNSW missing on `query_embedding`** → cosine-distance lookup is a
   seqscan. Latency grows linearly with row count.
2. **`cache_similarity_threshold` too low** (< 0.7) → many candidate rows
   pass the cosine prefilter, post-filter does heavy work.
3. **Bloated table** (no LRU eviction) — expired rows still cost scan I/O.

Sample real output from a worktree against the prod replica (30-day window):

```
END-TO-END (request_logs)
  n=17  success=11  refused=4  failed=2
  avg=14.32s  p50=15.10s  p95=16.94s  p99=17.20s  max=17.30s

PER-STEP LATENCY (request_steps, sorted p95 desc)
  step_name                              n      avg     p50     p95     p99     max  err
  ----------------------------------------------------------------------------
  generate                              17    8.91s   8.40s  12.10s  12.80s  13.00s    0
  retrieve                              17    3.40s   3.20s   4.50s   4.80s   4.90s    0
  cache_check                           17  840ms    790ms   1.21s   1.30s   1.32s    0
  grade                                 14   210ms   190ms     2.10s    2.30s    2.40s    0
```

See `reports/RAGBOT_REALITY_CHECK_AND_TIER_PLAN.md` section A for the
fuller version of this baseline (n=17, p95=16.94s, 30-day window).

---

## 5 — JSON output mode (CI gates)

```bash
python scripts/diagnose_p95_bottleneck.py \
    --hours 168 \
    --json-out reports/baseline_$(date +%Y%m%d_%H%M%S).json
```

The JSON is a `DiagReport` dataclass dump containing **all eight sections**
plus the generation timestamp + filter parameters. Use cases:

- Snapshot baseline before / after a deploy and diff p95 deltas.
- Feed a CI gate that fails if any `step_name` regresses p95 by more than
  a fixed budget.
- Capture historical baselines into `reports/` for later post-mortems.

The helper `scripts/perf_baseline.sh` wraps this with a fixed 168 h window
and timestamped path — see that file for the canonical baseline command.

---

## 6 — Sample outputs

```
Ragbot p95 Diagnostic — generated_at=2026-05-18T13:24:11+00:00
Window: last 720h  bot_filter=*

==============================================================================
  END-TO-END (request_logs)
==============================================================================
  n=17  success=11  refused=4  failed=2
  avg=14.32s  p50=15.10s  p95=16.94s  p99=17.20s  max=17.30s
```

(See section 4 for the per-step + cache_check sample, and
`reports/RAGBOT_REALITY_CHECK_AND_TIER_PLAN.md` section A for the full
30-day baseline this output is taken from.)

---

## 7 — Troubleshooting

| Exit code | Meaning | Fix |
|---|---|---|
| `0` | Success | — |
| `2` | DB unreachable | Confirm `DATABASE_URL` is exported. `set -a && source .env && set +a` from repo root |
| `3` | No rows in window | Widen `--hours` or check that `request_steps` instrumentation is enabled |

**Async URL handling**

The script accepts `postgresql+asyncpg://…` and `postgresql+psycopg://…`
DSNs — the dialect / driver suffix is stripped automatically before
opening the `psycopg2` connection. No manual rewriting required.

**Permission / readonly**

The script sets `readonly=True` + `autocommit=True` on the session. It
will never write. Safe against a prod read-replica or the primary.

**Missing `bots.plan_limits` column on dev schemas**

Older dev schemas may not have `plan_limits` JSONB. The script catches
the SQL error per-section and prints an empty list for that section —
the rest of the output is unaffected.
