# Troubleshooting Guide

> **Scope**: Symptom → root-cause → fix table for the issues we have actually hit on this codebase. Reference real commits and real V2 bug classes.
> **Companion**: [`docs/V2_MIGRATION_BUG_LESSONS.md`](V2_MIGRATION_BUG_LESSONS.md) — the four-bug pre-flight catalogue.
> **Last verified**: 2026-05-01 against migration HEAD `0054`.

---

## 0. First responder checklist

When a fresh issue lands, run these in order before deep-diving:

1. `curl -s http://<host>:3004/health | jq .` — `status` field, dependency block, pool gauges.
2. `curl -s "http://<host>:3004/health/models?tenant_id=&bot_id=&channel_type=" | jq .` — per-purpose resolver smoke (commit `01fd439`).
3. `.venv/bin/python scripts/preflight_check.py --strict` — DB at HEAD, all `system_config` keys set, no `purpose='reranker'` drift, provider keys live.
4. `tail -f /var/log/ragbot/app.log | jq 'select(.level=="error")'` — most recent error events with `trace_id`.
5. `psql -c "SELECT * FROM request_steps WHERE trace_id='<trace>' ORDER BY started_at"` — per-node forensic latency for a problem turn.

If steps 1–3 are all green, you are looking at a quality issue (T1) rather than a runtime/infra issue (T2/T3); jump to §3.

---

## 1. Retrieval / quality symptoms

### 1.1 `top_score` ≈ 0.05 even with Jina v3 wired (V2 BUG #1)

**Symptom**: Live trace shows `rerank mode=null_reranker` despite `bot_model_bindings` row + Jina key present.

**Root cause**: `application/services/reranker_resolver.py` SQL filters `WHERE b.purpose = 'rerank'`; legacy seed scripts wrote `purpose='reranker'`. SQL miss → `_lookup_db` returns `None` → `_build_from_config(None)` → `NullReranker()` overrides the bootstrap singleton.

**Fix**:

```sql
UPDATE bot_model_bindings SET purpose = 'rerank', updated_at = now() WHERE purpose = 'reranker';
```

Long-term: alembic CHECK constraint pins the enum; `0053` already enforces it. Pre-commit: `grep -rn "purpose.*'reranker'" src/ scripts/` must be 0. Reference: [`docs/V2_MIGRATION_BUG_LESSONS.md §BUG-1`](V2_MIGRATION_BUG_LESSONS.md).

After fixing, hit `POST /api/ragbot/admin/cache/reload` and verify with `/health/models`.

### 1.2 `semantic_cache_check failed` dim mismatch (V2 BUG #2)

**Symptom**: Log line `step=semantic_cache_check failed: asyncpg.DataError: different vector dimensions 1536 and 1024`. Cache silently misses; Q3–Q17 still runs end-to-end.

**Root cause**: `infrastructure/cache/semantic_cache.py:_find_similar_impl` hardcodes the 1536-dim `query_embedding` column. V2 introduced `query_embedding_v3 vector(1024)` (alembic `0054`), but `semantic_cache.py` was not migrated to a column-aware kwarg.

**Impact**: L2 hit-rate drops to ~2 %; no answer correctness risk (cache is best-effort). Wall-time / cost stay roughly flat.

**Fix in flight**: add `embedding_column` kwarg to `find_similar` / `find_similar_with_text` / `_find_similar_impl` mirroring the `pgvector_store` pattern, whitelist column names, plumb `state["embedding_column"]` from `query_graph`. Tracked.

**Workaround**: rely on L1 exact-hash for now; do not disable L2 (write path still works for matching-dim bots). Once fixed, expect L2 hit-rate to climb to 25–40 %.

### 1.3 Ingest writes wrong embedding dim (V2 BUG #3)

**Symptom**: Query path returns `top_score ≈ 0.05` despite Jina v3 binding; SQL shows `document_chunks.embedding` populated but with wrong dimensionality, OR `embedding IS NULL` for new ingests.

**Root cause**: `application/services/document_service.py:_embedding_spec()` reads the global `system_config.embedding_model` — it does **not** consult per-bot `bot_model_bindings`. A bot bound to Jina v3 still gets OpenAI 1536-dim chunks at ingest time.

**Workaround (operational)**:

```bash
.venv/bin/python scripts/emergency_restore_embeddings.py \
  --tenant-id 1 --bot-id <bot-name> --channel-type web --provider jina_v3
```

The script re-embeds existing chunks using the per-bot resolver and writes into the right column.

**Permanent fix** (planned, not on main):
- Inject `model_resolver` into `DocumentService.__init__`.
- Change `_embedding_spec(self, *, record_bot_id, record_tenant_id)`.
- Update worker job payload to include `record_bot_id` from upload context.

Reference: [`docs/V2_MIGRATION_BUG_LESSONS.md §BUG-3`](V2_MIGRATION_BUG_LESSONS.md).

### 1.4 Pre-flight: `reranker_provider key missing`

**Symptom**: `scripts/preflight_check.py` warns `reranker_provider not set in system_config`.

**Root cause**: First-time deploy without `init_system_config.py`, or someone deleted the key.

**Fix**:

```bash
.venv/bin/python scripts/init_system_config.py
```

Idempotent; re-running only adds missing keys. Verify with:

```sql
SELECT key, value FROM system_config WHERE key LIKE '%provider%';
```

### 1.5 `_check_reranker_preflight` "unrecognised prefix" warning (V2 BUG #4)

**Symptom**: Boot log: `reranker_preflight_unknown_provider note="enabled=true but provider prefix is unrecognised"`. Behaviour still works.

**Root cause**: Preflight in `interfaces/http/app.py` guesses provider from the model name string (`startswith("jina/")` …). Seed model name is `"jina-reranker-v3"` — no prefix → guess fails.

**Fix**: lookup `ai_models.record_provider_id` → `ai_providers.code` instead of pattern-matching the name. Tracked. Until shipped, the warning is cosmetic; safe to ignore once `/health/models` reports `status:"ok"` for the rerank purpose.

### 1.6 Refuse-rate up vs previous round

**Symptom**: PASS-rate drops; harness reports `REFUSE_GAP` widening; user reports "the bot is refusing things it used to answer".

**Diagnostic order**:

1. Was a `system_prompt` edited? Bot owner controls behaviour — check `bots.system_prompt` for new "if uncertain refuse" rule.
2. Was a `bot_model_bindings` row touched? `/health/models` per-purpose status.
3. Did `top_score` collapse? Run a single trace with logging: if `top_score < 0.10` on questions known to have answers, you are seeing V2 BUG #1 / #3 above.
4. Did the corpus change? `SELECT count(*) FROM document_chunks WHERE bot_id IN (...) AND embedding IS NOT NULL` — null embeddings mean ingest skipped.

**What NOT to do**: do **not** add platform-side text injection or override LLM answers to mask the regression — CLAUDE.md "Application MINDSET" forbids it (zero tolerance, locked by 9 unit tests).

---

## 2. Operational symptoms

### 2.1 HALLU_FABRICATE breach detected

**Severity**: P0 — sacred contract violated. The HALLU_FABRICATE invariant is **0 / 15 trap turns** every load-test round. Across all V2 rounds (R5, VA, VB, VC) we hold zero.

**Escalation playbook**:

1. **Freeze deploys** to that bot/tenant immediately.
2. **Capture trace**: `psql -c "SELECT * FROM request_logs WHERE trace_id='<trace>'"`, plus `request_steps` for the same row.
3. **Diff the system_prompt**: did it lose the "do not invent values not in the corpus" clause? Restore prior version from `audit_log` (action=`bot_update`).
4. **Diff the corpus**: did a new doc introduce a number that the LLM is generalising from? Inspect via `GET /api/ragbot/sync/documents`.
5. **Re-run harness with the failed turn**: confirm reproducibility before opening an Opus plan.
6. **File a plan** in `plans/YYMMDD-HALLU-breach-<bot>/plan.md` — T1 priority, all other work pauses.

### 2.2 Test pollution on full-suite run

**Symptom**: `pytest -q` passes on individual files but fails on the full suite (e.g. fixture leak, settings cache, Redis key collision).

**Root cause** (most common): a test forgot `monkeypatch` cleanup, leaving a global state mutation visible to later tests.

**Fix**:

```bash
# Run the failing test in isolation; if it passes, it is a pollution issue
.venv/bin/pytest -x -p no:cacheprovider tests/unit/test_<file>.py

# Bisect with -k to find the polluter
.venv/bin/pytest -x tests/unit/ -k "not <suspect>"
```

Quarantine the polluter in a `pytest.mark.serial` group rather than `xfail`-ing legitimate tests. The 2442-collected baseline must hold (CLAUDE.md "tests baseline never decreases" rule).

### 2.3 JWT expired mid load test

**Symptom**: Harness halfway through 150 turns starts emitting `401 Unauthorized`.

**Root cause**: Service token TTL elapsed (default 1 h on `/api/ragbot/test/tokens`).

**Fix**:

- Mint with `ttl_seconds: 7200` (2 h) for 150-turn runs that include 30 s think-times.
- Add a refresh hook in `scripts/agent_d_loadtest.py` (re-mint when `< 5 min` of TTL remain) — pattern already in the script's `_loadtest_common.py`.

### 2.4 `tenant_mismatch` on every chat call

**Symptom**: HTTP 403 `tenant_mismatch` from every `/chat` despite `tenant_id` in body.

**Root cause**: JWT `tenant_id` claim does not equal body `tenant_id`. Common causes:

1. Token was minted for the wrong tenant.
2. Body uses string `"1"` where schema expects int `1` (Pydantic coerces, but the upstream JWT may have minted a string claim).
3. JWT issuer mints `tenant_id` only on user tokens; service tokens carry a tenant from a different claim path.

**Fix**:

```bash
# Decode the JWT (no verification) to inspect claims
echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | jq .
```

Confirm `tenant_id` claim equals body. Super-admin tokens (`role: super_admin`) bypass this guard for cross-tenant ops.

### 2.5 `bot_not_found` on a bot that obviously exists

**Symptom**: 404 on `/chat` for `(tenant_id, bot_id, channel_type)` combination that you can see in the DB.

**Diagnostic**:

```sql
SELECT id, tenant_id, bot_id, channel_type, is_deleted
FROM bots
WHERE bot_id = '<bot-name>' AND channel_type = 'web';
```

If `tenant_id` differs from your request → you are sending the wrong tenant.
If `is_deleted = true` → soft-deleted (admin reactivate via `PATCH /api/ragbot/admin/bots/{uuid}`).
If row exists but lookup misses → Redis cache poisoned. `POST /api/ragbot/admin/bots/cache/reload` (super-admin) and retry.

### 2.6 Rate-limit 429 with no obvious traffic

**Symptom**: 429 spikes during a quiet period; `tenant_token_blocked` Prometheus counter increments.

**Root cause**:

1. `tenants.monthly_token_cap` exhausted — even at 1 RPM, output tokens accumulate.
2. Redis outage → fail-closed limiter rejects (Sprint-12B intentional).

**Fix**:

```sql
-- Inspect monthly usage (token_usage_log view)
SELECT tenant_id, sum(input_tokens + output_tokens) AS total
FROM invocation_logger_log
WHERE created_at >= date_trunc('month', now())
GROUP BY tenant_id;

-- Bump cap if legitimate
UPDATE tenants SET monthly_token_cap = <new> WHERE id = 1;
```

Set `bots.bypass_rate_limit=true` per-bot only when you know the bot is internal (e.g. health probe bot).

### 2.7 p95 latency regression (no code change)

**Symptom**: p95 jumps post-deploy or after a cron job runs.

**Diagnostic order**:

1. `/health` `pool_stats` — DB or Redis pool saturated? Bump pool size first, scale workers second.
2. `/admin/metrics/steps` — which step exploded? If retrieve, suspect HNSW degraded after bulk ingest. `REINDEX CONCURRENTLY ...` outside business hours.
3. `circuit_breaker_state` Prometheus — open breaker on JinaReranker / LiteLLMEmbedder = upstream provider degraded; Null fallback masks the issue functionally but shows up as p95 dip on rerank node.
4. Cache hit-rate — sudden drop suggests the cache key shape changed (e.g. system_prompt edit invalidates L1).

If none of the above, capture a profile with `py-spy` against the worker PID and triage the hot frame.

---

## 3. Quality / harness symptoms

### 3.1 Harness PASS-rate dropped

**Step 1**: are you comparing apples to apples? Same question file, same model bindings, same corpus snapshot.
**Step 2**: re-run the previous baseline; if it does not reproduce, the corpus or system_prompt drifted.
**Step 3**: `top_score` distribution per turn — VC mean was 0.318; below ~0.20 means retrieval is degraded (likely V2 BUG #1 or #3).
**Step 4**: per-batch deepdive: `scripts/loadtest_perbatch_deepdive.py reports/load_run_*.json`.

### 3.2 Sudden new HALLU_MISINTERPRET cases

A faithful answer that nevertheless conflates topics (VC.r60.i4 example: `"trẻ"` token ambiguity). This is **not** a HALLU_FABRICATE breach but is still graded FAIL.

**Path**:

- Add a clarification step on ambiguous `top_score` (0.30–0.40) — VD-2 plan.
- Split the harness target into FABRICATE vs MISINTERPRET in `scripts/auditor_analyze_round.py` so the team can track the two regressions independently. VD-3 plan.

### 3.3 Citations missing or wrong

**Diagnostic**: trace shows `citations_extract` returned `[]` despite chunks present.

**Common causes**:

1. The system_prompt does not instruct the LLM to emit citation markers. Bot owner edits — application code does **not** inject citation hints (CLAUDE.md zero-tolerance).
2. Citation parser regex requires a marker the LLM no longer emits (e.g. swapping `[1]` for `(1)`). Check `_lang_pack.citation_pattern` overrides per bot.
3. `top_chunks` truncated by `mmr_dedup` aggressively. Inspect `request_steps` for chunk counts at each Q-stage.

---

## 4. Migration / schema symptoms

### 4.1 `relation "..." does not exist` on boot

You are below migration HEAD. Run:

```bash
.venv/bin/alembic current
.venv/bin/alembic upgrade head
```

If `current` shows nothing, the database is empty — boot the schema with `alembic upgrade head` from a clean state. Never edit `_alembic_version` manually.

### 4.2 Migration drift: code expects column that doesn't exist

`scripts/preflight_check.py --strict` catches this — schema HEAD must match code HEAD. CI/CD post-deploy gate; if it fails, roll back the deploy, run `alembic upgrade head`, redeploy.

### 4.3 `purpose='reranker'` rows after upgrade

Should be impossible after `0053`, but if a partial migration leaves drift:

```sql
SELECT count(*) FROM bot_model_bindings WHERE purpose = 'reranker';  -- expect 0
UPDATE bot_model_bindings SET purpose = 'rerank' WHERE purpose = 'reranker';
```

Then `POST /api/ragbot/admin/cache/reload` and verify.

---

## 5. Where to ask for help

1. Check [`STATE_SNAPSHOT.md`](../STATE_SNAPSHOT.md) — current open P0 issues + deferred plans.
2. Check the relevant plan in [`plans/`](../plans/) (every active workstream has one).
3. Check `audit_log` for the resource — answers "who changed this and when".
4. Check `request_steps` joined to `request_logs` for forensic per-node latency.
5. Open a plan file `plans/YYMMDD-<symptom-slug>/plan.md` and tier-tag it (T1/T2/T3) before writing code.

---

## 6. See also

- [`docs/V2_MIGRATION_BUG_LESSONS.md`](V2_MIGRATION_BUG_LESSONS.md) — the four bug classes and the preflight that catches them.
- [`docs/API_REFERENCE_V2.md`](API_REFERENCE_V2.md) — error codes catalogue.
- [`docs/PERFORMANCE_TUNING.md`](PERFORMANCE_TUNING.md) — when "p95 spiked" maps to a tuning knob.
- [`docs/ONBOARDING_GUIDE.md`](ONBOARDING_GUIDE.md) — fresh-tenant smoke when "nothing works on a new env".
- [`docs/ARCHITECTURE_DIAGRAMS.md`](ARCHITECTURE_DIAGRAMS.md) — visual map; symptom often points to a node.
- [`scripts/preflight_check.py`](../scripts/preflight_check.py) — the deploy-time gate every fix should pass through.
