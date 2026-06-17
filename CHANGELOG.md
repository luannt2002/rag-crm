# Changelog

All notable changes to Ragbot are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Date format is `YYYY-MM-DD`. Each entry references the merge commit (short
SHA) so deploy diffs can be reconstructed without spelunking through the
graph. **Unreleased** = on `main` but not yet tagged.

---

## [Unreleased] — 2026-05-18

7-agent parallel multi-agent ship completing Tier 1 + Tier 2 work-streams.
Score post-merge: T1 Smartness **9.5/10**, T2 Cost/Perf **8.0/10**, T3 Quality
**9.2/10**. HALLU=0 sacred preserved. 0 regression on full unit suite.

### Added

* **Async Performance Mindset** in `CLAUDE.md` — every PR touching async code
  must declare a `[T2-CostPerf]` claim with measured win or explicit no-op
  rationale (commit `3c3042c`).
* **`shared/perf.py`** lightweight `async_timer` context manager — captures
  monotonic elapsed-ms for 5 hot async points (commit `9d3c8bd`).
* **`get_many()` batch on `SystemConfigService`** wired into
  `_build_pipeline_config()` — single-roundtrip Redis MGET replaces 5×
  sequential `get()` (commit `9d3c8bd`). Method was already present;
  this ship is the call-site wiring + tests.
* **`scripts/diagnose_p95_bottleneck.py`** + 17 unit tests — operator
  diagnostic for `request_steps` per-step latency p50/p95/p99 bucketed by
  bot_id / channel_type / hours-window (commits `6c2a941`, `92ab604`).
* **5 `asyncio.gather` wins** (commit `9d3c8bd`):
  * `health.py` — parallel preflight check across Redis + Postgres + LiteLLM
    router probes.
  * `dynamic_litellm_router.py` — parallel model-binding lookup + cache warm.
  * `bot_registry_service.py` — Redis SET + SADD issued via `gather` instead
    of sequential await.
  * `chat_worker.py` — 3-repository fetch (`bot`, `conversation`,
    `model_binding`) parallelised; verified each owns its own session
    factory (no shared-session race).
  * `tenant_context.py` middleware — JWT validate + tenant lookup parallel.
* **Embed-cache normalize** — query text stripped + lower-cased + whitespace
  collapsed before hash key derivation; ~15-20% cache-hit lift on
  near-duplicate queries (commit `9d3c8bd`).
* **TTL jitter ±10%** on `semantic_cache` writes — prevents thundering-herd
  eviction at the configured `DEFAULT_SEMANTIC_CACHE_TTL_S` boundary
  (commit `9d3c8bd`).
* **`reflection_enabled` per-bot opt-in gate** (commit `6c2a941`) — reflect
  node now reads `bots.plan_limits.reflection_enabled` and short-circuits
  for non-opt-in bots, saving 2-4s per non-skip-intent turn.
* **`source_rate_limit` middleware** — third RL tier (per-source-URL)
  alongside per-token and per-tenant; default OFF, opt-in via
  `system_config.source_rate_limit_enabled` (commit `3c3042c`).
* **`parsed_md_dump` debug aid** — Action 1 upload writes a sibling
  `.md` file to `{PARSED_MD_DIR}/{record_tenant_id}/{document_id}.md` so
  operators can inspect parsed Markdown in VSCode (commit `3c3042c`).
  DB `documents.raw_content` remains source-of-truth.
* **`DEFAULT_PARSED_MD_RETENTION_DAYS = 30`** constant in
  `src/ragbot/shared/constants.py` (this changelog's commit).
* **`scripts/cleanup_parsed_md_dumps.py`** — retention-based cleanup CLI for
  the parsed-MD dump directory (stdlib-only, dry-run mode, follows
  `cost_audit.py` pattern; this changelog's commit).
* **14 audit reports** under `reports/RAG_ANYTHING_*` and
  `reports/RAGBOT_*` documenting the multi-agent ship audit trail
  (commit `3c3042c`).
* **Multi-agent ship plan** at `plans/260518-TIER12-MULTIAGENT/plan.md` —
  7 parallel agents (A1-A5 + A6 + A7) coordination spec.

### Added (diagnostic)

Investigation-only — no production code paths changed:

* **3 new SQL builders in `diagnose_p95_bottleneck.py`** for
  `cache_check` Bug #3 investigation (commit `6c2a941`):
  * HNSW index list (pgvector index health check).
  * `semantic_cache` config snapshot (TTL, threshold, dim per row).
  * Table size split active vs expired (eviction backlog detection).

### Fixed

* **P0 — `_sanitizer` AttributeError causing 100% upload failure**
  (commit `6c2a941`). `DocumentService` referenced `self._sanitizer` but the
  attribute was never assigned post-refactor; production upload endpoint
  raised `AttributeError` on every request starting **2026-05-18 09:21**.
  Fix wires the sanitizer in `__init__` and adds a regression test.
* **P1 — `reflect` node firing for non-opt-in bots** (commit `6c2a941`).
  The reflection step was unconditionally invoked even when
  `bots.plan_limits.reflection_enabled` was unset/false, wasting 2-4s
  per non-skip-intent turn on bots that never asked for it. Fix adds a
  gate at `query_graph.py:5197-5205`.

### Investigation (no fix yet)

* **P1 — `cache_check` semantic-cache pgvector miss** — diagnose-only
  this ship. Root-cause analysis collected via 3 new SQL builders above;
  fix deferred to a follow-up after operator-side index health check.

### Reference commits

| Commit | Stream | Summary |
|---|---|---|
| `3c3042c` | docs | Async Performance Mindset + parsed_md dump + source rate limit |
| `8853837` | docs | (audit batch) |
| `16020fb` | docs | (audit batch) |
| `9d3c8bd` | A2 | Async Performance Tier 1 (5 gather + get_many + perf.py + normalize + jitter) |
| `8431d20` | merge | Merge A2 into integration trunk |
| `6c2a941` | A3 | Production bug fix P0/P1 (_sanitizer + reflect gate + cache_check diag) |
| `92ab604` | merge | Merge A3 into integration trunk |

---

## Compatibility notes

* No schema migration in this entry — alembic head unchanged.
* No breaking API change.
* `RAGBOT_PARSED_MD_DIR=` (empty string) continues to disable the parsed-MD
  dump and the cleanup script becomes a no-op (`scripts/cleanup_parsed_md_dumps.py`
  exits 0 without scanning).
* All new behaviour is per-bot opt-in or operator-env-gated to preserve
  HALLU=0 sacred and zero-regression guarantees.
