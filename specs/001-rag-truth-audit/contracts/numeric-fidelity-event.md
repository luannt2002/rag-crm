# Contract — Numeric-Fidelity Observe Event

Emitted by the numeric-fidelity check in the output-guard stage
(`src/ragbot/orchestration/nodes/guard_output.py`). OBSERVE-ONLY in this program: the event
never modifies the answer (sacred #10 safe); blocking is a separate, gated, future step.

## structlog event

Event name: constant in `shared/constants.py` (added by the implementing task; no literal
in the node body). One event per answered request where ≥1 number was extracted.

```json
{
  "event": "<NUMERIC_FIDELITY_EVENT>",
  "record_bot_id": "c6e1fc56-...",
  "trace_id": "...",
  "n_numbers": 3,
  "n_grounded": 2,
  "n_derived_valid": 1,
  "n_unsupported": 0,
  "unsupported_tokens": [],            // capped list, tokens only — no answer text (PII-safe)
  "context_source": "stats_synthetic"  // stats_synthetic | document_chunks | mixed
}
```

## Trace field (debug=full responses)

`debug.numeric_fidelity = {"grounded": 2, "derived_valid": 1, "unsupported": 0,
"unsupported_tokens": []}` — consumed by the harness (RunRecord.verdicts cross-check).

## Classification rules (fixed by research D1/D2)

1. Extract tokens via `shared/number_format.py` significant-number regex, `min_digits`
   guard from existing constant.
2. `grounded` = literal substring of served context OR parsed-value equality with a stats
   value (`price_primary`/`price_secondary`/numeric `attributes_json`) of an entity present
   in the served context.
3. `derived_valid` = equals `|a−b|` or `a+b` for grounded values a,b in the same answer.
4. else `unsupported`.

## Non-goals (this program)

- No answer mutation, no refusal injection, no per-bot wording — observe + report only.
- No new DB table: events go to structlog; aggregation reads logs/trace JSON
  (per project memory: no premature observability infra).
