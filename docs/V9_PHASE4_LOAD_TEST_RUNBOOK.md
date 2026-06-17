# V9 Phase 4 — Load Test Runbook

Audience: SRE / on-call running the deferred V9.3.4 load test once the
Jina rerank API key is healthy again.

V9 Phase 4 (V9.3.1 + V9.3.2 + V9.3.3) shipped code-only — see
`plans/260503-V9-PROD-SCALE-SPRINT/plan.md` lines 213-249. The 75-question
load-test verification was DEFERRED because the project's Jina API key
returned `403 insufficient_balance` at session time.

This runbook is the playbook for running the verification once the key
is restored or replaced.

---

## Pre-flight checklist

1. **Jina key healthy** — confirm before launching:

   ```bash
   curl -fsS -H "Authorization: Bearer $JINA_API_KEY" \
        -X POST https://api.jina.ai/v1/rerank \
        -H "Content-Type: application/json" \
        -d '{"model":"jina-reranker-v2-base-multilingual","query":"ping","documents":["test"]}' \
     | head -c 200
   ```

   Expect HTTP 200 + JSON results. Anything 4xx / `insufficient_balance` →
   STOP and rotate key first (see
   `~/.claude/projects/.../memory/project_jina_key_supply.md`).

2. **Golden questions present** — the harness expects pre-built JSON
   bundles under `tests/data/golden_questions/`:

   ```bash
   ls tests/data/golden_questions/*.json | wc -l
   ```

   At minimum: one set per round (×3 rounds). 75 questions per round.

3. **Server warmup verified** — fire the warmup probe and confirm all
   four providers report ok:

   ```bash
   curl -fsS http://localhost:8000/health/models | jq
   ```

   Confirm `{embed_ok, llm_ok, reranker_ok, tokenizer_ok}` are all
   `true`. If any is `false`, fix that provider before load-testing —
   else p99 spikes will be misattributed.

4. **Decompose-confidence gate active** — verify the new gate constant
   is loaded and not overridden per-bot:

   ```bash
   .venv/bin/python -c "
   from ragbot.shared.constants import (
       DEFAULT_DECOMPOSE_CONFIDENCE_GATE,
       DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
       DEFAULT_INTENT_CONFIDENCE_FALLBACK,
   )
   print('decompose_gate', DEFAULT_DECOMPOSE_CONFIDENCE_GATE)
   print('mq_dedup', DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD)
   print('intent_default', DEFAULT_INTENT_CONFIDENCE_FALLBACK)
   "
   ```

   Expected: `0.7`, `0.95`, `0.5`.

---

## Run the 3 rounds

```bash
# Round 1 — baseline confidence calibration
.venv/bin/python -m scripts.load_test \
   --questions tests/data/golden_questions/v9p4_round1.json \
   --out reports/v9p4_round1.json \
   --concurrency 1

# Round 2 — same payload, second pass to amortise warmup cost
.venv/bin/python -m scripts.load_test \
   --questions tests/data/golden_questions/v9p4_round2.json \
   --out reports/v9p4_round2.json \
   --concurrency 1

# Round 3 — full mix (factoid + multi_hop + chitchat) for tail-latency
.venv/bin/python -m scripts.load_test \
   --questions tests/data/golden_questions/v9p4_round3.json \
   --out reports/v9p4_round3.json \
   --concurrency 1
```

(Adjust the script invocation to match the existing harness; previous
campaigns used `scripts.run_mega_campaign` — see
`reports/MEGA_V2_3ROUND_FINAL_VERDICT_*.md` for the canonical command.)

---

## Acceptance gate

| Metric | Target |
|---|---|
| p95 end-to-end | ≤ 14 s (-2.6 s vs V9.2 16.6 s) |
| p99 end-to-end | ≤ 22 s (-9 s vs V9.2 31 s) |
| HALLU_FABRICATE | **0 / N** (sacred — V9 cannot regress) |
| PASS rate | ≥ 86 % (no V8.5 regression) |
| Cost per turn | ≤ $0.0008 |

If HALLU > 0 → STOP, file incident, do NOT roll forward. The hallu-zero
contract is the cross-cutting V9 promise.

---

## Verify decompose-skip + MQ-dedup metrics fire

After the 3 rounds, scrape `/metrics` and confirm the new V9 Phase 4
counters incremented:

```bash
curl -fsS http://localhost:8000/metrics \
  | grep -E "ragbot_decompose_skipped_low_confidence|ragbot_mq_variants_deduped|ragbot_mq_skipped_no_entities|ragbot_intent_classifier_confidence|ragbot_warmup_provider_duration_ms"
```

Expected pattern (sample, exact numbers depend on bot config):

```
ragbot_decompose_skipped_low_confidence_total{intent="multi_hop"}  N (where N > 0 if multi_hop questions hit low-confidence trap)
ragbot_intent_classifier_confidence_count{intent="factoid"}        N
ragbot_intent_classifier_confidence_bucket{intent="factoid",le="0.7"} N
ragbot_mq_variants_deduped_total                                    N (≥0; 0 is fine if rewriter produced no near-dupes)
ragbot_mq_skipped_no_entities_total                                 N (only > 0 when entity grounding is enabled)
ragbot_warmup_provider_duration_ms_count{provider="reranker",ok="true"} 1
```

If any V9 counter is missing → graph routing bug; metric unwiring at
boot. Inspect `/health` + recent `warmup_provider_complete` log events.

---

## Roll-back trigger

| Symptom | Action |
|---|---|
| HALLU > 0 | Revert decompose-confidence gate (set `DEFAULT_DECOMPOSE_CONFIDENCE_GATE` per-bot via `pipeline_config.decompose_confidence_gate = 0.0`). |
| p95 regressed > V9.2 baseline | Disable MQ dedup per-bot (`pipeline_config.multi_query_dedup_threshold = 1.01`). |
| Reranker warmup probe burned the rate budget | Bump `DEFAULT_WARMUP_TIMEOUT_S` higher OR set `RAGBOT_WARMUP_ENABLED=0` for next deploy and rely on first-call CB cooldown. |
| Tokenizer probe blocking lifespan readiness | Same env toggle as above; the probe runs in executor so should never block the loop, but the env-kill switch is the canonical bypass. |

---

## Notes for the chief auditor

* The decompose-confidence gate is wired at the *route* layer, not the
  decompose node body — so the LLM call is skipped (cost = 0) when the
  classifier reports low confidence. This is the source of the
  -2.6 s p95 trim target.
* MQ dedup runs *after* the rewriter LLM call (the cost is sunk) but
  *before* the parallel hybrid_search fan-out — so dedup saves on
  retrieval round-trips, not on rewriter cost.
* Warmup-extended adds 4 sequential probes at boot capped at
  `DEFAULT_WARMUP_TIMEOUT_S` (10 s) each → worst case 40 s extra
  startup before app is "warm". Lifespan does NOT block on warmup —
  task spawned via `asyncio.create_task`. Readiness latency is
  unchanged.

