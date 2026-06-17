# Performance Tuning Guide

> **Audience**: ops + backend engineers tuning Ragbot for latency/cost/UX (T2).
> **Prerequisite**: T1 quality is locked first (HALLU=0 sacred, FAITH ≥ 0.9). Never trade T1 for T2 (CLAUDE.md MVP priority).
> **Latest baseline**: VC 3-round V2 final 2026-05-01 — 78.0 % PASS, p95 OLD 22.0 s / NEW 21.4 s, $0.00076 / turn. See [`reports/MEGA_V2_3ROUND_FINAL_VERDICT_20260501.md`](../reports/MEGA_V2_3ROUND_FINAL_VERDICT_20260501.md).
> **Goal targets** (T2 GA blocker): p95 ≤ 8 s, $/turn ≤ $0.001.

---

## 1. p95 latency budget breakdown

The full Q1–Q17 graph dominates uncached p95. Per-node attribution is taken from `request_steps` (12 steps live; 15 of the canonical 24 not yet instrumented — see [`project_pipeline_24step_status`](../STATE_SNAPSHOT.md)). Shares below are observed mean fractions on the V2 stack at VC.

| Tier | Node | p50 | p95 | Share of p95 |
| :--- | :--- | ---: | ---: | ---: |
| 1 | Q14 generate (LLM) | 2.4 s | 6.5 s | ~30 % |
| 2 | Q12 grade (CRAG) | 1.0 s | 3.2 s | ~15 % |
| 2 | Q6 retrieve (hybrid + RRF) | 0.6 s | 2.8 s | ~13 % |
| 2 | Q4 + Q5 multi-query / decompose (sequential today) | 1.4 s | 3.8 s | ~17 % |
| 3 | Q3 understand_query | 0.4 s | 1.2 s | ~6 % |
| 3 | Q10 rerank (Jina v3 single batch) | 0.5 s | 1.6 s | ~7 % |
| 3 | Q15 + Q16 guard_output + reflect | 0.3 s | 1.1 s | ~5 % |
| 3 | Q2 cache check + Q17 persist + I/O | 0.2 s | 0.8 s | ~4 % |

**Hot path is generation.** The next-biggest swing is the **sequential** Q4 → Q5 → Q6 chain — parallelising the multi-query fan-out is the single largest win on the table (see §3.1).

---

## 2. Top 5 bottlenecks (VC measurements)

### 2.1 Q14 generate — LLM round-trip on `gpt-4.1-mini`

- p95 6.5 s on long answers (≥ 400 output tokens).
- Knobs:
  - `bot_model_bindings.options.max_tokens` per bot — clamp generation to what the system_prompt actually needs (default 450).
  - Streaming via `/chat/stream`: TTFT P50 240 ms / P95 620 ms (perceived latency win even when wall time is unchanged).
  - Prompt caching: OpenAI auto-cache fires at ≥1024-token prompts; Anthropic uses explicit `cache_control` (helper `infrastructure/llm/prompt_cache.py`). `prompt_cache_hits_total` Prometheus counter tracks.
- **Do not** swap to a smaller model for generation (T1 regression). Smaller models stay on grading/rewrite/decompose only.

### 2.2 Q12 grade — CRAG 3-state on cheap LLM

- 1× LLM call per turn; p95 3.2 s.
- Knobs:
  - Bind `purpose='grading'` to a nano-class model (`gpt-4.1-nano` or equivalent — 10× cheaper, 3× faster than mini). Seed: per-bot binding override.
  - Disable per-bot via `plan_limits.crag_enabled = false` for chitchat-heavy bots; quality regression measurable, only do this with HALLU=0 evidence post-change.

### 2.3 Q6 retrieve — pgvector + tsvector hybrid + RRF

- p95 2.8 s; spikes when chunk count > 50 K or `ef_search` is too high.
- Knobs:
  - `system_config.ef_search` — start 64; raise only if recall@10 drops; halve if p95 of Q6 > 1.5 s.
  - HNSW index health: rebuild after large bulk ingests (`REINDEX CONCURRENTLY ...`).
  - DB `max_connections` ≥ 100; pool `db_size` ≥ workers × 4. Watch `db_in_use` from `/health`.

### 2.4 Q4 + Q5 multi-query / decompose — sequential

- The current graph runs `rewrite (HyDE)` and `decompose` serially before retrieve. p95 contribution 3.8 s.
- **Plan**: A+D parallel fan-out — drafted in [`plans/260501-R3-PERF-PARALLEL/plan.md`](../plans/260501-R3-PERF-PARALLEL/plan.md). Target p95 22–27 s → 12–15 s once shipped. Status: DRAFT (not on main).
- Until shipped, you can disable decompose per-bot via `plan_limits.decompose_enabled=false` for narrow-corpus bots; expect a small recall regression.

### 2.5 Q3 understand_query — extra LLM hop on simple intents

- p95 1.2 s. The chitchat-pattern heuristic (`bbcb18e`) short-circuits greeting turns before Q3 is reached, so its share is dropping. Watch `chitchat_shortcircuit_total` to see hit-rate.

---

## 3. Parallelisation knobs

### 3.1 Multi-query parallel A + D (planned)

Fan-out: dispatch HyDE rewrite + decompose simultaneously, then merge with RRF before Q6. Fixes the dominant serial chain (§2.4).

- Plan: `plans/260501-R3-PERF-PARALLEL/plan.md`.
- Enable when shipped via `system_config.multi_query_parallel_enabled = true` (Strategy + DI: per-bot override possible).
- Roll out behind a per-bot toggle; do not flip globally before a 150-turn harness baseline (PASS-rate must hold).

### 3.2 Cache hit-rate optimisation

L1 (Redis exact-hash) hit-rate is the cheapest tier and rises ~linearly with traffic. L2 (semantic pgvector) is **broken on V2 stack** (cache writes throw `DataError` because column dim is 1536 vs vector 1024 — V2 BUG #2). Fix is the `embedding_column` kwarg pattern; tracked in [`docs/V2_MIGRATION_BUG_LESSONS.md §BUG-2`](V2_MIGRATION_BUG_LESSONS.md).

Targets once L2 is fixed: 25–40 % L2 hit-rate on conversational traffic, 1.93 → 25 % cache_hit % swing on the verdict table.

### 3.3 Cache-stampede single-flight

`asyncio.Lock` per cache-key already deployed. Metric: `cache_stampede_avoided`. No tuning required.

### 3.4 Connection-pool tuning

| Pool | Default | Recommended for production |
| :--- | ---: | :--- |
| Postgres `db_size` | 5 | `max(20, workers × 4)` |
| Postgres `max_overflow` | 10 | 2× `db_size` |
| Redis `max_connections` | 50 | `max(50, workers × 8)` |
| HTTPX (LLM router) | 100 | unchanged unless `circuit_breaker_state` flapping |

Observe via `/health`'s `pool_stats` block (gauges: `db_in_use`, `db_idle`, `redis_in_use`, `redis_available`). Saturation = bump pool **before** scaling workers.

### 3.5 CircuitBreaker per provider

5-fail / 30 s cooldown on JinaReranker + LiteLLMEmbedder (Sprint 13/14 hardening; commits `64cdc0a` / `a3d7700`). When `circuit_breaker_state` flips to `open`, expect the orchestrator to fall back to the registry's `Null<Thing>` and log it. Fix the upstream root cause; do not extend the cooldown window without a plan.

### 3.6 Rate-limit fail-closed

`tenants.rate_limit_per_min` enforced via Redis sliding window. If Redis is down, the limiter rejects (fail-closed) — Sprint-12B explicit decision. To bypass per-bot for a high-trust path, set `bots.bypass_rate_limit=true`; never disable globally.

---

## 4. Anthropic prompt caching pattern

For Anthropic models, prompt caching requires explicit `cache_control` markers; OpenAI does it automatically at ≥1024-token prompts. We expose a hybrid helper at `infrastructure/llm/prompt_cache.py` so application code is provider-agnostic:

```python
# Application code uses the helper; the helper picks the right strategy per provider.
from ragbot.infrastructure.llm.prompt_cache import build_messages_with_cache

messages = build_messages_with_cache(
    system=bot.system_prompt,
    history=history_msgs,
    documents=retrieved_chunks_text,
    question=user_q,
)
# OpenAI: messages flow through unmodified; OpenAI auto-detects ≥1024-token reusable prefixes.
# Anthropic: helper inserts {"type":"text","cache_control":{"type":"ephemeral"}} on the
#            stable system + history prefix and leaves question/documents fresh.
```

Counter `prompt_cache_hits_total` (label `provider`) reports hit-rate. Target: ≥ 60 % on returning conversations.

---

## 5. Cost levers (T2 budget)

VC averages $0.00076 / turn. The cost stack:

| Lever | Lever effect |
| :--- | :--- |
| Bind `purpose='grading' / 'rewriting' / 'decompose' / 'understand_query'` to a nano-class model | −40 % $/turn (3× faster + 10× cheaper than mini) |
| Enable prompt caching on Anthropic generation | −15 % token cost on long contexts |
| Multi-query parallel + drop redundant rewrite path on chitchat | −10 % $/turn after T2 perf-parallel ships |
| Raise `cache.exact_hash_ttl` when answers are stable | +cache hit-rate, no quality risk |

Always measure before/after with `scripts/agent_d_loadtest.py` (≥ 50 turns) — invocation_logger emits per-call `cached_input` + cost so the delta is auditable.

---

## 6. Load-test methodology

Reproducible run:

```bash
# Bootstrap a service token (RBAC: super_admin)
TOKEN=$(curl -s -X POST $RAGBOT_BASE_URL/api/ragbot/test/tokens \
  -H "Authorization: Bearer $RAGBOT_BOOTSTRAP_TOKEN" \
  -d '{"tenant_id":1,"role":"super_admin","ttl_seconds":7200}' | jq -r .token)

# Run harness (150 turns, batch-of-10 progress, JSON output)
.venv/bin/python scripts/agent_d_loadtest.py \
  --questions reports/MEGA_VC_questions_old.txt \
  --tenant 1 --bot <bot-name> --channel web \
  --token "$TOKEN" \
  --batch-size 10 \
  --out reports/load_run_$(date +%s).json
```

Always pair with the post-hoc analyser (`scripts/loadtest_batch_analyze.py`) and store the verdict alongside the run JSON. Compare against the previous round's PASS-rate — never lose ground (CLAUDE.md "tests baseline never decreases" rule).

---

## 7. SLA verdict

| Metric | Target | VC current | Status |
| :--- | ---: | ---: | :---: |
| p95 chat | ≤ 8 000 ms | 22 000 ms (OLD) / 21 400 ms (NEW) | NOT READY |
| TTFT stream P95 | ≤ 1 500 ms | 620 ms | READY |
| $/turn | ≤ $0.001 | $0.00076 | READY |
| HALLU_FABRICATE | 0 / 15 trap | 0 / 15 sacred | READY |
| FAITH proxy | ≥ 0.9 | 0.983 | READY |
| Error rate | 0 % | 0 % | READY |

**Verdict**: T2 cost + reliability ready; **T2 latency is the GA blocker**. Multi-query parallel is the single largest unshipped win (§3.1).

---

## 8. See also

- [`docs/PERFORMANCE.md`](PERFORMANCE.md) — historical v1.x post-fix baseline (kept for diff reference).
- [`docs/OPS_POOL_SIZING.md`](OPS_POOL_SIZING.md) — formula + rationale for pool sizing.
- [`reports/MEGA_V2_3ROUND_FINAL_VERDICT_20260501.md`](../reports/MEGA_V2_3ROUND_FINAL_VERDICT_20260501.md) — full VA/VB/VC numbers.
- [`plans/260501-R3-PERF-PARALLEL/plan.md`](../plans/260501-R3-PERF-PARALLEL/plan.md) — multi-query parallel plan (DRAFT).
- [`docs/ARCHITECTURE_DIAGRAMS.md`](ARCHITECTURE_DIAGRAMS.md) — per-node visual map.
- [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — when "p95 spiked" → which symptom corresponds to which fix.
