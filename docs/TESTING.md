# Testing — RAGbot

> Test discipline: 1206 tests passing — never decrease without explicit approval.
> New feature = new tests. No `assert True` / `assert is not None` only — real assertions on values/behavior.

---

## Quick run

```bash
# All tests (1206 collected)
pytest -q

# Unit only (fast, no IO)
pytest tests/unit/ -q

# Integration (requires Docker Postgres + Redis)
pytest tests/integration/ -q

# With coverage
pytest --cov=ragbot --cov-report=term-missing
```

---

## Test suite breakdown

| Suite | Count | Notes |
| :--- | ---: | :--- |
| Unit | 774 | Domain + application, no external IO |
| Integration | 218 | Real Postgres + Redis (Docker required) |
| RBAC red-team | 117 | Role matrix: all 7 levels × 35 routes |
| Tenant-scope red-team | 11 | Cross-tenant isolation checks |
| Skipped | 3 | Intentional (optional deps) |
| **Active total** | **1 206** | |

Source: Sprint 11B final run. See [`STATE_SNAPSHOT.md`](../STATE_SNAPSHOT.md) for latest count.

---

## Specific test targets

```bash
# RBAC red-team matrix (Sprint 11B — 117 tests)
pytest tests/integration/test_rbac_*.py -v

# Tenant-scope isolation (cross-tenant red-team)
pytest tests/integration/test_tenant_scope_*.py \
       tests/integration/test_cross_tenant.py -v

# DeepEval smoke (5q opt-in — needs OPENAI_API_KEY for judge)
DEEPEVAL_SMOKE=1 pytest tests/integration/test_deepeval_smoke.py -v

# Zero-hardcode regression (P21)
pytest tests/unit/test_p21_regression.py -v

# Chunking path
pytest tests/unit/test_chunking*.py -v

# Cache + circuit breaker
pytest tests/unit/test_semantic_cache*.py \
       tests/unit/test_circuit_breaker*.py -v
```

---

## DeepEval 100q full run

Measures Faithfulness, Answer Relevancy, Contextual Precision, Contextual Recall against a golden set.

```bash
# Full run (100 questions — costs ~$0.50 in LLM calls)
DEEPEVAL_SMOKE=0 python scripts/deepeval_runner.py \
  --tenant-id 1 \
  --bot-id "<demo-bot-slug>" \
  --channel-type web \
  --golden-set golden_set/golden_questions_v2.json \
  --output reports/deepeval_run_$(date +%Y%m%d_%H%M%S).json
```

Latest result: [`reports/deepeval_run_20260428_204417.json`](../reports/deepeval_run_20260428_204417.json)

| Metric | Mean | Pass-rate | Threshold |
| :--- | ---: | ---: | ---: |
| Faithfulness | **0.985** | **97.0%** | ≥ 0.85 |
| Answer Relevancy | 0.451 | 27.3% | ≥ 0.80 |
| Contextual Precision | 0.651 | 60.6% | ≥ 0.75 |
| Contextual Recall | 0.561 | 54.5% | ≥ 0.75 |

Note: low Answer Relevancy reflects correct refusals on out-of-corpus questions (F2 STRICT docs-only rule), not bot errors.

---

## Load test

```bash
# Run all 4 scenarios (smoke / sustained / burst / stream)
export RAGBOT_LOAD_TENANT_ID=1
export RAGBOT_LOAD_BOT_ID=<demo-bot-slug>
export RAGBOT_LOAD_CHANNEL=web

./scripts/load_test/run_load_test.sh all
# Results in reports/load_test/*.{csv,html}

# Parse results to markdown
python scripts/load_test/parse_results.py
```

### Latest results (post-v1.x)

Source: [`reports/LOAD_TEST_v1x_FINAL_20260429.md`](../reports/LOAD_TEST_v1x_FINAL_20260429.md)

| Scenario | P50 | P95 | RPS | Errors |
| :--- | ---: | ---: | ---: | ---: |
| Smoke (1u/60s) | 9800ms (cold) | — | 0.10 | 0% |
| Sustained (5u/3m) | **310ms** | 14000ms | 1.01 | 0% |
| Burst (15u/2m) | **1600ms** | **2600ms** | **4.09** | 0% |
| Stream TTFT (5u/90s) | 240ms | **620ms** | 2.10 | 0% |

Bottleneck: single-worker CPU saturates at ~88-91% during burst. Fix: `--workers 4` (Sprint 13 P0).

---

## 340-turn LLM judge harness

Separate harness measuring grounded/refused/hallucinated at scale:

```bash
python scripts/eval_harness.py \
  --tenant-id 1 --bot-id "<demo-bot-slug>" --channel-type web \
  --turns 340 --output reports/harness_run_$(date +%Y%m%d).json
```

Sprint 7+8 baseline (source: [`reports/sprint8_final_analysis.md`](../reports/sprint8_final_analysis.md)):

| Gate | Target | Result | Status |
| :--- | ---: | ---: | :---: |
| Answered | ≥95% | 100% | ✅ |
| Hallucinated | ≤10% | 6.8% | ✅ |
| Grounded | ≥80% | **80.3%** | ✅ |
| Equiv (semantic match) | ≥80% | 78.8% | ❌ gap 1.2pp |
| Halluc-diff | ≤5% | 5.0% | ✅ |

---

## Pre-commit self-verification

Before committing hot-path code (`src/ragbot/**`), run:

```bash
# 1. Magic numbers in staged code (outside constants.py)
git diff --cached --name-only | grep '\.py$' | grep -v 'constants.py' \
  | xargs grep -nE '\b(1024|256|500|1000|2000|3000|4000|5000|8000|450|300|60|30)\b' 2>/dev/null

# 2. Hardcoded AI model names (outside constants.py / settings.py)
git diff --cached --name-only | grep '\.py$' | grep -v 'constants\|settings' \
  | xargs grep -nE '"gpt-\d|"claude-\d|"text-embedding-|"cohere/' 2>/dev/null
```

Any hit → STOP, move literal to `shared/constants.py`, re-stage.
