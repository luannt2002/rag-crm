# [T2-CostPerf] P4 — Ingest priority queue (noisy-neighbor fairness)

> Tier: **T2** (UX/perf — a large file from one tenant must not starve a small file from another).
> Does NOT touch answer correctness (T1). Defer below P9.

## Root cause (verified — rule#0, SỰ THẬT)
- **Single FIFO queue**: ingest is one Redis stream keyed by event type. The document worker consumes
  `SUBJECT_DOCUMENT_UPLOADED = "document.uploaded.v1"`
  ([document_worker.py:60](src/ragbot/interfaces/workers/document_worker.py#L60)); the bus routes by
  `_stream_key(event_type)` ([redis_streams_bus.py:109](src/ragbot/infrastructure/events/redis_streams_bus.py#L109))
  — **no split by size or tenant**. A 50MB OCR job ahead of a 5KB job blocks it (head-of-line).
- **Orphan knob**: `priority_tier` (`"shared"`/`"priority"`) is declared in
  `PLAN_LIMIT_SCHEMA` ([bot_limits.py:99](src/ragbot/shared/bot_limits.py#L99)) but has **zero consumers**
  — no code routes on it (grep: only the schema line). The intent exists; the wiring does not.
- Context: `ingest-fairness` ADR-W2-D8 exists (per memory) but priority-queue itself is unimplemented.

## Strategy — EVOLVE (add lanes, keep the bus + worker contract)
Do NOT rewrite the bus. Add a **lane dimension** to the existing stream-key + a bounded number of worker
consumer groups per lane. Publisher classifies the job into a lane at enqueue; consumers drain lanes with a
weighted policy so the fast lane is never starved by the truck lane. Backward-compat: default single-lane
behaviour when the feature flag is OFF.

## Design (config-driven, zero-hardcode, no per-bot literal)
### A. Lane classification (at publish)
- Lane = function of (a) `priority_tier` (paid bots → `priority`), AND (b) payload size threshold
  `DEFAULT_INGEST_FASTLANE_MAX_BYTES` (shared/constants) — small jobs → `fast`, large → `bulk`.
  Result: lanes `{priority, fast, bulk}` (names in constants, not inline).
- `_stream_key(event_type)` → `_stream_key(event_type, lane)` so the stream becomes
  `…document.uploaded.v1.{lane}`. Single-lane today = the no-suffix key (backward-compat when flag OFF).
### B. Weighted consumer drain
- Worker runs N consumer groups (one per lane) with a weighted round-robin
  (`DEFAULT_INGEST_LANE_WEIGHTS`, e.g. priority:fast:bulk = 4:2:1) so a long bulk job cannot monopolise
  the worker — between bulk batches the worker services fast/priority.
- Bound concurrency per lane via existing semaphore pattern (Async Rule 6) — no unbounded fan-out.
### C. Config + flag
- `ingest_priority_queue_enabled` (system_config + per-bot plan_limits, default **False**) — flag OFF =
  byte-identical single-queue today. Lane weights + size threshold in system_config (Redis-cached),
  overridable without redeploy.

## Stages
1. **ADR FIRST** — `docs/adr/` entry: multi-lane ingest. Hard-to-reverse (stream topology + consumer
   groups), surprising-without-context, real trade-off (fairness vs ordering vs ops complexity). **Gate:
   user approves ADR before code.**
2. **CODE (TDD)** — lane classifier (pure fn, TDD: size×tier → lane); `_stream_key` lane suffix
   (backward-compat test: flag OFF → unchanged key); weighted drain in worker (TDD on the scheduler
   picking order, mocked streams). Publisher wires lane at enqueue.
3. **VERIFY (runtime, rule#0)** — load test: enqueue 1 large + many small concurrently, measure small-job
   wait-time p95 with flag OFF vs ON (must drop materially); no job lost (exactly-once preserved); large
   job still completes. No claim without the measured numbers.

## Sacred-rule compliance (self-audit)
- #0 evidence-first ✅. Zero-hardcode ✅ (lane names/weights/threshold → constants + system_config).
- Domain-neutral ✅ (lanes by size/tier, not by bot/brand). No per-bot logic in core ✅ (reads
  `priority_tier` config, no `if bot_id==`). No-version-ref ✅ (lane names purpose-based, not v1/v2).
- Async rules ✅ (bounded per-lane semaphore; exactly-once ACK-after-process preserved — Rule 8).
- Narrow-except ✅. Model-tier ✅ (no LLM). EVOLVE ✅ (bus + worker contract kept).

## ADR? **YES — required** (architectural, hard-to-reverse). Code blocked on ADR approval.

## Verification gate
ADR approved · TDD green · ruff 0-new · flag-OFF identity proven · domain-neutral grep 0 · runtime
fairness delta measured (small-job p95 OFF vs ON) · zero job loss · exactly-once preserved.
