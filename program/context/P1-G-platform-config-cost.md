# P1-G — PLATFORM / CONFIG / OBSERVABILITY / COST (Phase 1 context absorption)

Read-only context map. Every claim = `file:line` or commit. No judgement — UNDERSTAND only.
Anchor commit: `7dd1f84`. Alembic head: `0195`. Branch: `fix-260604-action-slotmachine-dead-key`.

---

## (a) CONFIG ARCHITECTURE

### a.1 — constants package: 22-module map

Split from monolith `constants.py` 4762 lines → package, commit `1446fef` ("split constants.py 4762 → constants/ package (22 modules ≤735 lines)"). **Structural fact (verified)**: the split is a *mechanical daisy-chain*, NOT a domain split — each module does `from ._NN_prev import *` (e.g. `_01_http_db_client_construction_.py:3`), and `__init__.py` re-exports everything so `from ragbot.shared.constants import X` is unchanged (`constants/__init__.py:1-3`). Module names = the feature that happened to start that slice (some carry date refs, e.g. `_17_260509_...` — borderline vs no-version-ref rule).

| Module | consts | Owns (first symbols) |
|---|---|---|
| `_00_app_env_taxonomy.py` (204L) | 60 | APP_ENV_* taxonomy (development/uat/staging/production) |
| `_01_http_db_client_construction_.py` (252L) | 73 | HTTP/DB client timeouts, health-models probe |
| `_02_per_intent_rerank_skip_gate_.py` (181L) | 40 | RERANK_SKIP_INTENTS, embedding/metadata model names |
| `_03_language_packs_db_driven_pro.py` (165L) | 48 | LANGUAGE_PACK cache prefix + prompt keys, multi-query prompt keys |
| `_04_jwt_auth.py` (163L) | 53 | JWT TTL, dev-token gates |
| `_05_embedding_circuitbreaker.py` (154L) | 60 | embedder CB fail-max/reset, concurrency, CB policy |
| `_06_llm_defaults.py` (150L) | 48 | LLM max-tokens (answer/generation/metadata), temperature |
| `_07_llm_sampling_defaults.py` (121L) | 40 | sampling temp, streaming word-delay, chat stream timeout |
| `_08_sentry_otel.py` (143L) | 48 | Sentry sample rate, OTEL knobs, circuit-breaker fail-max |
| `_09_message_feedback_thumbs_verd.py` (735L — biggest) | 61 | feedback verdicts, comment length, aggregate windows |
| `_10_rbac.py` (280L) | 73 | RBAC numeric levels (super/tenant/admin), cache TTL |
| `_11_table_csv_chunking_strategy.py` (200L) | 54 | CSV/table detect heuristics (comma-ratio, run-min-lines) |
| `_12_multi_stage_retrieval_fallba.py` (226L) | 53 | multistage retrieval stages, early-exit threshold, BM25 multiplier |
| `_13_adapchunk_layer_1_ocr_parser.py` (223L) | 55 | parser engine keys (kreuzberg/docling), AdapChunk L1 |
| `_14_anti_abuse_ip_rate_limit_hon.py` (279L) | 77 | anti-abuse IP ban, auth-fail threshold/window, honeypot |
| `_15_m2_neighbor_window_expansion.py` (237L) | 29 | neighbor expand window/budget/concurrency |
| `_16_prompt_token_squeeze_phase_b.py` (220L) | 39 | prompt-token-opt min-chunk-score, dedupe Jaccard |
| `_17_260509_a1_pipeline_audit_6_c.py` (155L) | 32 | proximity-cache LSH, RAGAS stub score, self-RAG skip intents |
| `_18_admin_all_tenants_analytics_.py` (183L) | 34 | analytics all-tenants limit/window |
| `_19_sprint3_ekimetrics_selector_.py` (154L) | 31 | ekimetrics selector BI/DCC thresholds |
| `_20_cag_mode_cache_augmented_gen.py` (281L) | 47 | CAG mode/provider, cleanbase tier-0, doc-profile analyzer |
| `_21_streaming_upload_wb_2_p1_5.py` (140L) | 19 | streaming upload max-bytes/chunk-size/temp-dir, NATS-style subject |

Total ~1,074 exported constants / 4,884 lines.

### a.2 — Resolve chain (the "6-tier")

`resolve_bot_limit(bot_cfg, key, system_default)` — `src/ragbot/shared/bot_limits.py:376-456`, highest→lowest:

1. `bot_cfg.threshold_overrides[key]` (JSONB, Stream V Phase 2) — `bot_limits.py:410-411,423`
2. `bot_cfg.<key>` dedicated column (hot-path: `max_documents, max_history, prompt_max_tokens, rerank_top_n` — `_COLUMN_KEYS` `:373`) — `:404-406`
3. `bot_cfg.plan_limits[key]` (JSONB, validated/clamped by `validate_plan_limits` `:515`) — `:414-415`
4. `system_default` (caller passes the `system_config` row) — `:420`
5. `PLAN_LIMIT_SCHEMA[key]["default"]` (mirrors a `DEFAULT_*` constant) — `:419-420`
6. (constants fallback when key not in schema — `DEFAULT_*` passed by caller, e.g. `query_graph.py` `_pcfg(state, key, DEFAULT_X)`)

That is **5 tiers in the resolver + constants as ultimate caller-side fallback**; MEMORY/charter "6 tầng" — the 6th (workspace/tenant layer) exists only as a comment `chat_worker.py:1410` *"bot.col -> plan_limits -> workspace_config -> tenants ->"* — workspace_config is referenced-but-thin. **Range guard**: numeric bot values outside `schema.min/max` REJECTED with `bot_limit_out_of_range_rejected` warn (`bot_limits.py:429-448`) → fall back. `PLAN_LIMIT_SCHEMA` = ~50 keys (`bot_limits.py:51-361`).

**Keys that BYPASS the chain (operator-global)**: `max_total_graph_iterations`, `max_grade_retries`, `graph_recursion_limit` etc. are NOT in `PLAN_LIMIT_SCHEMA` — read via `_pcfg` from `state["pipeline_config"]` which the worker fills from **system_config only** (`chat_worker.py:1070,720,927-947`). Tenant cannot override them; but the overridable/global boundary is **implicit** (in-schema vs not), no audited whitelist.

### a.3 — The 4-place config-drift hazard (charter's "đổi 1 default = sync 4 chỗ")

4 places a default lives:
1. `src/ragbot/shared/constants/` (`DEFAULT_*` SSoT)
2. `alembic/versions/20260417_0020_seed_system_config.py` (`SEED_CONFIGS`, "migration is source of truth")
3. `scripts/init_system_config.py` (duplicate seed list, claims to mirror 0020)
4. `src/ragbot/shared/bot_limits.py::PLAN_LIMIT_SCHEMA` defaults (`:48-361`)

**DRIFT-1 (concrete, verified 2026-06-10) — init script has DIVERGED from alembic 0020:**

| key | alembic 0020 | init_system_config.py | drift |
|---|---|---|---|
| `llm_default_max_tokens` | `"450"` (`...0020...py:24`) | `"1024"` (`init_system_config.py:30`) | **2.3×** |
| `rag_rerank_top_n` | `"5"` (`:32`) | `"10"` (`:38`) | **2×** |
| `rag_top_k` | literal `"20"` (`:31`) | `str(DEFAULT_TOP_K)` (`:37`) | source mismatch (literal vs import) |

Init script also seeds keys absent from 0020 (`bm25_normalization_flags`, `bm25_use_cover_density`). Fresh DB seeded via script ≠ via migration.

**DRIFT-2** — alembic 0020 freezes string literals (`"rag_top_k": "20"`, `"pipeline_cache_similarity_threshold": "0.97"`), so changing `DEFAULT_TOP_K` in constants does NOT propagate to a migration-seeded row (migration-freeze is legitimate, but undocumented as a hazard).

**DRIFT-3** — later alembics UPSERT over 0020 (0057, 0067, 0068, 0085, 0190/0191): live value of any key = 0020 XOR latest UPSERT — no single file shows current `system_config` state.

**Lint coverage is narrow**: `scripts/audit_config_key_drift.py` checks only 2 hardcoded `_SUSPECT_PAIRS`; `scripts/validate_constants.sh:18` still targets `src/ragbot/shared/constants.py` — **a file that no longer exists post-split `1446fef`** (guard exits on "not found", silently useless). No 4-way value-equality lint exists.

### a.4 — system_config access layer

`system_config_service.py` — Redis-cached read; writes emit `system_config.changed.v1` outbox rows for cross-replica cache invalidation (`system_config_service.py:62-66`). Worker pulls chat keys in bulk: `_cfg = await _cfg_svc.get_many(_CHAT_CONFIG_KEYS)` (`chat_worker.py:720`).

---

## (b) OBSERVABILITY MAP

**Stack**: structlog in **216 files** (`grep -rl structlog src/ragbot | wc -l` = 216). Prometheus via Port+Adapter: `application/ports/metrics_port.py` + `infrastructure/observability/prometheus_metrics_adapter.py` (`step_duration_seconds.labels(step_name=...)` `:27`). OTEL: `infrastructure/observability/tracing.py` — opt-in `OTEL_ENABLED=true` (`tracing.py:49-50`), silent no-op if packages missing. Sentry knobs in `constants/_08_sentry_otel.py`. Extra adapters: `sla_metrics.py`, `p99_outlier.py`, `pipeline_audit_logger.py`, `invocation_logger.py`.

**request_steps instrumentation — CURRENT COUNT: 33 distinct chat-pipeline step names in `query_graph.py`** (verified `grep step\(" | sort -u` = 33):
`adaptive_decompose, cache_check, citations_extract, condense_question, critique_parse, decompose, filter_min_score, generate, grade, graph_retrieve, grounding_check, guard_input, guard_output, litm_order, mmr_dedup, multi_query_fanout, multistage_retrieval, neighbor_expand, persist, prompt_build, prompt_compression, query_complexity, reflect, rerank, retrieve, retrieve_fallback, rewrite, rewrite_retry, router, router_select_model, rrf_fuse, semantic_cache_check, understand_query`.
→ The MEMORY note "12 live / 15 NOT_INSTRUMENTED" (2026-04-30) is **OBSOLETE** — all top-5 previously-missing (`prompt_build, citations_extract, multi_query_fanout, rrf_fuse, litm_order`) now present. `request_steps` written via `request_log_repository.py:278,337`; StepTracker at `application/services/step_tracker.py`.

**Ingest steps** (`document_service.py:141 INGEST_STEP_NAMES`, kind=`ingest`): `cleanbase_tier0_scrub, adapchunk_b2_block_pipeline, adapchunk_l3_profile, late_chunking_sliding` + `bkai_vn_embed` (`bkai_vn_embedder.py:313`) + `narrate_{block_type}` / `narrate_batch_submit` / `narrate_batch_fetch`.

**Still NOT instrumented:**
- `semantic_cache.py` internals use `_NULL_STEP_CTX` (`:103,403`) — no timing on that path.
- CAG step is **commented out** in 3 files (`cag_service.py:140`, `null_cag.py:69`, `anthropic_cag.py:150` — `step_name="cag_lookup"` only in comments).
- `purpose` dimension absent from DB everywhere (see (c) GAP-1).

---

## (c) COST / EVAL BASELINE — latest measured state (objective)

### c.1 — GRADED eval 2026-06-10 (the baseline the charter's 6 axes compare against)

Source: `reports/GRADED_LATEST_20260610.txt` (GRAND TOTAL line) + `reports/GRADED_SUMMARY.json`. Harness: `scripts/loadtest_graded.py` — 3 runs/question, bypass_cache, DB ground-truth verify, LLM-judge `gpt-4.1-mini` (`loadtest_graded.py:36`), semaphore-parallel, attribution layer per GRADING_SOP.

**GRAND TOTAL: 85/91 PASS (93.4%) · 5 flips · 6 test-data issues** (`GRADED_LATEST_20260610.txt` last lines; matches commit `7dd1f84` "85/91 (revert confirmed)").

| Bot (archetype) | pass/n | flip | failures (layer) |
|---|---|---|---|
| dia-ly-vn (academic) | 7/7 | 4 | — (flips = RETRIEVAL gold-fact-not-in-chunks, still pass 2/3) |
| hoa-hoc-10 | 7/7 | 0 | — |
| kinh-te-vi-mo | 7/7 | 0 | — |
| lich-su-vn | 7/7 | 0 | — |
| luat-giao-thong (legal) | 7/9 | 0 | L3-2 + L4-1 = MODEL/SCAFFOLD (compute) — facts retrieved, computation wrong (isolation: facts OK at L1 → GENERATION/SCAFFOLD) |
| sinh-hoc-12 | 7/7 | 0 | — |
| test-spa-id (commercial) | 7/11 | 1 | L2-2 GENERATION (21-step recital wrong); L3-2 compute; L3-3 + L4-1 CORPUS-GAP/TEST-DATA (`'299.000'` not in DB) |
| thong-tu-09-2020-tt-nhnn | 8/8 | 0 | — |
| tin-hoc-co-ban | 7/7 | 0 | (1 test-data issue, still pass) |
| toan-hoc-12 | 7/7 | 0 | — |
| vat-ly-11 | 7/7 | 0 | — |
| y-te-co-ban | 7/7 | 0 | — |

**HALLU**: 0 fabrication — none of the 6 failures carries a fabrication layer (`GRADED_SUMMARY.json`); judge tracks `fabricated` explicitly; `GRADED_FINDINGS_20260609.md:32`: "HALLU = 0: mọi câu L0 OOS bot từ chối đúng, không bịa". All 12 L0 refusal traps pass.
**p95**: NOT measured by this harness (no latency field in GRADED output) — charter axis "NHANH" has **no current measurement** at this baseline; last known p95 ≈ 17-22s from earlier 90Q campaigns (MEMORY, pre-dates this branch). CHƯA verify — cần latency-instrumented run.
**Failure signature of the remaining 6.6%**: arithmetic/aggregation across 2+ retrieved facts (4 of 6) + corpus-gap (2 of 6) — i.e., not retrieval, not hallucination: **compute scaffold** is the open axis.

### c.2 — Cost-attribution gaps (what the platform can/can't answer)

**Measured**: `request_logs` 1 row/turn with `record_tenant_id` NOT NULL (`models_monitoring.py:92`), `record_bot_id` (`:96`), `prompt/completion/total_tokens`, `cost_usd` (`:124-127`), model/binding ids; finalized `request_log_repository.py:90-138`. `TenantTokenMeter` Redis hash `tokens:tenant:{uuid}:{YYYY-MM}` for monthly cap (`tenant_token_meter.py`). Prometheus `purpose` label on cache/failover counters (`dynamic_litellm_router.py:472,479`).

- **GAP-1 (worst)** — `purpose` NEVER persisted to DB: `complete_runtime(..., purpose="unknown")` (`dynamic_litellm_router.py:431`) → Prometheus/log only; no `purpose` column anywhere in `models_monitoring.py`. One turn = up to ~10 LLM calls; `request_logs.total_tokens` is the aggregate. **Cost-per-pipeline-stage per-tenant is NOT recoverable from DB.**
- **GAP-2** — TenantTokenMeter keys tenant+month only (`tenant_token_meter.py:84`): no per-bot, no per-purpose.
- **GAP-3** — meter increment silent-skips when no tenant contextvar (`dynamic_litellm_router.py:307-332`, no-op at `:322-325`; errors swallowed `logger.debug` `:331`) → background/system LLM spend un-metered.
- **GAP-4** — ingest LLM spend (CR enrichment, narrate Haiku batch `anthropic_haiku_batch.py:174,203`, metadata extraction) writes only `request_steps` timing, no `cost_usd` row → ingest cost outside the per-tenant ledger.

---

## (d) PLAN × STATUS × EVIDENCE — all 27 plans (the orphan-plan source for SYNTHESIS)

27 = 21 dated dirs + 6 top-level docs (`ls plans/`). Status from each plan's `**Status**` line; cross-checked vs code/alembic where a concrete artifact was claimed.

| # | Plan | Tier | Stated status | Code evidence (verified) | Verdict |
|---|---|---|---|---|---|
| 1 | `260603-handoff-phase-D-ga-hardening/HANDOFF.md` | GA | handoff (6 sub-agents D1-D6) | PII universal still default-OFF (`bot_limits.py:117`) | **DOING/partial** |
| 2 | `260604-bm25-vietnamese-aware` | T1+T2 | ⏳ DRAFT | superseded by #6 (self-declared) | **ABANDONED (superseded)** |
| 3 | `260604-deepaudit-rootcause-fix` | T1+T2 | ⏳ IN PROGRESS | F1/F4 wired — `models.py:201,205`, `bot_config.py:131,134`, `bot_repository.py:107` | **DOING (F1/F4 shipped)** |
| 4 | `260604-expert-rag-action-architecture` | T1-T3 | ⏳ DRAFT | L2 action_config DTO+ORM wired; L3 tool-use exec adapter = 0 grep hits | **DOING (L2 only, L3 ORPHAN)** |
| 5 | `260604-fix-2bot-verify` | T1+T2 | (anchor `81becf6`) | alembic 0165/0166 exist | **DONE (scoped)** |
| 6 | `260604-metadata-aware-v4` | T1+T2 | ⏳ DRAFT | superseded by #7 | **ABANDONED (superseded)** |
| 7 | `260604-multi-domain-metadata-aware` | T1+T2 | ✅ SHIPPED `81becf6` | alembic 0162 + `generic_llm_extractor` registry; plan §2 (40 regex) explicitly REJECTED in real ship | **DONE (§2 = road-not-taken, DIVERGENCE recorded)** |
| 8 | `260605-booking-leak-facts-to-corpus` | T1 | ✅ FIXED | alembic 0176 + 0177 | **DONE** |
| 9 | `260605-generation-discipline-fix` | T1 | ⏳ implementing | alembic 0173; folded into #10 | **DOING (folded)** |
| 10 | `260605-multistep-quality-master` | T1-T3 | ⏳ partial | RC-1/RC-2 done (0173/0174); RC-3 retrieval PENDING; RC-4 handler-2 pending | **DOING (≥3 RCs open)** |
| 11 | `260605-rag-full-fix-master` | T1+T2 | ⏳ in-flight | F2 alembic 0182 landed; F1/F3 partial | **DOING** |
| 12 | `260605-rag-hardquery-rootcause-fix` | T1-T3 | ⏳ DRAFT | Group-1 throughput fixed (semaphore/bulkhead); Phases 1-5 pending | **DOING (Group-1 only)** |
| 13 | `260605-rc4-generation-discipline-fix` | T1 | ⏳ | alembic 0178 exists | **DONE (0178)** |
| 14 | `260608-multiagent-fix-retest` | T1 | (anchor `2ca79d9`) | alembic 0186-0189 all present | **DONE/DOING (merged)** |
| 15 | `260608-multitenant-hardening` | T1-Sec | Phase 0 only | alembic 0186/0187 EXIST but `attach_rls_session_hook` (`session.py:154`) **0 callsites** (grep: only def+__all__+docstring) | **DOING (migrations landed, hook NOT wired)** |
| 16 | `260608-path-to-9.5-expert` | T1+T2 | ⏳ planned | Phase 0a/0b not verified shipped | **DRAFT/planned (ORPHAN-risk)** |
| 17 | `260608-rag-quality-rootcause` | T1 | (anchor `14ec96d`) | `scripts/multistep_ragas_report.py` exists; rest staged | **DOING** |
| 18 | `260609-file-size-reduction` | T3 | (anchor `bf5b77f`) | constants split DONE (`1446fef`, 22 modules); `query_graph.py` STILL 8087L, `document_service.py` 4104L (wc verified) | **DOING (1 of 3 targets)** |
| 19 | `260609-query-graph-split` | T3 | (anchor `bf5b77f`) | query_graph.py 8087 lines unchanged — split NOT executed | **ORPHAN (plan written, code absent)** |
| 20 | `260609-prod-test-framework/FRAMEWORK.md` | test-infra | L0-L5 taxonomy | `loadtest_graded.py` + `GRADING_SOP` + GRADED_* reports = live | **DONE (in active use)** |
| 21 | `260610-ga-hardening` | Sec+T1 | active | ISSUE-1 RLS 0-callsite confirmed; T1 tie-break shipped `6547fb6` then REVERTED `2f5ed41` (graded A/B verdict `7dd1f84`) | **DOING (active branch plan; 1 reverted experiment)** |
| 22 | `260506-MASTER-BACKLOG.md` | meta | backlog | scaffolding doc | meta (not a feature plan) |
| 23 | `DEFERRED_STREAMS.md` | meta | deferred registry | — | meta |
| 24 | `MASTER_CODER_PROMPT.md` | meta | agent prompt | — | meta |
| 25 | `PLAN_V0_CHANGELOG.md` | meta | changelog | — | meta |
| 26 | `RESUME_KIT_V9_1.md` | meta | session resume | — | meta |
| 27 | `ROADMAP_V2.md` | meta | roadmap 2026-04-30 | partially superseded by program/ charter | meta (stale) |

**Orphan/divergence summary**: 2 ABANDONED-superseded (#2, #6) · **3 true orphans/orphan-risk** (#4 L3 action-execution, #19 query-graph-split, #16 path-to-9.5) · ~9 DOING partial-ship (most common state) · 5 DONE · 1 DIVERGENCE recorded (#7 §2 regex draft rejected at ship) · 1 reverted experiment (#21 tie-break). **Recurring meta-pattern (the plans name it themselves): "built-but-not-wired"** — RLS hook 0-callsite, action_config DTO-drift (fixed on this branch), CAG step commented out, `validate_constants.sh` pointing at deleted file.

---

## (e) RESOLVE-CHAIN/TOPOLOGY DETAIL + vs-SOTA + OPEN QUESTIONS

### e.1 — Worker topology + deployment as-built

- `docker-compose.yml`: postgres:16-alpine (`:4`), redis-stack 7.4 (`:21`), api/uvicorn (`:51`), infinity BGE-m3 (`:73`), tei-reranker (`:81`). Workers NOT in compose — systemd on host.
- `deploy/ragbot-chat-worker.service` (consumes `chat.requested`, templated @ scale, Restart=always) · `ops/systemd/ragbot-document-worker@.service` (consumes `document.uploaded.v1`; RAM-audit history: 4 instances = -800MB, operator-tuned) · `deploy/ragbot-monthly-reset.timer` (quota reset 1st 00:01 VN) · `deploy/ragbot-token-reconcile.timer` (every 5 min, Redis↔DB token reconcile) · outbox publisher for `system_config.changed.v1` (`system_config_service.py:62-66`).

### e.2 — vs SOTA platform/config/obs 2026: HAS / LACKS

**HAS (at or near SOTA):**
- Layered config resolve with per-key schema + min/max guard + write-time validation (`bot_limits.py`) — equivalent to LaunchDarkly-style per-entity targeting, DB-native.
- Config change propagation via outbox + Redis invalidation (no redeploy) — 12-factor + event-driven cache-bust.
- Port+Adapter metrics (`metrics_port.py`) with per-step duration histogram; 33-step pipeline timing in a queryable DB table (`request_steps`) — richer than most RAG stacks which only have traces.
- Opt-in OTEL with graceful no-op fallback (`tracing.py:49-55`); structlog structured events in 216 files.
- Eval harness with DB ground-truth verification + LLM-judge + failure-layer attribution + flip detection (`loadtest_graded.py`) — ahead of typical RAGAS-only setups; 3-run flip detection ≈ determinism testing few platforms do.
- Monthly token metering + 5-min Redis↔DB reconcile timer (eventual-consistency ledger pattern).

**LACKS (vs SOTA 2026):**
- **No per-LLM-call cost ledger** (purpose/stage dimension) — SOTA (Langfuse/Helicone/OpenLLMetry) records every LLM span with cost; here only per-turn aggregate (GAP-1).
- **No config-drift CI lint** across the 4 default sources; one existing guard targets a deleted file (a.3) — SOTA: schema-as-code with single generator emitting constants+seed+docs.
- **No live "effective config" introspection endpoint** (what value does bot X resolve for key Y, and from which tier) — SOTA feature-flag platforms expose evaluation reasons.
- **RLS designed but not engaged** (0 callsites) — defence-in-depth not yet active vs SOTA postgres multi-tenancy.
- **No SLO/error-budget machinery**: p95 target exists in charter, but no continuous latency measurement wired into the graded harness; no alerting rules (intentional per `feedback_no_premature_observability`, but a GA gap).
- **Eval not in CI** — graded harness is operator-run, not a regression gate per commit.
- OTEL default-off and not correlated to `request_steps` (two disjoint trace systems).

### e.3 — 10 OPEN QUESTIONS for Phase 2

1. **Cost-per-purpose**: add `request_llm_calls` child table (purpose, model, tokens, cost) so stage-level cost per tenant is DB-recoverable? Or accept per-turn aggregate for GA? (GAP-1)
2. **Seed drift**: is `init_system_config.py` still a live bootstrap path? Why diverged from alembic 0020 (`max_tokens` 1024 vs 450, `rerank_top_n` 10 vs 5)? Can one seed path be deleted? (DRIFT-1)
3. **4-way config lint**: build CI assert `DEFAULT_X == PLAN_LIMIT_SCHEMA[x].default == latest system_config value`? Or document migration-freeze as intentional?
4. **`validate_constants.sh` targets deleted `constants.py`** post-split `1446fef` — repoint to `constants/` package (and re-check version-ref tokens in module names like `_17_260509_*`).
5. **RLS hook**: what blocks wiring `attach_rls_session_hook` (`session.py:154`) — `SET LOCAL` per-txn perf, or role-migration ordering (0186/0187)? This is `260610-ga-hardening` ISSUE-1, P0.
6. **Override whitelist**: replace implicit "in PLAN_LIMIT_SCHEMA = overridable" with an audited allow-list? A schema key added without min/max becomes silently unbounded-overridable.
7. **Un-metered system-context LLM spend** (ingest enrich, narrate batch, HyDE on contextvars-less paths skip TenantTokenMeter) — attribute ingest cost to tenant ledger, how? (GAP-3/4)
8. **Orphan plans**: formally close or re-charter #19 query-graph-split + #16 path-to-9.5 + #4 L3 action-execution? Which DOING plans (esp. #10 multistep RC-3 retrieval) merge into the program waves?
9. **CAG observability**: `cag_lookup` step commented out in 3 files — is CAG wired at all, or another built-but-not-wired? If enabled, it's invisible in request_steps.
10. **p95 baseline missing**: the 85/91 graded baseline has NO latency measurement; charter axis NHANH (Tier-1 <1s / Tier-3 <15s) is unverifiable today. Wire latency capture into `loadtest_graded.py` before Phase 3 so the 6-axis table has all columns?
