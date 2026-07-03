# Contract — Repeated-Run Harness CLI

Extension of `scripts/rag_trace_capture.py` (existing single-pass contract unchanged —
backward compatible).

## Invocation

```bash
.venv/bin/python scripts/rag_trace_capture.py \
  --scenario tests/scenarios/chinh-sach-xe_probe9.json \
  --out specs/001-rag-truth-audit/evidence/baseline_runs.json \
  --repeat 15 \
  --concurrency 6
```

## New arguments

| Arg | Type | Default | Semantics |
|---|---|---|---|
| `--repeat N` | int | 1 | Run every scenario question N times; `connect_id` = `trace-{qid}-r{i}` (unique per iteration — no session reuse) |
| `--concurrency` | int | 6 | Existing; semaphore across ALL (question × iteration) tasks |

## Behavioral requirements

1. **Cache-bypass assertion**: every response's debug cache status MUST equal `bypassed`;
   any other value → abort batch, exit code 2, partial output NOT written (research D3).
2. **Corpus-version stamp**: computed from DB at batch start AND end
   (`count(chunks), max(documents.updated_at), md5(agg(content_hash))` for the bot);
   mismatch → abort, exit code 3 (research D4).
3. **Output**: top-level `{"corpus_version": {...}, "repeat": N, "records": [RunRecord...]}`
   — RunRecord schema per `../data-model.md`. Number extraction + grounded/derived/
   unsupported verdicts computed in-harness against served chunks + stats DB
   (`document_service_index` for the bot), using `shared/number_format.py` parsing (D1/D2).
4. **Determinism of the harness itself**: given the same stored responses, re-grading is
   pure (no LLM call in the verdict path).
5. Exit code 0 only when all runs completed with valid cache + stable corpus version.
