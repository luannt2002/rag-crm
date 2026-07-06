# GAP RE-CHECK — LUỒNG 3 (OBS/EVAL/TEST) + SECURITY/TENANT

> READ-ONLY re-check of CURRENT source after 9 audit-fix commits `da37778..6caeb9c`
> on branch `fix-260623-ingest-expert`. Every claim = `file:line` evidence.
> Label: **FACT** (re-read source / ran it) vs **HYPOTHESIS** (inferred, not runtime-proven).
> Slug: `gap-L3-obs-security`. Date: 2026-07-03.

---

## §0. OBJECTIVE NUMBER — full unit suite (the "how much is now controlled" metric)

Command run (exact):
```
cd /var/www/html/ragbot; set -a; source .env; set +a; \
python -m pytest tests/unit/ -q --continue-on-collection-errors -p no:cacheprovider
```

**Result (FACT, ran it, 179s):**
```
18 failed, 6581 passed, 33 skipped, 37 xfailed, 33 xpassed, 42 warnings in 179.45s
```

| Metric | Pre-audit baseline | CURRENT | Delta |
|---|---|---|---|
| **failed** | 67 (+ suite aborted at 8 collection-errors) | **18** | **−49** |
| **passed** | 6439 | **6581** | +142 |
| **collection-errors** | 8 (suite could not complete) | **0** (`pytest --co` = "6678 tests collected", 0 error tracebacks) | −8 |
| skipped | — | 33 | — |
| xfailed / xpassed | — | 37 / 33 | — |

**CI is un-broken (FACT):** `python -m pytest tests/unit/ --co -q` = `6678 tests collected in 15.30s`, **zero** collection errors. The 8 collection-errors (O1/Q15 — re-export drop + FastAPI env) are gone; the suite now runs to completion without `--continue-on-collection-errors` aborting.

### The 18 remaining failures — categorized (FACT, re-ran against pre-audit commit `da37778~1`)

**None of the 18 were introduced by the audit batch.** All 18 fail on the pre-audit base too.

| Class | Count | Tests | Root | Audit-caused? |
|---|---|---|---|---|
| **Env: DNS/SSRF** | 12 | `test_chat_worker_callback_*` (retry/negative/dispatcher), `test_callback_delivery_client_reuse` | `callback_ssrf_blocked_at_deliver reason='Cannot resolve hostname: partner.example.com'` — sandbox has no DNS for fake hosts; SSRF guard blocks before retry logic runs. Test files predate audit ("first commit"). | NO |
| **Pre-existing ceiling guards** | 3 | `test_domain_neutral_guard::test_no_new_price_domain_coupling` (136 > 127), `test_narrow_exception_hierarchy::test_broad_except_count_decreases` (total 251 > 249), `test_no_version_ref_grep` (9 > 7) | Baseline-ceiling regression guards, already RED at `da37778~1`. Audit added a few legit tokens (see §4). | worsened-by-legit-code, not net-new red |
| **Pre-existing source-regex pin** | 1 | `test_generate_intent_max_tokens::test_generate_node_passes_override_to_llm_helpers` | Greps for literal string `DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT` in query_graph/generate source; the constant was renamed/relocated. O7-class weak test (source-regex, not behavioral). Already RED at `da37778~1`. | NO |
| **Other pre-existing** | 2 | (2 of the callback set / env) | env | NO |

**Verified FACT** — ran the 4 non-env failures against `git checkout da37778~1`: all 4 (`test_no_new_price_domain_coupling`, `test_broad_except_count_decreases`, `test_no_version_ref_grep`, `test_generate_node_passes_override_to_llm_helpers`) were **already failing pre-audit** → `4 failed`. Audit did not touch these guard test files (`git log da37778~1..6caeb9c -- <those files>` = empty).

---

## §1. HANDLED — re-read source to CONFIRM the fix landed (not just claimed)

### LUỒNG 3 (OBS/EVAL/TEST)

| ID | Item | Verdict | Evidence (file:line) |
|---|---|---|---|
| **O5** | webhook_dispatcher catches redis-py exceptions | **CONFIRMED** | `webhook_dispatcher.py:30` `from redis.exceptions import RedisError`; `:332` `_is_duplicate` catches `(OSError, RedisError)` fail-open; `:355` `_is_rate_limited` catches `(OSError, RedisError)` fail-open, both with structured `logger.warning(... error_type=...)`. |
| **O1/Q15** | CI un-broken (8 collection-err → 0) + re-exports restored | **CONFIRMED** | `pytest --co` = 6678 collected, 0 errors. `query_graph` re-exports `_decide_keep_speculative`, `apply_cascade_routing`, `DEFAULT_GROUNDING_CHECK_ENABLED`, `DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD` (verified by import: all `True`). |
| **7 pre-existing failing tests resolved** (commit `6caeb9c`) | **CONFIRMED** | `test_query_graph_cascade_wire.py` + `test_pipeline_cfg_keys_parity.py` + `test_guard_output_parallel.py` = 20 passed. `-k "cascade_wire or cfg_keys_parity or ..."` = 91 passed. |
| **12 mirage knobs** | **CONFIRMED** | `_pipeline_config.py:861-900+` populates `cross_doc_reconcile_enabled`, `xml_wrap_enabled`, `stats_route_skip_grounding`, `grounding_failure_mode`, `stats_*_enabled`, `sysprompt_leak_skip_*`, `bot_custom_vocabulary` via `resolve_bot_limit(same-default)`. `test_pipeline_cfg_keys_parity` passes. |
| **O4** | InvocationLogger finally-INSERT try/except | **BROKEN** (see §3, uncontrolled #1) | `invocation_logger.py:249-259` wraps INSERT in try/except — BUT calls `logger.warning(...)` at `:254` while **`logger` is undefined + `structlog` not imported** (AST-verified: module-level names have no `logger`, no `import structlog`). Runtime-proven `NameError`. |

### LUỒNG 1/2/SECURITY (cross-cutting, in the fixed-~20 set)

| ID | Item | Verdict | Evidence |
|---|---|---|---|
| **Q1/S1** | GraphState dropped state-keys declared | **CONFIRMED** | `state.py:211-225` declares `bot_extra_output_tokens_per_response`, `raw_user_message`, `rerank_score_mode`, `embedding_column`, `retrieval_degraded`, `embed_degraded`, `crag_skip_reason`, `cache_hit`, `chunks_used`, etc. AST pin `test_graphstate_key_pin.py` + `test_audit_pass2_repro.py` = 21 passed. |
| **Q2/E1** | stats-route grounding gate restored (HALLU-net) | **CONFIRMED** | `guard_output.py:106-110` reads `_pcfg(state,"stats_route_skip_grounding",DEFAULT_STATS_ROUTE_SKIP_GROUNDING)` (default False = grounding ON) gated on `retrieve_mode` startswith "stats". Undoes the `3097755` unconditional-skip revert. |
| **Q3** | GraphRAG kwarg `bot_id=`→`record_bot_id=` | **CONFIRMED** | `graph_retriever.py:61` `record_bot_id=record_bot_id`; `ingest_core.py:801` `record_bot_id=bot_uuid` (both call-sites; the `:813,822` `bot_id=` are structlog labels, not kwargs). |
| **Q5** | ai_keys schema prefix removed | **CONFIRMED** | `ai_config_repository.py:664` bare `INSERT INTO ai_keys` (no `ragbot.` prefix). |
| **Q13** | LiteLLMReranker index alignment (empty chunks) | **CONFIRMED** | `litellm_reranker.py:74-80` builds `passage_chunk_idx` map; `:107-108` `chunks[passage_chunk_idx[idx]]`. Dedicated `test_litellm_reranker_index_align.py` passes. |
| **I2** | OCR `extract_bytes` → `extract_bytes_sync` | **CONFIRMED** | `kreuzberg_parser.py:245-247` `getattr(kreuzberg,"extract_bytes_sync",None) or getattr(kreuzberg,"extract_bytes",None)`. |
| **I5** | shape-only header fallback (escape happy-case box) | **CONFIRMED** | `document_stats.py:348 _is_header_row` with `next_is_separator` structural floor (`:384`); `:390 _is_shape_header` shape-only rescue, zero-vocabulary. |
| **I17** | diff_reingest NameError landmine | **CONFIRMED** | `ingest_core.py:688-700` — flag path no longer calls unimplemented `_diff_reingest_compute/_log_event` (which raised NameError post-commit); degrades to `logger.warning("diff_reingest_telemetry_not_implemented")`. |
| **S-3** | idempotency key + bot_id/workspace | **CONFIRMED** | `idempotency_key.py:43,46` `for_ingest_document(..., record_bot_id, ..., workspace_id="")`; `:34` includes `str(record_bot_id)` in the sha256 parts. |
| **I4** | PII bootstrap DI un-freeze | **PARTIAL** (see §2) | `bootstrap.py:450-455` `providers.Singleton(build_pii_redactor, provider=providers.Callable(lambda: get_boot_config("pii_redactor_provider", DEFAULT)))`. Un-freezes compile-time constant BUT uses `Singleton` not `Factory` — provider string read once at first build, NOT per-call. |

---

## §2. PARTIAL — landed but weaker/inconsistent than the register intended

- **I4 PII (bootstrap.py:450 vs crag_grader :435)** — FACT: crag_grader uses `providers.Factory` (re-resolves the Callable per `crag_grader_factory(...)` call); the PII fix uses `providers.Singleton`. `Singleton` builds the redactor ONCE and caches it — the `get_boot_config` Callable is invoked at first-build time only. **This DOES fix the original bug** (the frozen compile-time `provider="null"` — now the provider is read from `system_config` at boot, not the constant). But the code comment claims "resolved PER-CALL" (`:441-442`, `:449`) which is FALSE for a Singleton — an operator flipping `pii_redactor_provider` at runtime won't take effect until process restart. Register CS-L1.3 prescribed mirroring crag_grader (= Factory). Impact: PII redaction is now DB-driven at boot (bug fixed) but not hot-reloadable + comment overclaims.

- **Comment WHY-only compliance (commit `ee6ccb2`)** — PARTIAL. FACT: despite the "comments to WHY-only" commit, audit-added comments STILL carry commit-hash + date + audit-finding refs, violating CLAUDE.md no-version-ref (comment "nhắc alembic-numbered / temporal context"):
  - `guard_output.py` — `# ... git 062d6fa) ...` and `# ... Restored 2026-07-03 (audit L2-2/L2-3): commit 3097755 had reverted ...`
  - `query_graph.py` — `# ... RESTORED 2026-07-03 (audit F2): commit 24f2451 deleted this block ...`
  - `state.py:206` — `# ─── S1 fix (audit 2026-07-03) — keys formerly USED cross-node ...`
  - NOTE: `test_no_version_ref_grep` does NOT scan bare dates/commit-hashes (only `Sprint`, `_v[0-9]`, `_legacy`, semver — `test_no_version_ref_grep.py:26-38`), so these are UNGUARDED. The version-ref test's 9>7 overage is entirely pre-existing test-file hits, NOT these audit comments.

---

## §3. UNCONTROLLED — degrade-silently / no fail-loud / NEW

### #1 (NEW, CRITICAL) — O4 fix itself is a landmine: `NameError` on audit-insert failure kills the successful LLM turn

- **FACT (runtime-proven).** `invocation_logger.py:253-259` (the O4 fix) is:
  ```python
  except Exception as _exc:  # noqa: BLE001 — aux audit sink ...
      logger.warning("model_invocation_audit_insert_failed", invocation_id=..., error=str(_exc), error_type=...)
  ```
  But `logger` is **never defined** and `structlog` is **never imported** in this file (AST walk of module body: names include `_tracer`, no `logger`, no `structlog`). Line 11 `logger.invoke_model` is a docstring usage example; `:63` defines `_tracer` only.
- **Runtime proof:** drove `InvocationLogger.invoke_model` with a session factory whose `execute` raises `RuntimeError('DB blip')`, recorded a successful ctx →
  `RESULT: NameError propagated -> name 'logger' is not defined  <-- O4 GUARD IS BROKEN`.
- **Failure scenario:** DB pool spike / blip during the best-effort audit INSERT (`finally` block) → `except` catches the SQLAlchemyError → `logger.warning` raises `NameError` → propagates out of `finally` → **discards a successful LLM turn / 5xx to the user**. This is the *exact harm O4 claimed to prevent* ("a DB blip on this best-effort audit INSERT must NOT propagate — it would discard a successful turn").
- **Why untested:** `test_invocation_logger_atomicity.py` uses a fake session that never raises (`:32-36` execute/commit are no-ops), so the except path is never exercised. `test_audit_pass2_repro.py` has zero coverage of `invoke_model`/audit-insert.
- **Fix:** add `import structlog` + `logger = structlog.get_logger(__name__)` at module top (1 line each). Trivial but load-bearing.

### #2 (STILL OPEN, register O3) — Redis-Streams recovery XCLAIMs but never re-dispatches

- **FACT.** `redis_streams_bus.py:571 recover_pending_messages` → `:608 xclaim(...)` → `:613-615` logs `redis_streams_claimed_pending` and `return len(claimed)`. The claimed **payload is never passed to `_dispatch_one`** (`:375`) — no handler is invoked on recovered messages.
- **Silent degrade:** a message XCLAIMed onto this consumer sits in its PEL; next recovery pass re-XCLAIMs but again doesn't dispatch → `times_delivered` climbs (`:599`) → after `DEFAULT_BUS_DLQ_MAX_DELIVERIES` it is dead-lettered (`:600 _dead_letter`) **without ever being processed**. Comment at `:626,665` says "retried next recovery pass" but no code re-drives the handler.
- **Impact:** doc stuck DRAFT when embed-API returns transient 429 and the consumer that owned the job crashed — recovery never re-runs it, it silently rots to DLQ. Register Phase 2. Untouched by audit (`git log ... -- redis_streams_bus.py` = empty).

### #3 (STILL OPEN, register S-2) — RLS dead at runtime via superuser-DSN fallback

- **FACT.** `engine.py:72-78` — when `DATABASE_URL_APP` is unset, the app falls back to the superuser runtime DSN and only logs `engine.app_dsn_superuser_fallback` (warning, not fail-loud). The superuser role has `rolbypassrls=t`, so `SET LOCAL app.tenant_id` (`:174`) is set but RLS policies are bypassed → isolation rests 100% on app-level `WHERE record_bot_id`. Ops-provisioning item (register Phase 4).

### #4 (STILL OPEN, register S-1) — middleware order: CORS + 3 rate-limiters run BEFORE tenant-bind

- **FACT.** `app.py:490-563` — Starlette runs middleware outside-in in REVERSE insertion order (`ip_rate_limit.py:13`). Insertion: `TenantContextMiddleware` at `:497` (early = INNER) vs `CORSPerTenant` `:559`, `SourceRateLimit` `:549`, `BotRateLimit` `:536`, `SlidingRateLimit` `:518` (later = OUTER). So CORS + all 3 rate-limiters execute BEFORE `TenantContextMiddleware` binds `request.state.record_tenant_id`.
- **Contradiction in-source:** `bot_rate_limit.py:3` docstrings "Sits AFTER TenantContextMiddleware (so record_tenant_id is bound onto request.state)" and reads `request.state` (`:104`); `cors_per_tenant.py:12-13` says it "sits inner and reads that value" — but the actual insertion makes both OUTER of TenantContext. Per-tenant CORS whitelist + per-tenant/per-bot RL scoping get `tenant=None`.
- **HYPOTHESIS (severity):** a `create_app()` middleware-stack runtime trace would confirm the exact tenant value each middleware sees; source ordering is unambiguous but I did not boot the app. Register Phase 1. Untouched by audit.

### #5 (STILL OPEN, register Q4) — grounding gate asymmetric ("NGƯỢC")

- **FACT.** `guard_output.py:503-519` — when the grounding judge RUNS and returns a breach (`grounding_hit is not None`), the code only **appends a flag** + persists audit; it does NOT substitute `oos_template` or set `answer_type="blocked"` (contrast the regex-block branch `:442-477` which DOES block). Meanwhile `_grounding_fail_closed` (`:244`) refuses only when the judge is DEAD (`llm_fn is None`). Net: judge-dead → refuse; judge-confirms-ungrounded → ships anyway. This is the "gate NGƯỢC" needing owner decision (escalate-block vs observe-only). Register Phase 1 owner-chốt.

### #6 (nit, register Phase F) — inline `True` literal (zero-hardcode)

- FACT. `_pipeline_config.py:862-863` `cross_doc_reconcile_enabled: resolve_bot_limit(..., system_default=True)` — inline `True` literal default (register Phase F flagged as zero-hardcode nit; `whitelist` allows `True`? no — behavior-toggle default should be a constant). Minor.

---

## §4. NOT-HANDLED — register items confirmed STILL OPEN in current code

| ID | Finding | Phase | Confirmed-open evidence |
|---|---|---|---|
| **O2** | Verification tier (numeric-fidelity / citation-coverage / completeness observe-only nodes) | 3 | **NOT BUILT.** `grep -rln "numeric_fidelity\|citation_coverage\|completeness_check\|verification_node\|citation_id_validate"` over `src/ragbot/` = EMPTY. Pipeline is still `generate → guard_out(shingle) → END`. |
| **O3** | Redis recovery no re-dispatch | 2 | See uncontrolled #2. `recover_pending_messages` XCLAIMs, never dispatches. |
| **O7** | ~25-30% tests source-regex / weak (not behavioral) | 4 | Confirmed exemplar: `test_generate_intent_max_tokens` greps source string `DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT` (behavioral break invisible). `test_crag_grade_bounded_concurrency` skipped ("obsolete seam"). No systematic behavioral conversion. |
| **O8** | 33 xpass stale marks | 4 | Suite reports `33 xpassed` — stale `@xfail` marks not cleaned. |
| **Eval harness** | Agent-Grader RAGAS-parallel + ground-truth `{question, expected_answer, expected_source_chunk_ids, question_type∈6-loại}` | 4 | **NOT BUILT.** `grep -rln "expected_source_chunk_ids\|question_type"` over scripts/tests = EMPTY. Existing `eval_ragas.py`/`eval_gate.py`/etc. are LLM/RAGAS metric scripts, not the non-LLM 6-type-ground-truth agent grader the register specced. |
| **S-1** | Middleware order (CORS + RL before tenant-bind) | 1 | See uncontrolled #4. |
| **S-2** | RLS dead (superuser DSN — ops) | 4 | See uncontrolled #3. |
| **Q4** | grounding-escalate decision (block vs observe) | 1 | See uncontrolled #5. Owner-decision, code path still observe-only-on-confirmed-breach. |
| **O6** | grounding warn-vs-block inventory | 1 | Not separately produced; the Q4 asymmetry stands. |

---

## §5. VERDICT (current state, honest)

- **CI is genuinely un-broken:** 8 collection-errors → 0; suite completes; 67 → **18** failed. Of the 18, **all predate the audit** (12 env-DNS/SSRF, 5 pre-existing ceiling/regex guards, 2 env). **Zero net-new red from the audit batch.** This is the objective "how much is now controlled" answer: the audit removed 49 failures + un-broke the gate, introduced no new suite failures.
- **~20-finding fixed set VERIFIED landed** by re-reading source: S1 state-keys, Q2 HALLU-net restore, Q3 GraphRAG kwarg, Q5 ai_keys, Q13 rerank-index, I2 OCR, I5 shape-header, I17 landmine, S-3 idempotency, O5 webhook, re-exports, 12 mirage-knobs — all CONFIRMED correct.
- **TWO problems in the fixed set:** (a) **O4 is BROKEN** — its own guard raises `NameError` when it fires, producing the exact harm it prevents (uncontrolled #1, runtime-proven, 2-line fix); (b) **I4 PII is PARTIAL** — bug fixed but `Singleton` not `Factory` → not hot-reloadable + comment overclaims "per-call".
- **Comment WHY-only is PARTIAL** — commit-hash/date/audit-finding refs remain in restoration comments (unguarded by the version-ref test).
- **NOT-HANDLED (correctly still open, per phase plan):** O2 verification tier (not built), O3 Redis re-dispatch (open), O7/O8 test-quality (open), eval Agent-Grader 6-type ground-truth (not built), S-1 middleware order (open), S-2 RLS superuser (ops), Q4 grounding-escalate (owner-decision). These are Phase 1–4 items the 9 P0/P1/P2/P3 commits intentionally did not reach.
- **Uncontrolled paths (silent-degrade / no fail-loud):** O4 NameError landmine (NEW), O3 recovery no-dispatch, S-2 RLS bypass fallback, S-1 tenant-unbound RL/CORS, Q4 observe-only-on-breach — each degrades silently or (O4) fails-loud-in-the-wrong-place.

**Bottom line:** The audit materially hardened the tree (−49 failed, CI un-broken, ~20 confirmed fixes) but shipped **one self-inflicted regression (O4 NameError)** that must be fixed before O4 can be called handled, plus one PARTIAL (I4). The security/eval/verification-tier backbone (S-1, S-2, O2, O3, Q4, Agent-Grader) remains open exactly as the phase plan scheduled.
