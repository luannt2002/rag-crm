# RAGAS Metrics Runbook

Granular RAG quality measurement on top of binary HALLU + answer-rate
labels. Surfaces *where* a turn weakens (high faithfulness but low
context_precision = retrieval-side fix; the inverse = generation-side
fix).

This runbook documents the **scaffold**: a deterministic stub adapter and
a CLI gate. The live `ragas` package is a deferred dependency owned by
the platform admin (see "Wire the real RAGAS provider" below).

## Scope

- Dev tool, NOT chat hot path. Never invoked from `query_graph` or
  `chat_worker`.
- Offline only. No live LLM call from the adapter today.
- Mock-only tests; no network in CI.

## Files

- `src/ragbot/application/services/ragas_metric_adapter.py` — Port-style
  adapter (`RagasMetricPort`) with deterministic `RagasMetricAdapter`
  stub. Real provider plugs in via Strategy + Registry.
- `scripts/eval_ragas_metrics.py` — argparse CLI: reads questions /
  answers JSONL + corpus dir, prints a markdown summary table, exits 1
  when any metric falls below its threshold.
- `src/ragbot/shared/constants.py` — `DEFAULT_RAGAS_MIN_*` thresholds and
  `DEFAULT_RAGAS_STUB_SCORE` placeholder.

## Run the CLI

```bash
PYTHONPATH=src python scripts/eval_ragas_metrics.py \
    --questions data/questions.jsonl \
    --answers data/answers.jsonl \
    --corpus  data/corpus/ \
    --min-faithfulness 0.8 \
    --min-answer-relevancy 0.7 \
    --min-context-precision 0.7 \
    --min-context-recall 0.7
```

### Input format

`questions.jsonl` — one JSON object per line:

```json
{"id": "q1", "question": "How do I reset the password?"}
```

`answers.jsonl` — one JSON object per line:

```json
{"id": "q1", "answer": "Click 'forgot password'.", "contexts": ["..."]}
```

If `contexts` is omitted, the CLI looks for `<corpus>/<id>.txt` and uses
its full content as the single context. When both are absent, contexts
is empty and faithfulness is forced to `0.0` (an unground-able claim).

### Output

Markdown table on stdout, structured failure list on stderr:

```text
| metric | mean | n |
|---|---|---|
| faithfulness | 0.0000 | 1 |
| answer_relevancy | 0.0000 | 1 |
| context_precision | 0.0000 | 1 |
| context_recall | 0.0000 | 1 |
FAIL: metrics below threshold: faithfulness, answer_relevancy, ...
```

Exit code:
- `0` — every metric meets its threshold.
- `1` — at least one metric below threshold OR I/O error.

## Wire the real RAGAS provider

When admin approves the dependency cost:

1. Add `ragas` (and any LLM-call back-end it requires) to project
   dependencies. Admin owns prod dep manifest changes.
2. Implement `RagasLiveProvider` under
   `src/ragbot/infrastructure/ragas/<provider>.py` conforming to
   `RagasMetricPort`. Each call must produce the four
   `EXPECTED_METRIC_KEYS` clamped to `[0.0, 1.0]`. Empty contexts MUST
   still collapse `faithfulness` to `0.0`.
3. Add the registry mapping in
   `src/ragbot/infrastructure/ragas/registry.py` (Strategy + Registry
   pattern, mirroring `infrastructure/reranker/registry.py`).
4. Inject via `bootstrap.py` based on a `system_config` provider key —
   no `if provider == "..."` in business logic.
5. Switch the CLI `--adapter <name>` (add the flag at that point) to
   pick up the live provider while the stub remains the default.

Reference docs: `https://docs.ragas.io/`.

## Sacred constraints

- Application MUST NOT inject text or templates into the LLM prompt
  (CLAUDE.md Quality Gate #10).
- Application MUST NOT override LLM answers; metrics are read-only.
- Tenant isolation: when wiring the live provider, scope every input
  via the calling tenant's bot — never run cross-tenant aggregation.
- Cost: the live provider runs LLM calls per metric per turn. Treat as
  expensive batch work; gate behind admin approval and explicit run
  flag, never on the chat hot path.
