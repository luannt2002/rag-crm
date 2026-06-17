# Vertical-Agnostic Golden Test Harness (WS-4)

Drive the same ragbot platform through multiple industry verticals
(finance, healthcare, retail, spa, ...) without changing a single line
of harness code. Adding a new vertical is purely a YAML edit.

## Layout

```
tests/eval/
- README.md                      # this file
- constants.py                   # default floor thresholds (test-only)
- golden_runner.py               # GoldenTestRunner class (vertical-agnostic)
- test_golden_harness_smoke.py   # in-process mock-transport smoke tests
- fixtures/
  - <vertical>/questions.yaml    # one folder per vertical
  - spa/questions.yaml           # current vertical (15 VN questions)
  - finance/questions.yaml       # second vertical (15 EN questions)
```

## Add a new vertical (3 steps)

1. **Copy a fixture template**:

   ```bash
   cp -r tests/eval/fixtures/finance tests/eval/fixtures/<new-vertical>
   ```

2. **Fill `questions.yaml`** — change `vertical:` and `language:`, then
   replace each `text:` / `expected_keywords_any:` / `must_not_contain:`
   with the new vertical's questions and rubric. Use the `<brand>`
   placeholder for any tenant-specific name. Recommended: 15 questions
   covering factoid, greeting, out-of-scope, and clarification intents.

3. **Run the smoke harness**:

   ```bash
   pytest tests/eval/test_golden_harness_smoke.py -q
   ```

   This validates that the new fixture parses and the runner class loads
   it without modification. (It does NOT call a live ragbot deployment;
   for that, see CI section below.)

To run the harness against a live ragbot deployment programmatically:

```python
from pathlib import Path
from tests.eval.golden_runner import GoldenTestRunner

runner = GoldenTestRunner(
    vertical="<new-vertical>",
    fixtures_dir=Path("tests/eval/fixtures"),
    bot_3key=(tenant_id_int, "<bot-slug>", "<channel>"),
    base_url="http://localhost:3004",
)
result = runner.assert_meets_floor()  # raises EvalFloorViolation on shortfall
```

## CI invocation

A live nightly job per vertical is proposed in
`reports/MEGA_GOLDEN_TEST_CI_HOOK_20260430.md`. In short:

- Job target: `golden-vertical-${VERTICAL}` (matrix entry per vertical)
- Required env vars: `LOADTEST_BOT_ID`, `LOADTEST_TENANT_ID`,
  `LOADTEST_CHANNEL_TYPE`, `BASE_URL`, `VERTICAL`
- Timeout: 15 minutes per vertical (60 s/request times 15 questions
  with safety factor)
- Artifact: `golden_run_<vertical>_<sha>.json` containing aggregate
  metrics + per-question detail
- Failure surface: PR comment with floor-delta when
  `EvalFloorViolation` raises

## Floor numbers

Defaults live in `tests/eval/constants.py`. A fixture's `floor:` block
overrides them per vertical.

| Floor               | Default | Meaning                                                   |
| ------------------- | ------- | --------------------------------------------------------- |
| `pass_rate`         | 0.60    | Fraction of questions whose answer met the rubric.        |
| `faithfulness`      | 0.85    | Mean grounding score from the chat endpoint.              |
| `top_score`         | 0.45    | Mean of the best retrieval score across questions.        |
| `p95_ms`            | 8000    | p95 end-to-end latency (milliseconds).                    |
| `hallu`             | 0       | Max questions that contained a banned term (`must_not`).  |

## Per-question rubric

Each fixture entry has:

```yaml
- intent: factoid_in_corpus      # opaque label (greeting, out_of_scope, etc.)
  text: "..."                    # the user message sent to the chat endpoint
  expected_keywords_any: ["a"]   # answer must contain at least one
  must_not_contain: ["secret"]   # answer must NOT contain any (= hallucination)
```

`expected_keywords_any` empty means "any answer passes the keyword
gate". `must_not_contain` empty means "no banned terms".

## Domain-neutral guarantee

The harness code (`golden_runner.py`, `constants.py`) contains zero
mention of any specific industry. The `vertical` argument is a free-form
string used only to locate the fixture file. Two pre-commit greps
enforce this — see `plans/260430-ROADMAP_V2` for the full rule.
