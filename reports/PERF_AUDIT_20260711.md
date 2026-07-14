# Performance Audit — whole hot path — 2026-07-11

> Method: 4 parallel read-only agents swept query-pipeline, ingest, LLM-router+cache,
> and DB/repo layers. Main session (Opus) cross-ranked. Continues the async work in
> `reports/ASYNC_BOTTLENECK_SCAN_20260518.md` — the big structural wins there are
> already shipped (parallel cache+understand+speculative, pre-retrieve 3-way gather,
> bounded grade semaphore, `add_steps_batch` collapsing ~27 INSERTs→1, HNSW indexes,
> per-batch bulk chunk INSERT). Findings below are the RESIDUAL hotspots.
>
> **rule#0 / Async-Rule-3:** every number here is GIẢ THUYẾT (a hypothesis to measure
> with `shared/perf.py::timer`), NOT a measured speedup. Nothing is "faster" until
> baseline+after are compared. No lift is claimed.

---

## 0. The honest headline (read this first)

**The pipeline fires ~6–10 LLM calls per question; on the deployed gateway each call is ~1–30s.** That LLM budget dominates end-to-end latency by 1–2 orders of magnitude over every Redis/CPU/DB micro-cost below. Therefore:

- **Only the LLM-call-count reductions (Tier A) move per-question P50/P95.** A whole reflect round-trip removed = ~1–2s off the dominant path. That is the real lever.
- **Tier B (pure-perf, zero-risk) and Tier C (concurrency) matter for THROUGHPUT under load and for freeing the event loop / DB pool — not for single-request latency.** They are worth doing (cheap, safe) but must not be sold as latency wins.
- Do NOT micro-optimize a 5ms Redis hop while a 30s LLM call sits next to it. Prioritize by that reality.

---

## Tier A — reduce LLM-call count (the only real latency lever) — answer-path, char-test-first

| # | Win | file:line | Mechanism | GIẢ THUYẾT | Risk |
|---|---|---|---|---|---|
| A1 | **Reflect fires unconditionally; skip-gate runs AFTER the call** | `reflect.py:90` (call) vs `:148-176` (gate) | The grounded+`top_score≥floor` heuristic only suppresses the RETRY, never the reflect LLM call — yet both its inputs exist before the call. Move the gate ahead of the invoke → skip reflect on grounded high-score answers. Partially duplicates the grounding judge that just ran (`guard_output.py`). | −1 LLM call (~1–2s) on the dominant grounded-factoid path | Med — Self-RAG keep/rewrite decision. Characterization test on the pinned 60Q mandatory. |
| A2 | **Batch-grade is a config toggle, not guaranteed default** | `grade.py:186` (1 call) vs `:318` (N calls) | Bots without `grade_use_structured_output_batch` pay N per-chunk grade calls instead of 1 batched. Confirm default-ON for every bot (or force it). | −(N−1) grade LLM calls on deep top_k | Med — CRAG relevance; char-test. |
| A3 | **Verify no bot still takes the legacy `condense→router` 2-LLM fork** | `query_graph.py:2913-2914,2982` | Merged `understand_query` is 1 call; the legacy condense+router path is 2. `_cache_route` picks one. Confirm no live bot routes legacy. | −1 LLM call if any bot is on the legacy fork | Low — verification, not a code change unless a bot is found |

Discipline: A1/A2 sit on the answer path → red/characterization test first, one change per step, re-run pinned 60Q, attribute the delta. Never combine A1 and A2 in one measurement.

---

## Tier B — pure-perf, behavior-preserving (safe to land first, throughput not latency)

| # | Win | file:line | Anti-pattern | GIẢ THUYẾT | Risk |
|---|---|---|---|---|---|
| B1 | **Embedding computed BEFORE the exact-hash cache check** | `check_cache.py:85-96` + `semantic_cache.py:410-459` | The cheapest outcome (exact-hash hit) still pays a full embedding provider round-trip + tokens. Reorder: text-only SHA lookup first, embed only on hash miss. | Every exact-repeat query saves 1 embedding call + tokens | Low-Med — `find_similar` API takes the embedding as arg → needs a `hash_only` split |
| B2 | **`resolve_runtime` L2 Redis is write-only (dead tier)** | `service.py:445,543` | Redis written every cold resolve, never read back (masked payload can't rehydrate). Pure overhead. Either drop the write or make L2 readable. | Wasted Redis write + JSON serialize per cold resolve | Low (drop-write) |
| B3 | **sys_prompt leak-shingle set recomputed every turn** | `guard_output.py:554-561` | Pure function of (system_prompt, shingle_size) — both stable per bot. Memoize by `record_bot_id`. | Removes a sliding-window sha256 pass every request | Very low (pure fn) |
| B4 | **`inspect.signature` re-introspected on the hot path** | `retrieve.py:1054/1072/1098/1106`; `query_graph.py:1736/1750/1783`; `:1521/1635` | DI handles are process-wide singletons; their signatures are stable. Compute the param-set once at `build_graph`, close over it. | Small CPU × variants × nodes per request | Low |
| B5 | **MQ embedding cache lookups done sequentially** | `query_graph.py:1500-1503` | N independent Redis GETs in a `for` loop → `gather()` or Redis `MGET`. | −(N−1)×Redis RTT on MQ pre-warm | Low |
| B6 | **Stats-index extraction blocks ingest finalize** | `ingest_stages_final.py:498-586` | Sync table re-parse + delete/insert on the hot path for a best-effort side index, AFTER `document_ingested` is logged. GraphRAG already backgrounds this (`:420-428`). | Per-doc finalize latency inflated; event loop stalled | Low — mirror the GraphRAG `create_task` pattern |
| B7 | **hybrid_search returns full 1280-dim vector per candidate** | `pgvector_store.py:530,540,555,598` | The embedding column is serialized back for top_k×2 rows to feed MMR; dead weight when MMR/diversity is off. Plain `search()` already omits it. | Largest single over-fetch on retrieve | Low — drop column unless MMR flag on |

---

## Tier C — concurrency / event-loop / pool (matters under load, needs care)

| # | Win | file:line | Anti-pattern | GIẢ THUYẾT | Risk |
|---|---|---|---|---|---|
| C1 | **Doc-shingle sha256 O(K·M) blocks the event loop** | `guard_output.py:570-580` | Nested split + sliding-window sha256 over every graded chunk runs synchronously → stalls all concurrent requests on the worker at high top_k. | Biggest non-LLM CPU stall in guard_output under concurrency | Med — leak guard (security); memoize by chunk_id or `to_thread`, char-test |
| C2 | **Chunking / profiling / stats-parse run on the event loop (ingest)** | `ingest_stages.py:770,615,664`; `document_stats.py:1084+` | Heavy regex/atomic-split CPU not offloaded (VN segmentation already uses `to_thread` — the pattern to copy). | Large doc stalls the loop, starves co-tenant ingest/query | Low-Med — wrap in `asyncio.to_thread`, verify no shared mutable state |
| C3 | **Retry backoff paid TWICE on failover (~4.5s)** | `dynamic_litellm_router.py:596,632,744` | Primary exhausts ~2.25s of backoff, then the fallback hop runs another full `retry_with_backoff`. | Failover latency up to ~4.5s of pure sleep before fallback answer | Med — ties into the T2-5 fallback work; changes resilience semantics |
| C4 | **`pool_pre_ping` = SELECT 1 per checkout; ~9+ checkouts/question; 30-conn ceiling** | `engine.py:52`; `constants/_05:52-53`; per-call sessions in `ai_config_repository.py` | Every pooled checkout does a liveness RTT; retrieval/cache checkouts uncapped → pool exhaustion → 30s waits under load. | +1 DB RTT/checkout; pool-exhaustion tail under high QPS | Med — pre-ping guards stale conns; sizing is env-specific, measure |
| C5 | **Sequential embedding sub-batches (ingest)** | `litellm_embedder.py:168-213`; `bkai_vn_embedder.py:226-307` | `for`-loop of sequential `await`; wall-time = Σ sub-batch latency vs max. | Many-chunk docs embed slower than necessary | Med — provider 429; keep CB + bounded `Semaphore(k)` |
| C6 | **Semantic-cache HNSW post-filter, no `ef_search` set** | `semantic_cache.py:474-489` | Filtered-HNSW on a shared multi-tenant table with no `SET hnsw.ef_search` (unlike pgvector_store) → low recall (extra full run) or seq-scan as it grows. | Cache-check per turn degrades as table grows | Low-Med — add `SET LOCAL ef_search` + composite/partial index |

---

## Recommended order

1. **Tier B first** (B2, B3, B4, B5, B6 are near-zero-risk, behavior-preserving) — land them under existing unit tests; they free CPU + Redis + a wasted embedding, no answer-path exposure. B1/B7 next (low risk, small reshape).
2. **Then Tier A with characterization tests** — A3 (verify only), then A1, then A2, each measured on the pinned 60Q one at a time. This is where real P50/P95 moves.
3. **Tier C under a load probe** — C1/C4 need the `reliability_probe.py` concurrency harness to show the throughput/tail win (they don't move single-request latency). C3 folds into the fallback-provider work (T2-5).

**Measurement contract (Async Rule 3, non-negotiable):** each change gets a `perf.py::timer` baseline before and after on the same input; no change is reported as a win without the two numbers. Under load, use `reliability_probe.py` for p50/p95. Because the LLM budget dwarfs everything, report Tier B/C wins as **event-loop-ms / DB-checkout / throughput** deltas, NOT as end-to-end latency claims.

## SHIPPED this pass (zero-latent-bug only, verified)
- **B5** — MQ embedding cache lookups parallelized (`query_graph.py:1500`, sequential loop → `asyncio.gather`). Behavior-preserving (same results/order/error semantics). Verified: AST OK + 107 MQ tests pass.
- **B2** — dead L2 Redis write removed from `resolve_runtime` (`service.py:541-549` + unused `import json`). VERIFIED dead: the `model_runtime:*` key is written but read ONLY from L1; `_get_cached`'s `_cache.get` reads a different namespace (`ai_cfg:*`); no external scanner; no test asserts the write. Verified: AST OK + 27 resolver tests pass + F401 count unchanged (20→20, pre-existing strangler debt, not touched).
- Deferred as NOT-zero-risk: B1/B3/B4/B6/B7 + all Tier A/C (each carries an invalidation/timing/answer-path risk — need char-tests or load probe, per the user's zero-latent-bug bar).

## What was verified GOOD (no action)
- No `gather()`-across-shared-session (Async Rule 7) anywhere — every gathered coroutine opens its own session.
- pgvector HNSW correctly indexed + `ef_search` tuned + partial `doc_deleted_at IS NULL` for pushdown.
- Chunk store + stats bulk_insert are multi-row INSERTs (not N+1); `add_steps_batch` batched.
- Foreground/background LLM lanes separated (background grounding can't starve foreground generate).
- Engines built once at boot (no per-request engine); incremental re-embed by content-hash diff implemented.
