# 16-P — RAGSchema (Paper 26 RAGO knob spec)

> **Tier**: T2 perf+cost (Stream D RAGO Pareto sweep)
> **Purpose**: Tabular spec of all RAG knobs that the offline Pareto sweep
> (`scripts/rago_pareto_sweep.py`) explores. Each knob lists name (matching
> `system_config` row OR `shared/constants.py` constant), type, range,
> default, primary impact axis (quality / latency / cost), and sweep range.
>
> **Update protocol**: every knob added must (a) exist in
> `shared/constants.py` as `DEFAULT_*` OR `system_config` row, (b) have a
> verified read site in `src/ragbot/`, (c) declare an impact axis backed by
> code path. No speculative knobs.

---

## Schema rows

| # | Knob | Type | Min | Max | Default | Impact | Sweep range | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | `chunk_size` | int | 256 | 2048 | 1024 | quality+cost | [512, 1024, 1536, 2048] | `DEFAULT_CHUNK_SIZE` (`constants.py:21`) |
| 2 | `chunk_overlap` | int | 0 | 256 | 128 | quality | [0, 64, 128, 192] | `DEFAULT_CHUNK_OVERLAP` (`constants.py:22`) |
| 3 | `rag_top_k` | int | 5 | 30 | 20 | quality+latency | [10, 15, 20, 25] | `DEFAULT_TOP_K` (`constants.py:11`) |
| 4 | `rag_rerank_top_n` | int | 3 | 15 | 7 | quality+latency | [5, 7, 10, 12] | `DEFAULT_RERANK_TOP_N` (`constants.py:15`) |
| 5 | `multi_query_n_variants` | int | 1 | 7 | 5 | quality+latency+cost | [1, 3, 5, 7] | `DEFAULT_MULTI_QUERY_N_VARIANTS` (`constants.py:1110`) |
| 6 | `rrf_k` | int | 30 | 90 | 60 | quality | [30, 60, 90] | `DEFAULT_RRF_K` (`constants.py:39`) |
| 7 | `reranker_enabled` | bool | — | — | true | quality+latency+cost | [true, false] | `DEFAULT_RERANKER_ENABLED` (`constants.py:85`) |
| 8 | `reranker_min_score_active` | float | 0.0 | 1.0 | 0.4 | quality+latency | [0.2, 0.4, 0.6] | `DEFAULT_RERANKER_MIN_SCORE_ACTIVE` (`constants.py:89`) |
| 9 | `multi_query_enabled` | bool | — | — | true | quality+latency+cost | [true, false] | `DEFAULT_MULTI_QUERY_ENABLED` (`constants.py:1105`) |
| 10 | `grade_use_structured_output` | bool | — | — | true | quality+cost | [true, false] | `DEFAULT_GRADE_USE_STRUCTURED_OUTPUT` (`constants.py:1434`) |
| 11 | `grade_use_batch` | bool | — | — | true | latency+cost | [true, false] | `DEFAULT_GRADE_USE_BATCH` (`constants.py:1443`) |
| 12 | `pipeline_parallel_rewrite_mq_enabled` | bool | — | — | true | latency | [true, false] | `DEFAULT_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED` (`constants.py:1138`) |

**Total search space (all combinations, brute-force)**:
- Continuous-discretized: 4 × 4 × 4 × 4 × 4 × 3 = 3072 (knobs 1-6)
- Boolean knobs: 2^6 = 64 (knobs 7-12)
- Combined: ~196,608 configs — too large for exhaustive

**Sweep strategy**: Latin hypercube sample 30 configs (default in
`rago_pareto_sweep.py`). Owner can override via `--n-configs` flag.

---

## Impact axis legend

- **quality** — affects PASS_rate / faithfulness / refuse rate
- **latency** — affects p95 wall-clock per turn
- **cost** — affects $/turn (LLM token + rerank API + embed)

A knob can have multiple axes. The Pareto frontier is computed on the
3-axis output `(PASS_rate, p95_ms, cost_per_turn)` — the knob's impact
listing is informational only (not used in frontier compute).

---

## Knob value resolution chain

The sweep harness applies a config by writing rows to `system_config`
(global override). Per-bot `pipeline_config` JSONB on `bots` row could
also be used but the global override is simpler for sweep isolation.

Read sites in pipeline (resolved at request time via `_pcfg(state, key,
default)` or direct `system_config` lookup → cache):

- `chunk_size`, `chunk_overlap` → ingest path (`shared/chunking/` package)
- `rag_top_k` → `query_graph.retrieve` node
- `rag_rerank_top_n` → `query_graph.rerank` node + `infrastructure/reranker/*`
- `multi_query_*` → `application/services/multi_query_expansion.py`
- `rrf_k` → `application/services/multi_query_expansion.rrf_merge_chunks`
- `reranker_*` → `infrastructure/reranker/registry.py` + `zeroentropy_reranker.py` (default, `zerank-2`) / `jina_reranker.py` / `litellm_reranker.py` / `viranker_local_reranker.py` / `null_reranker.py`
- `grade_*` → `query_graph.grade` node
- `pipeline_parallel_*` → `query_graph.rewrite_and_mq_parallel`

---

## Out of scope (NOT in this sweep)

These knobs exist but are excluded from sweep for stated reason:

- `generator_model` (gpt-4.1-mini vs gpt-4.1) — bound to `bot_model_bindings`
  per-bot, not a `system_config` flip. Owner manually swaps.
- `prompt_cache_enabled` — Anthropic-side feature, currently always ON.
- `record_tenant_id`, `record_bot_id` — identity, not perf knob.
- `oos_threshold`, `crag_grade_threshold` — guardrail, sweeping risks
  HALLU breach. Defer to dedicated guardrail study.
- `language` — domain-neutral mandate; bot owner sets.

---

## Sweep output schema

CSV columns produced by `scripts/rago_pareto_sweep.py`:

```
config_id, chunk_size, chunk_overlap, rag_top_k, rag_rerank_top_n,
multi_query_n_variants, rrf_k, reranker_enabled, reranker_min_score_active,
multi_query_enabled, grade_use_structured_output, grade_use_batch,
pipeline_parallel_rewrite_mq_enabled,
n_turns, pass_rate, p95_ms, cost_per_turn, hallu_count, error_count
```

Frontier compute (`scripts/rago_pareto_pick.py`) reads this CSV.

---

## References

- Paper 26 RAGO — `docs/academic-papers/26-rago-serving.md`
- arXiv 2503.14649 — https://arxiv.org/abs/2503.14649
- Plan — `plans/260506-streamD-rago-pareto/plan.md`
