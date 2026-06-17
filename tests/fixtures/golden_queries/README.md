# Golden retrieval queries — fixtures

Per-bot JSONL files used by `scripts/eval_retrieval_hit_at_k.py` to
compute Hit@K + nDCG@K + MRR. Each filename is the bot's internal UUID
slug (`<record_bot_id>.jsonl`); the platform never inspects the slug
beyond using it as the lookup key for the retrieval runner.

Each line is one query object with at least:

```json
{"question": "<query text>", "expected_doc_ids": ["doc-uuid-a", "doc-uuid-b"]}
```

* `question` (str, non-empty) — the user-facing query.
* `expected_doc_ids` (list[str], non-empty) — the unordered set of
  documents that **must** appear in top-K to count as a hit.

Extra keys are ignored; add freely for future metrics. Domain-neutral:
do not put brand / customer / industry literal in the file — keep
fixtures synthetic so the eval framework stays portable.

See `sample_bot.jsonl` for a minimal worked example used by the unit
tests.
