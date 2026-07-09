# ALL-FLOWS DEEP AUDIT — SYNTHESIS (multi-agent, code-truth) · 2026-07-09

> Workflow `wf_521d6a0b-8f5`: 24/24 agents, 0 errors. Adversarial verify: **52 CONFIRMED · 3 PLAUSIBLE · 0 REFUTED** across 11 flows. Every claim cites file:line or a DB/load-test number. Companion: `ALLFLOWS_DEEP_AUDIT_20260709.md` (my consolidation).

---

# ALL-FLOWS DEEP AUDIT (code-truth)

> Synthesis of 11 adversarially-verified flow audits (CONFIRMED/PLAUSIBLE only) against the DB-verified load-test 2026-07-08 (`ragbot_v2_dev`, n=302 full-day / 307 in the step-trace snapshot). Every claim below cites `file:line` or a DB/load-test number. `[RPT]` = report-sourced (not DB-verifiable); `[DB]` = live-DB verified; `[CODE]` = code-proven.

---

## 1. Executive truth (sự thật xuyên suốt)

1. **The engine's retrieval/rank/grade path is genuinely fast and clean — the crisis is 100% in LLM calls.** `[DB]` retrieve p50 **18ms** (max 1.77s), rerank p50 **1.49s**, grade p50 **0ms**, rrf/mmr/litm/citations all ≤48ms. Total non-LLM BE work ≈ **2s**. Yet request p50 = **45.4s**, p95 = **110.0s**, and **237/302 (79%) requests exceed 30s** `[DB]`. Latency is understand+generate+sync-grounding+decompose/rewrite — nothing else.

2. **Three LLM anti-patterns own the entire latency budget:** (a) `understand_query` fires on **307/307** requests at p50 **15.2s** `[DB]` — an unconditional router LLM floor before generate; (b) the **sync grounding judge** hits the 30s cap on **31/45 = 69%** of the runs it executes (status still `'running'`, 30001ms, *no verdict returned*) `[DB]`; (c) `retry(3) × 90s` innocom timeout stacks toward a **270s** theoretical worst-case (`_04_jwt_auth.py:180`; alembic `20260708_raise_innocom_timeout_90s.py:31`).

3. **Correctness/HALLU is NOT in the system of record — the 93%/HALLU=1 baseline cannot be reproduced from DB.** `[DB]` `request_logs.is_correct` = **0/307 populated**, `quality_evaluator` = **0/307**, `refusal_reason` = **0/302 (all NULL)**. Grounding verdict only emits structlog (`query_graph.py:905/916`), never a queryable column. Baseline lives only in the external grader `wtbvzdlbc` → labeled `[RPT]`, un-auditable.

4. **All 7–8 request failures are infra, not logic.** `[DB]` every `failed` row = `PIPELINE_ERROR — LLMError: LLM provider innocom failed after retries`, last successful step = `litm_order` on every one → retrieval/rerank/order all completed; death was at the **generate LLM call** with `output_tokens=0`. The report's "comparison/retrieval/coverage" fail-tags for ≥4 of these are **confounded** — they manifested as innocom 5xx timeouts, not clean misses.

5. **Multiple "shipped fixes" are inert or dead code — false-green confidence.** `extract_all_codes` (the comparison two-entity fix) has **zero src call-sites** (`query_range_parser.py:509` + only its test); `rrf_round_robin` fairness layer is **never wired** (`rrf_round_robin.py:88`, only importer is its test); the 002-D MMR ceiling constant **0.98** never reaches runtime (**live DB = 0.88**); rerank cliff floor constant **0.05** vs **live DB 0.2**; the `understand_query` cache `.set()` is **unreachable dead code** (`understand.py:278-293`, boolean-of-function bug); the AdapChunk block-pipeline flag is **default-ON but emits no block-native chunks** (`ingest_stages.py:582-615`).

6. **The comparison flow (weakest correctness, xe 0/4 `[RPT]`) is broken by a 4-link chain, not one bug:** short comparisons denied decompose by an 8-token floor + 0.7 confidence gate (`routing.py:115-132`) → even when decomposed, per-leg synthetic price chunks share one **sentinel `chunk_id`** so dedup drops every leg after the first (`retrieve.py:187-192` + `query_graph.py:2630`) → the un-decomposed path can't fetch entity B because `extract_all_codes` is dead → the fairness layer that would rescue minority-brand chunks is unwired.

7. **HALLU=0 guards can be silently switched off by a fallible intent label.** A factual turn misclassified as chitchat (a documented live failure mode, `generate.py:324-326`) simultaneously drops `<documents>` (`generate.py:707-711`), bypasses the 0-chunk refuse (`generate.py:339-344`), and is excluded from the grounding judge (`guard_output.py:369-375`) — while numeric-fidelity block is observe-only by default (`DEFAULT_NUMERIC_FIDELITY_ACTION='observe'`, `_14:354`). The single load-test HALLU (spa S-005 fabricated phone `0909.999.999`) slips because `numeric_fidelity` **blanks leading-0 phone runs before classification** (`numeric_fidelity.py:52,83`).

8. **The 3-layer RLS apparatus is 100% inert at runtime.** `[DB+CODE]` app connects as **postgres superuser** (`.env:10`, `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`), `rolbypassrls=t` → all **24 policies + FORCE-RLS on 24 tables + SET-LOCAL binder** are bypassed; isolation collapses to app-level `WHERE` filters only (`engine.py:60-81`, `session.py:5-17`). Live query confirms `current_user=postgres`, `ragbot_system` role **does not exist**.

9. **Config governance has a structural blind spot.** The parity pin-test scans **only `query_graph.py` (43 `_pcfg` keys)** while **138 more `_pcfg` sites live in `nodes/*.py`** (`test_pipeline_cfg_keys_parity.py:35,56-64`) → keys like `decompose_stats_max_subs`, `mmr_min_keep` are **pinned to their DEFAULT constant forever** (`_pcfg` has no `system_config` fallback, `query_graph_helpers.py:177`). Four code comments cite a CI guard `scripts/audit_pipeline_cfg_parity.py` **that does not exist**.

10. **Auxiliary workers can die silently and invisibly.** `cost_cap_alerter`/`cache_purge` don't catch `SQLAlchemyError` (`embedded_workers.py:163,203,224`) → a Postgres failover kills them with no log/restart; and `/health` inspects only Postgres+Redis (`health.py:150`), never worker liveness → a dead worker still returns `status='ok'`. A URL-sourced ingest fetches the whole body into RAM with **no size cap** in the shared API process (`document_worker.py:443-448`) → OOM risk to concurrent chats.

**Net honest verdict:** the *skeleton is expert* (Hexagonal/Port+DI, fast BE retrieval path, HALLU-conscious guard design), but **the wiring is unfinished** — several flagship fixes are dead/inert, the latency is dominated by an unhealthy external LLM endpoint plus a sync grounding judge that pays 30s for zero safety, correctness is unmeasurable from the SoR, and RLS defense-in-depth is a facade under superuser.

---

## 2. Per-flow table (evidence-cited)

| Flow | Trạng thái | Sự thật (DB/code) | Điểm che giấu (misleading) | Điểm nghẽn (bottleneck) | Chưa-expert | Fix đúng tầng |
|---|---|---|---|---|---|---|
| **ingest** | cần vá | Retrieval-path fast; ingest correctness gaps. `MAX_DOCUMENT_CONTENT_CHARS=500K` only size bound (`_03:78`) | Block-pipeline flag default-ON but **no block-native chunks** — both branches converge on `smart_chunk(content)` (`ingest_stages.py:582-615,763-775`); stale narrate/CR comments say "ON/True" but constants are **False** (`_11:111`, `_20:81`) | No per-doc **chunk-count cap** → row-atomic sheet ~10k chunks = 10k embeds+rows (`ingest_core.py:377`; embed batch 100 `_04:77`) | Worker route **flattens parser rows to full_text, no raw_bytes** → row-preserve bypass dead; same file chunks differently vs `/sync` (`document_worker.py:460-626` vs `sync.py:558-566`) | Coverage/dropped-number checks **observe-only** (log+metadata, no repair) — silent data loss (`ingest_stages.py:868-899`, comment `:887` "NEVER raises") | Wire block-native chunker OR rename flag; thread `raw_bytes` so ONE canonical funnel; add ingest-time **repair** on coverage-fail (not raise); config-sourced chunk-count guard |
| **retrieve_stats** | cần vá | Comparison 0/4 `[RPT]`; deterministic point-lookup exists but self-sabotages | Docstring `retrieve.py:163-172` claims join fixes comparison-miss it **reintroduces** | — | **Shared sentinel `chunk_id`** → dedup drops all legs after 1st (`retrieve.py:187-192`, `query_graph.py:2630`, `_21:118`); `extract_all_codes` **dead** (`query_range_parser.py:509`, 0 src callers); brand filter narrows wrong brand per leg on same-size compare (`table_shape.py:118-128`, `query_graph.py:2586`); join gated behind `if per_query_chunks` (`retrieve.py:1445-1465`) | Per-leg synthetic id; dedup by content not sentinel; wire `extract_all_codes` for un-decomposed 2-code; hoist stats-join out of the `per_query_chunks` gate; load-test 2-brand `bypass_cache` |
| **rerank_filter** | expert + cần vá | Rerank engine fast (p50 1.49s `[DB]`); config-drift + dead fairness | 002-D MMR 0.98 **inert** (live 0.88), cliff floor 0.05 vs live 0.2 — fresh DB ≠ prod (`_14:235`, `_01:169`; no post-squash alembic seeds them) | — | `rrf_round_robin` fairness **never wired** (`rrf_round_robin.py:88`, only its test); MMR silently flips cosine↔trigram per-batch, unlogged (`mmr.py:117-121`, `mmr_dedup.py:58-64`) | Land 002-D + cliff-floor via **git-tracked alembic** (align constant/init/alembic/live); wire-or-delete `rrf_round_robin` with measured lift; log the MMR space tag (`mmr.py:233` helper unused) |
| **grade_retry** | cần vá + nghẽn | Grade node trivial (p50 0ms `[DB]`); rewrite_retry LLM-heavy | `n_chunks_after` reports **pre-retry** count (`rewrite_retry.py:35`); dead var `has_ambiguous` (`grade.py:448`) | `rewrite_retry` runs full rewrite-LLM+re-retrieve on the already-worst branch, self-documented low-yield (`grade.py:512-516`; `query_graph.py:3007`); rewrite step p50 44.5s/mean 40.7s `[DB]` | Mixed-grade (rel=0, amb>0) **drops correct-but-mislabeled-irrelevant chunk, no rescue** — fallback gated on `all_irrelevant` (`grade.py:449-505,508-533`) | Fire rescue on `not has_relevant` (not only `all_irrelevant`) OR keep-top-1; low-end smart-skip instead of full re-retrieve; rename metadata field |
| **generate** | cần vá + nghẽn | Slowest step; p50 19s, max 114.6s (a FAILED row, 0 output) `[DB]` | Post-hoc top-chunk citation presented as model citation (`generate.py:905-917`) — but does NOT gate any guard (guard_output reads 0 citations) | Structured-output-first → parse-fail returns None **after ≥1 full LLM call**, then a **second** full free-form call (`generate.py:790,849-851`; `structured_output_helper.py:714,729`) | Chitchat misclass drops `<documents>` + bypasses refuse + excluded from grounding, numeric block observe-only (`generate.py:328,339,707-711`; `guard_output.py:369-375`; `_14:354`); output-token cap **flat, not intent-aware** despite `_intent_max_tokens` name (`generate.py:749-752`, `token_budget.py:63-78`); silent length-truncation, no flag | Key guards on **presence-of-context** not intent label; reuse captured raw text on parse-fail (skip 2nd call); intent-aware output cap + `finish_reason=='length'` telemetry |
| **guards** | nghẽn + cần vá | Sync grounding = the masked bottleneck | Timeout→degraded **PASS** bypasses AG-A2 fail_closed (only fires when llm_fn is None) (`local_guardrail.py:522-527`, `guard_output.py:511-523`); stale threshold comment "0.5/0.4/top_score" vs real ratio-cutoff 0.3 (`guard_output.py:352-356`, `_15:105`) | Sync judge on critical path, 30s shared I/O timeout, **31/45 no verdict** `[DB]` (`guard_output.py:689-700`; `DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED=False` `_14:217`) | `numeric_fidelity` **blanks leading-0 phone before classify** (`numeric_fidelity.py:52,83`) → fabricated phone unflaggable; derived-valid **self-pairs** admit 2×grounded & 0 (`numeric_fidelity.py:119-124`); grounding checks only first-5-sentences & ratio **strict >0.3** (`local_guardrail.py:422,454,539`); `empty_answer_guard` OFF → blank ships (`_14:374`); brand_scope hardcodes `À-Ỹ` + magic 48/6 (`brand_scope.py:25,29`, `claim_fidelity.py:36,39`) | Short dedicated grounding timeout (~3-4s) + route timeout→fail_closed OR ship-then-check async; strip only context-present contact numbers; inner slice `i+1`; add absolute-count gate; default empty-guard ON; lift windows to constants |
| **front_orch** | cần vá + nghẽn | understand_query fires 307/307, p50 15.2s `[DB]` | `understand_query` cache `.set()` **dead** (bool-of-function bug) → cache never populates, `had_history` always True (`understand.py:173,269,278-293,306`); legacy router first-substring-match, dead-by-default (`router.py:43-48`); brand literal `trẻ hóa/Ultherapy` in comment (`routing.py:106-110`) | Unconditional ~15s LLM router before generate (slow innocom provider); adaptive_decompose p50 27.8s `[DB]` | Short comparison **denied decompose** by 8-token floor + 0.7 conf gate (`routing.py:111-132`, `_14:107,112`); decomposer prompt over-splits "numbers/names/identifiers" no atomic-token notion (PLAUSIBLE, `query_decomposer.py:60-63`) | Fix cache guard `not _history_meaningful`; bind understand to a **fast** classifier + heuristic short-circuit; exempt comparison/multi_hop from token floor; measure decompose before prompt change |
| **config** | cần vá (governance) | Two builders diverge: demo **209** keys vs worker **198** `[CODE-AST]` | 4 comments cite non-existent CI guard `scripts/audit_pipeline_cfg_parity.py` (`test_chat/_pipeline_config.py:128,715`, `chat_worker/config.py:191`); stale key-counts "200/~30/65" (real 172) | — | Parity test scans only `query_graph.py` → **138 node `_pcfg` sites blind** (`test_pipeline_cfg_keys_parity.py:35`); 9 keys pinned to constant on prod; 2 keys drop `bot_cfg` on demo (`pipeline_config.py:159` vs `test_chat:410`); bool `'on'` = True(demo)/False(worker) (`test_chat:342` vs `config.py:89`) | ONE shared `_build_pipeline_config`; glob nodes/*.py in scanner; delete phantom-script comments → point at real pytest; one shared bool coercion |
| **rls_tenant** | cần vá (security) | RLS **inert** — superuser bypass (`current_user=postgres`, `rolbypassrls=t`) `[DB]` | `session.py:5-17` docstring asserts "3 layers must ALL be live" — layer-2 NOBYPASSRLS role **never connected** (`ragbot_system` role count=0) | — | Workspace GUC never bound on HTTP path → 0141 workspace policies short-circuit to tenant-only (`tenant_context.py:383-429`, latent/double-gated); `stats_index_repository.delete_by_document` **unscoped DELETE**, no SET-LOCAL/predicate (`:251-259`) | Provision `ragbot_app` NOBYPASSRLS, point `DATABASE_URL_APP`, drop superuser escape, startup assertion; bind workspace slug post-BotRegistry; add `record_tenant_id` to stats delete |
| **workers** | cần vá + nghẽn | Aux loops embedded in API process (`embedded_workers.py:1-16`) | `/health` docstring claims worker-liveness probe but `health.py:150` only checks PG+Redis; recovery worker DOES catch SQLAlchemyError → omission inconsistent not intentional (`document_recovery_worker.py:338-341`) | URL fetch `_resp.content` **no size cap** → OOM shared process (`document_worker.py:443-448`); recovery JOIN non-sargable `convert_from(payload)::jsonb->>'document_id'` no index, outbox never GC'd (PLAUSIBLE, `document_recovery_worker.py:179`) | `cost_cap`/`cache_purge` **silent death** on SQLAlchemyError — not in catch tuple (`embedded_workers.py:163,203,224`); replay fresh outbox UUID bypasses inbox dedup, no per-doc lock (PLAUSIBLE, `document_recovery_worker.py:256`) | Add SQLAlchemyError to catch tuples; store tasks on `app.state` + `/health` worker entry; stream URL fetch with byte-ceiling; advisory per-doc lock; outbox GC + indexed `document_id` |
| **perf_vs_correct** | nghẽn (meta) | p50 45.4s / p95 110s / 237>30s; every second in LLM calls `[DB]` | Report tags 3 failures as orchestration/retrieval — DB shows retrieve=success, died at generate infra (`req 6ebc1205/5dc998fc/8ef576a8`); step_order at entry → generate looks "before" its children (`step_tracker.py:117`) | Sync grounding 69% no-verdict; retry 270s; understand 15s/turn (§1) | Correctness **never persisted** (is_correct 0/307) `[DB]`; guard_output.duration double-counts nested grounding (max 30029ms wraps 30000ms); brand literals in 12+ engine comments (8 files) | Persist correctness/HALLU to `request_logs`; derive failure-layer from `request_steps.status` (last non-success); parent_step_id / order-at-exit; scrub brand literals + pre-commit grep |

---

## 3. Correctness vs Performance (never conflated)

### 3A. CORRECTNESS — wrong or missing answer (ranked)

| # | Issue | Flow | Evidence | Verdict |
|---|---|---|---|---|
| C1 | Comparison chain: sentinel `chunk_id` dedup drops all legs after 1st → only entity A's price survives | retrieve_stats | `retrieve.py:187-192`, `query_graph.py:2630`, `_21:118` | CONFIRMED high |
| C2 | `extract_all_codes` (two-entity comparison fix) is **dead code** — un-decomposed path fetches only 1st code | retrieve_stats | `query_range_parser.py:509`, 0 src callers, `retrieve.py:1247,1469` | CONFIRMED high |
| C3 | `rrf_round_robin` minority-brand fairness **never wired** — plain RRF drops comparison minority | rerank_filter | `rrf_round_robin.py:88`, `retrieve.py:1448,1824` | CONFIRMED high (fix-value PLAUSIBLE) |
| C4 | Chitchat misclass drops `<documents>` + bypasses refuse + excluded from grounding → factual turn generated with 0 context AND 0 HALLU guard | generate | `generate.py:328,339-344,707-711`, `guard_output.py:369-375` | CONFIRMED high |
| C5 | Short comparison denied decompose (8-token floor + 0.7 conf gate) → embedded as one diluted vector | front_orch | `routing.py:111-132`, `_14:107,112` | CONFIRMED medium |
| C6 | `numeric_fidelity` blanks leading-0 phone before classify → fabricated phone (the 1 real HALLU) unflaggable | guards | `numeric_fidelity.py:52,83,102,128` | CONFIRMED medium |
| C7 | Mixed-grade case drops correct-but-mislabeled-irrelevant chunk, no rescue (fallback gated on `all_irrelevant`) | grade_retry | `grade.py:296-297,449-505,508-533` | CONFIRMED medium |
| C8 | Worker route flattens parser rows → row-preserve dead → same file chunks differently than `/sync` | ingest | `document_worker.py:460-626`, `ingest_stages.py:763` vs `sync.py:558-566` | CONFIRMED high (consistency) |
| C9 | Brand filter narrows wrong brand per leg on same-size comparison (non-deterministic set iter) | retrieve_stats | `table_shape.py:118-128`, `query_graph.py:2586-2596` (`stats_brand_aware` OFF default) | CONFIRMED medium (latent) |
| C10 | 002-D MMR ceiling 0.98 inert; live 0.88 → aggressive cosine dedup of distinct sections | rerank_filter | `_14:235` vs live DB 0.88, `mmr_dedup.py:35-48` | CONFIRMED medium |
| C11 | derived-valid self-pairs admit 2×grounded & 0 → fabricated round value coincidentally validates | guards | `numeric_fidelity.py:119-124` | CONFIRMED medium |
| C12 | Lossless-coverage/dropped-number checks observe-only → silent data loss at ingest | ingest | `ingest_stages.py:868-899` (comment `:887` "NEVER raises") | CONFIRMED medium |
| C13 | decompose stats-join gated behind `if per_query_chunks` → both legs empty ⇒ refuse despite corpus having rows | retrieve_stats | `retrieve.py:1445-1465,1499-1500` | CONFIRMED low |
| C14 | Grounding judge only first-5-sentences + strict ratio>0.3 → sentence-6 or 1-of-4 (0.25) never flagged | guards | `local_guardrail.py:422,454,539`, `_15:105` | CONFIRMED low |
| C15 | `empty_answer_guard` OFF → blank/whitespace generation ships to user | guards | `guard_output.py:99-101`, `_14:374` (DB: 7 empty shipped) | CONFIRMED low |
| C16 | MMR silently flips cosine↔trigram per batch (one embedding-less lexical chunk downgrades whole batch); same 0.88 = two behaviours | rerank_filter | `mmr.py:117-121`, `pg_bm25_retrieval.py` (no embedding) | CONFIRMED low |

### 3B. PERFORMANCE — latency / resource / bottleneck (ranked, never wrong-answer)

| # | Issue | Flow | Evidence (DB + code) | Verdict |
|---|---|---|---|---|
| P1 | Sync grounding judge on critical path, 30s shared timeout, **31/45 (69%) no verdict** — pays full latency, zero HALLU safety | guards/perf | `guard_output.py:689-700`, `_14:217`; DB status='running' 31/45 @30001ms | CONFIRMED high |
| P2 | `retry(3) × 90s` innocom → 270s worst-case; **all 8 failures = retry-exhausted innocom 5xx** (output_tokens=0) | perf | `_04:180`, `dynamic_litellm_router.py:744-752`, alembic `..._90s.py:31`; DB 8 failed | CONFIRMED high |
| P3 | `understand_query` unconditional ~15s LLM every turn; cache can't hit on distinct traffic | front_orch/perf | DB 307/307 p50 15.2s; `query_graph.py:1000,1693` | CONFIRMED high |
| P4 | URL-fetch `_resp.content` no size cap → OOM shared API process kills concurrent chats | workers | `document_worker.py:443-448`; PDF cap runs AFTER materialize (`pdf_parser.py:70`) | CONFIRMED high |
| P5 | Structured-output-first → parse-fail = **two full LLM generations** on the slowest step | generate | `generate.py:790,849-851`, `structured_output_helper.py:714,729` | CONFIRMED medium |
| P6 | No per-doc chunk-count cap → row-atomic table fans out to ~10k embeds+rows | ingest | `_03:78`, `ingest_core.py:377`, `__init__.py:477,486` | CONFIRMED medium |
| P7 | `rewrite_retry` full re-retrieve+rewrite-LLM on the already-worst branch, self-doc low-yield | grade_retry | `query_graph.py:3007`, `grade.py:512-516`; DB rewrite p50 44.5s | CONFIRMED low |
| P8 | Recovery JOIN non-sargable `convert_from(payload)::jsonb->>'document_id'`, no index; outbox never GC'd | workers | `document_recovery_worker.py:179`, `models.py:428-429` | PLAUSIBLE low |

---

## 4. Top 10 issues by (severity × blast-radius)

> Scored on severity × how many requests/tenants/formats it touches. Each: layer + `file:line` + expert fix.

**#1 — RLS is 100% inert (superuser bypass).** `[SECURITY / all tenants]`
Layer: **infra/DB provisioning**. `engine.py:60-81`, `.env:10` + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, `_17:12-13`; live `rolbypassrls=t`, `ragbot_system` role absent. 24 policies + FORCE-RLS + binder all bypassed → single-layer app-`WHERE` isolation only.
Fix: provision `ragbot_app` NOBYPASSRLS, point `DATABASE_URL_APP` at it, delete the superuser escape from prod, add a startup assertion (`current_user` non-super AND NOT `rolbypassrls` when `APP_ENV!=dev`) + an integration test proving a cross-tenant SELECT returns 0 rows.

**#2 — Sync grounding judge: 30s on critical path, 69% no verdict.** `[PERF / every factoid request]`
Layer: **guards/orchestration**. `guard_output.py:689-700`, `DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED=False` `_14:217`; DB 31/45 `'running'` @30001ms. Worst of both worlds — pays ~30s, ships UNVERIFIED (timeout→degraded PASS bypasses AG-A2 fail_closed).
Fix: dedicated short timeout (~3-4s vs judge p50≈0.8s) with **deterministic fail-closed** on timeout, OR default the background ship-then-check lane ON for factoid so the judge never blocks.

**#3 — Correctness/HALLU never persisted to the SoR.** `[MEASUREMENT / all claims]`
Layer: **observability/eval pipeline**. DB `is_correct` 0/307, `quality_evaluator` 0/307, `refusal_reason` 0/302; verdict only structlog (`query_graph.py:905,916`). The 93%/HALLU=1 baseline is `[RPT]`-only — violates measure-before-claim (rule #0).
Fix: write per-request correctness/HALLU + grounding_async_breach/pass into `request_logs.is_correct/quality_evaluator/quality_evaluated_at` (or a dedicated eval table) from the grader path.

**#4 — retry(3)×90s = 270s; 8 infra failures mislabeled as logic.** `[PERF+correctness-tag / P99 + failure taxonomy]`
Layer: **LLM router + dashboard classifier**. `_04:180`, `dynamic_litellm_router.py:744-752`, alembic `..._90s.py:31`; DB all 8 = innocom InternalServerError, last step `litm_order`, `output_tokens=0`.
Fix: hard wall-clock deadline (`asyncio.wait_for` over the whole retry sequence) + fewer foreground attempts (1-2); keep aggressive retries on background lane only; **derive failure layer from `request_steps.status` (last non-success step), not question shape** — re-tag these as generate/provider-infra.

**#5 — understand_query: 15s unconditional LLM router on every turn.** `[PERF / all 307 requests]`
Layer: **front_orch / model binding**. DB 307/307 p50 15.2s; two sequential LLM calls (understand→generate) drive the ~45s p50. Driven by the slow innocom provider, not a cache bug (1/307 hit is correct on distinct traffic).
Fix: bind `understand_query` to a **fast small classification model** (not the generate provider) + a cheap heuristic pre-router that short-circuits trivial/greeting/short queries before any LLM call.

**#6 — Comparison chain broken (xe 0/4).** `[CORRECTNESS / comparison intent]`
Layer: **retrieve_stats + front_orch routing**. Sentinel dedup `retrieve.py:187-192` + `query_graph.py:2630`; dead `extract_all_codes` `query_range_parser.py:509`; 8-token/0.7 decompose gate `routing.py:111-132`; unwired `rrf_round_robin.py:88`.
Fix (one coherent pass): per-leg distinct synthetic ids (or dedup by content) + wire `extract_all_codes` for the un-decomposed 2-code path + exempt comparison/multi_hop from the token floor + measure the G-095/097/098 set `bypass_cache` before/after. If measured lift=0, delete the dead modules instead of keeping them.

**#7 — Chitchat misclass disarms all HALLU guards + drops context.** `[CORRECTNESS/HALLU-sacred / any misclassified factual turn]`
Layer: **generate + guards**. `generate.py:328,339-344,707-711`; `guard_output.py:369-375`; misclass is documented-live (`generate.py:324-326`).
Fix: key guard behavior on **presence-of-context, not intent label** — drop `<documents>` only when `graded` is empty; make grounding eligible whenever context chunks are present; tighten the refuse-bypass to chitchat AND empty-graded only.

**#8 — URL-fetch OOM risk in shared API process.** `[AVAILABILITY / whole process + concurrent chats]`
Layer: **workers/ingest**. `document_worker.py:443-448` `_resp.content` no `max_bytes`/stream; PDF 10MB cap runs after materialize (`pdf_parser.py:70`); workers embedded in API (`embedded_workers.py:1-16`).
Fix: `cli.stream('GET', …)` with Content-Length precheck + `aiter_bytes()` accumulation aborting past a config-sourced ceiling (`DEFAULT_MAX_BODY_INGEST_BYTES` / new `DEFAULT_URL_FETCH_MAX_BYTES`) before full materialization.

**#9 — Config parity blind spot + phantom CI guard.** `[GOVERNANCE / 138 unconfigurable knobs, silent drift]`
Layer: **config governance/tests**. `test_pipeline_cfg_keys_parity.py:35,56-64` scans only `query_graph.py` (43 of 181 `_pcfg` sites); `_pcfg` has no `system_config` fallback (`query_graph_helpers.py:177`); 4 comments cite non-existent `scripts/audit_pipeline_cfg_parity.py`; builders diverge 209 vs 198.
Fix: glob `query_graph.py + orchestration/nodes/*.py` in the scanner; collapse to ONE shared `_build_pipeline_config`; delete phantom-script comments → point at the real pytest.

**#10 — Aux workers die silently + invisibly.** `[AVAILABILITY/COST-BLINDNESS / cache GC + cost-cap]`
Layer: **workers + health**. `embedded_workers.py:163,203,224` omit `SQLAlchemyError` (recovery worker includes it `document_recovery_worker.py:338-341` — inconsistent); `/health` sees only PG+Redis (`health.py:150`), tasks never on `app.state`.
Fix: add `SQLAlchemyError` to inner + `_supervise` catch tuples (log+continue), store tasks on `app.state`, add a `workers` entry to `/health` that flips to degraded on `task.done() and task.exception()`; pair with backoff-restart.

*(Runners-up just outside top-10: MMR/cliff-floor config drift not seeded post-squash (fresh DB ≠ prod), structured-output double-call on the slowest step, numeric_fidelity phone-strip HALLU escape, ingest coverage observe-only silent data loss.)*

---

## 5. Where the platform is ALREADY expert (honest)

1. **Retrieval BE path is genuinely fast and clean.** `[DB]` retrieve p50 **18ms**, rerank p50 **1.49s**, grade p50 **0ms**, rrf/mmr/litm/citations/persist all **≤48ms**. No DB bottleneck — `hybrid_search` + BM25 fusion + zeroentropy rerank + CRAG grade total ≈2s. The "engine is slow" narrative is false; the LLM calls are slow.

2. **Failure isolation is correct — 0 failures are logic/retrieval crashes.** `[DB]` all 7-8 `failed` rows died at the external generate call with retrieval/rerank/order already `success`. The orchestration graph itself did not error on a single request.

3. **HALLU containment held in practice: 1/200 `[RPT]`, xe 0 absolute, trap 15/15, price_lookup 42/42.** The anti-fabrication design (grounding judge + numeric-fidelity + brand-scope + claim-fidelity detectors) is real and multi-layered — the gaps found are edge-cases (phone-strip, self-pairs, ratio dilution), not an absent guard.

4. **Architecture is honestly Hexagonal/Port+DI as claimed.** `_pcfg` config-chain, Strategy registries, Null-Object defaults, 4-key identity, and the strangler-fig evolve-not-rewrite posture are intact. No per-bot hardcode in executable paths — brand literals are confined to comments/docstrings (a hygiene issue, not a logic coupling).

5. **Cost efficiency is strong.** `[DB]` 302 req = **$0.808 total**, 6397 tok/req, **avg 2.17 LLM-steps/req** (simple ~2, complex 4-5). The pipeline is not over-calling the LLM; the problem is per-call *latency*, not call *count*.

6. **The recovery worker and outbox/inbox exactly-once machinery are well-built** where they matter — `document_recovery_worker.py:338-341` correctly catches `SQLAlchemyError` (proving the aux-worker omission is an oversight, not the norm), and the answer-critical consumer/outbox loops use broader, correct handling than the two auxiliary GC loops.

7. **The config governance *intent* is expert** — a 172-key single-batched `get_many()`, resolve-chain (column > plan_limits > system_config > default), and a parity pin-test all exist. The defect is coverage (scanner scope), not a missing discipline.

---

## 6. Recommended fix order (T1 smartness > T2 cost/perf > T3 pattern)

### T1 — Smartness / correctness / HALLU (do first)
1. **Persist correctness/HALLU to the SoR** (Top-10 #3). *Precondition for everything else* — you cannot claim any lift under rule #0 until `is_correct`/grounding verdict are DB-queryable. `request_logs` columns already exist.
2. **Comparison chain, measured** (Top-10 #6): per-leg distinct synthetic ids + wire `extract_all_codes` + exempt comparison from token floor. Load-test G-095/097/098 `bypass_cache` before/after; delete dead modules if lift=0. This is the single weakest correctness area (0/4).
3. **Decouple HALLU guards from intent label** (Top-10 #7): guard on presence-of-context, not chitchat classification. Protects the HALLU=0 sacred invariant against the documented misclass failure mode.
4. **Close the guard escape hatches**: numeric_fidelity context-scoped phone strip + derived-valid `i+1` slice + default `empty_answer_guard` ON. Directly addresses the one observed HALLU class.
5. **Mixed-grade rescue** (`grade.py`): fire fallback on `not has_relevant`. Then re-measure spa coverage (S-039/046) — *note the S-075 confound: it died at infra, not grade* (`req 8ef576a8`).

### T2 — Cost / perf / UX (do second — this is where the user pain is)
6. **Sync grounding judge → short timeout + fail-closed OR background lane** (Top-10 #2). Highest single latency lever: eliminates ~30s of dead wait on 69% of judged requests.
7. **understand_query → fast classifier + heuristic short-circuit** (Top-10 #5). Removes ~15s floor on every turn.
8. **Bound the retry deadline + healthier innocom endpoint** (Top-10 #4). Kills the 176s/185s tails and the 8 failures; re-tag the failure classifier off `request_steps.status`.
9. **Structured-output parse-fail: reuse captured raw text** (P5) — removes one full generation on the slowest step in the common case.
10. **URL-fetch streaming byte-cap** (Top-10 #8) — availability; prevents OOM taking concurrent chats.

### T3 — Pattern / governance / hygiene (do last)
11. **RLS de-inert** (Top-10 #1): provision NOBYPASSRLS role + `DATABASE_URL_APP` + startup assertion. *Security-critical but currently mitigated by app-level WHERE filters* — schedule deliberately, with the workspace-GUC bind and stats-delete scoping landed in the same pass so RLS doesn't fail-closed on go-live.
12. **Config: one shared builder + scanner glob nodes/*.py + delete phantom-script comments** (Top-10 #9). Then the 9 pinned keys and the demo/worker `bot_cfg`/bool-`on` divergences surface as test failures and get wired.
13. **Land MMR-0.98 / cliff-floor-0.2 via git-tracked alembic** (align constant/init/alembic/live per no-psql-hotfix); **wire-or-delete `rrf_round_robin`** with measurement.
14. **Ingest hygiene**: thread `raw_bytes` for ONE canonical funnel (kill the route-dependent chunking), coverage-fail → repair not observe-only, per-doc chunk-count cap, fix the narrate/CR stale comments.
15. **Observability/hygiene**: aux-worker `SQLAlchemyError` catch + `/health` worker entry; `parent_step_id`/order-at-exit; scrub brand literals from 12+ engine comments + add pre-commit grep; rename `_intent_max_tokens`/`n_chunks_after`; delete dead `has_ambiguous`.

**Governing principle throughout:** the framework is expert — do not rewrite it. Every T1/T2 fix must be **measured before claimed** (rule #0), and several "fixes" here are literally *deleting dead code or wiring existing dead code*, not new abstraction. The fastest wins for the owner are T2 #6–#8: they don't touch a single answer, and they cut p50 from ~45s toward the ~2s the BE path actually costs.