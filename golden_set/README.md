# `golden_set/` — per-bot golden Q&A pairs (CI regression gate)

Bot owners check in golden Q&A pairs here. CI runs every pull request via
[`.github/workflows/per-bot-golden.yml`](../.github/workflows/per-bot-golden.yml),
replays each bot's questions through
[`scripts/eval_per_bot_golden.py`](../scripts/eval_per_bot_golden.py), and
fails the merge if any bot's pass rate drops below its baseline.

> Domain-neutral mandate: the runner knows nothing about industry / brand.
> Examples below use `<bot-id>` and `<expected fragment>` placeholders;
> never paste real customer brand names into examples in this file.

## Layout

```
golden_set/
- README.md                       # this file
- <record_bot_id>.jsonl           # one JSONL per bot, named by record_bot_id UUID
- baseline.jsonl                  # prior pass rates, one line per bot
```

`<record_bot_id>` is the internal UUID PK of the `bots` row, resolved from
the mandatory 4-key identity tuple `(record_tenant_id, workspace_id,
bot_id, channel_type)`. One file per bot keeps tenants isolated; never
mix two bots into one file.

## Per-question schema

Each line of `<record_bot_id>.jsonl` is a JSON object:

```json
{"question": "<user message text>", "expected_answer": "<expected fragment>", "expected_intent": "<intent label>", "must_cite": true}
```

| Field             | Type    | Required | Meaning                                                                       |
| ----------------- | ------- | -------- | ----------------------------------------------------------------------------- |
| `question`        | string  | yes      | The user message replayed against the bot.                                    |
| `expected_answer` | string  | optional | Substring that must appear in the bot's answer (case-insensitive). Empty string = skip the keyword check. |
| `expected_intent` | string  | optional | Intent label the pipeline should classify; matched case-insensitive. Empty string = skip the intent check. |
| `must_cite`       | bool    | optional | When `true`, the response must carry at least one non-empty citation.         |

Pass rule (all conditions that are present must hold): keyword substring
present, intent matches, citation present when required. A line with
none of the optional fields set is a smoke test: the bot just needs to
return a non-error response.

## `baseline.jsonl` schema

```json
{"record_bot_id": "<bot-id>", "baseline_pass_rate": 0.85}
```

| Field                | Type   | Meaning                                              |
| -------------------- | ------ | ---------------------------------------------------- |
| `record_bot_id`      | string | UUID of the bot — must match the JSONL filename stem.|
| `baseline_pass_rate` | float  | Pass rate threshold; CI fails when current drops below `baseline - tolerance`. |

CI tolerance defaults to `0.0` (strict). Operators may relax via
`--tolerance 0.02` on the eval CLI when noise is expected.

## Add a new bot's golden set (3 steps)

1. Look up the bot's `record_bot_id` UUID (e.g. via the admin console
   or `BotRegistryService.lookup`).

2. Create `golden_set/<record_bot_id>.jsonl` with one Q&A per line. A
   minimum-viable starter (5 lines) catches obvious regressions; teams
   typically grow to 15–30 questions covering factoid, greeting,
   out-of-scope, and clarification intents.

   ```jsonl
   {"question": "<sample question>", "expected_answer": "<expected fragment>", "expected_intent": "factoid", "must_cite": true}
   {"question": "hello", "expected_intent": "greeting", "must_cite": false}
   ```

3. Append the bot's baseline entry to `golden_set/baseline.jsonl`:

   ```json
   {"record_bot_id": "<bot-id>", "baseline_pass_rate": 0.80}
   ```

   Pick the baseline by running the eval against the current production
   build first (record what the bot does today, then gate future PRs
   against that number).

## Update an existing baseline

After a deliberate quality lift, run the eval against the new build,
record the higher pass rate, and bump the matching `baseline.jsonl`
line. Lowering a baseline requires a written justification in the PR
body — the whole point of the gate is to ratchet upward.

## Local invocation

```bash
PYTHONPATH=src python scripts/eval_per_bot_golden.py \
  --golden-dir golden_set \
  --baseline golden_set/baseline.jsonl
```

Without `--baseline` the script still runs every Q&A and logs pass rates
but skips the regression check (useful for one-shot smoke tests). The
script's default bot runner is a stub that raises — CI plugs in an
HTTP-backed runner; local dev plugs in a runner pointing at
`http://localhost:3004`.
