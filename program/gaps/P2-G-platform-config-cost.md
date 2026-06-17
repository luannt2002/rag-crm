# P2-G — PLATFORM / CONFIG / OBSERVABILITY / COST AUDIT (Phase 2)

> STANCE = **EVOLVE**. Read-only src/alembic/tests; this is the only file written.
> Input: `program/context/P1-G-platform-config-cost.md` + `P1-SYNTHESIS.md §4 Q21/Q22`.
> Anchor commit `7dd1f84` · alembic head `0195` · branch `fix-260604-action-slotmachine-dead-key`.
> Every claim below = `file:line` / commit, re-verified 2026-06-10. **Where I disagree with P1-G I say so explicitly and give the line.**

---

## 1. LABELED COMPONENT TABLE (✅ / 🕰 / ↔️ / 🐛)

| # | Component | Label | Evidence (`file:line`) | One-line verdict |
|---|---|---|---|---|
| 1 | 5-tier resolve chain (`resolve_bot_limit`) | ✅ | `bot_limits.py:376-456`; range-guard reject `:429-448`; schema ~50 keys `:51-361` | Genuinely good — per-key schema + min/max + write-time validate = LaunchDarkly-class, DB-native. **Praise.** |
| 2 | 33-step `request_steps` instrumentation | ✅ | 33 step names `query_graph.py` (`grep 'step("' \| sort -u`); write path `request_log_repository.py:241-300` | Queryable per-step timing in a DB table — richer than trace-only RAG stacks. **Praise.** |
| 3 | `PLAN_LIMIT_SCHEMA` + clamp | ✅ | `bot_limits.py:51-361,515`; `validate_plan_limits` clamps JSONB | Per-key default+min+max as code; out-of-range silently falls through (correct fail-safe). **Praise.** |
| 4 | **Per-step LLM cost ALREADY persisted** (P1-G under-credited this) | ✅ | `record_llm()` `step_tracker.py:268-291`; **12 production callers** `query_graph.py:2051,2214,2713,2965,4229,5354,6542,6852,7193,7222,7891` + condense `:2051`; persisted `request_log_repository.py:283-285` (`input_tokens/output_tokens/cost_usd`) | `request_steps.cost_usd` + `step_name` + `record_tenant_id` are POPULATED on LLM steps. **Per-stage per-tenant cost IS DB-recoverable today** — see §3 (this revises P1-G GAP-1 severity). |
| 5 | Config change propagation (outbox + Redis bust) | ✅ | `system_config_service.py:62-66` emits `system_config.changed.v1`; bulk read `chat_worker.py:720` | No-redeploy config flip, event-driven invalidation. **Praise.** |
| 6 | Config drift: `init_system_config.py` ≠ alembic 0020 | 🐛 | 0020 `max_tokens="450"` (`...0020...py:24`) vs init `"1024"` (`init_system_config.py:30`); 0020 `rerank_top_n="5"` (`:32`) vs init `"10"` (`:38`) | Two bootstrap paths produce different DBs. §2. |
| 7 | `validate_constants.sh` points at deleted file | 🐛 | `validate_constants.sh:18` `CONSTANTS=src/ragbot/shared/constants.py`; file gone post-split `1446fef` (`ls` → No such file); guard exits at `:19-20` | Hook is a silent no-op — version-ref check no longer runs. §2. |
| 8 | Cost attribution — `purpose` never a DB column; ingest cost=0; no per-step cost read-query | 🐛 | `complete_runtime(purpose="unknown")` default `dynamic_litellm_router.py:432`; ingest helper hardcodes `"cost_usd": 0.0` `document_service.py:3881`; `analyze_step_timing` groups `step_name` but sums only `duration_ms` NOT `cost_usd` `request_log_repository.py:582-592` | Data partly exists but read-path + ingest leg incomplete. §3 + Q21. |
| 9 | Config management approach (6-tier sync 4 places) | 🕰 | 4 places: constants/, alembic 0020, init script, `PLAN_LIMIT_SCHEMA`; lint covers 2 pairs only `audit_config_key_drift.py` | Hand-synced; 2026 norm = generated-from-single-schema. §4 + 🕰 box below. |
| 10 | Cost attribution mechanism (child-table vs OTEL span) | 🕰 | OTEL opt-in no-op `tracing.py:49-55`; disjoint from `request_steps` | 2026 norm = OTEL GenAI `gen_ai.usage.*` span attrs. 🕰 box below. |
| 11 | Tier count: charter "6-tier" vs 5 documented | ↔️ | resolver = 5 tiers `bot_limits.py:410-420`; 6th = comment-only `chat_worker.py:1410` | Chốt in §5. |
| 12 | RLS hook `attach_rls_session_hook` orphan | 🐛 | `session.py:154` def; grep across `src/ragbot/` = 3 hits, ALL in `session.py` (docstring `:30` + def `:154` + `__all__` `:182`) → **0 production callsites** | Defence-in-depth designed, not engaged. (`260610-ga-hardening` ISSUE-1 P0; out of this auditor's fix scope — flagged to P2-C.) |

**Label tally: ✅ ×5 · 🐛 ×4 · 🕰 ×2 · ↔️ ×1** (component #11 is the ↔️; #12 is a 🐛 owned by multitenancy auditor).

---

### 🕰 "Chuẩn 2026 là gì" — 2 questions, ≤3 web searches used

**Q-A · Config management: 6-tier-sync-4-places vs single-source schema-registry + generated constants?**
2026 norm (verified): **generated-from-a-single-schema beats hand-synced seed files** — "seed-file drift is structural, not disciplinary; a static file cannot keep up with a schema that changes every sprint" ([Seedfast](https://seedfa.st/blog/seed-file-maintenance)). The drift-detection pattern is CI-level: `atlas migrate diff` then assert no new file was generated — if state ≠ migration dir, the engineer forgot to regen ([Atlas drift CI](https://atlasgo.io/faq/desired-state-drift)). **Verdict for Ragbot**: the 4-place model is below 2026 norm, but a full schema-registry rewrite is over-engineering for ~50 keys (violates Simplicity-First). EVOLVE move = make **one** of the 4 the generator and emit the other 3 (constants → generated seed + generated `PLAN_LIMIT_SCHEMA` defaults), then a CI value-equality lint (Q22) as the cheap drift gate. Don't import Confluent/Buf/Atlas-registry machinery.

**Q-B · Cost attribution: per-call child table vs OTEL span attributes?**
2026 norm (verified): **OTEL GenAI semantic conventions** — `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.request.model` captured per LLM span, turned into per-request cost breakdowns ([OTel GenAI blog 2026](https://opentelemetry.io/blog/2026/genai-observability/), [Uptrace LLM cost](https://uptrace.dev/blog/llm-cost-monitoring)). Status: **most GenAI conventions still experimental as of Mar 2026** ([DEV/OTel conventions](https://dev.to/x4nent/opentelemetry-genai-semantic-conventions-the-standard-for-llm-observability-1o2a)); Langfuse maps OTel→its model ([Langfuse OTel](https://langfuse.com/integrations/native/opentelemetry)). **Verdict for Ragbot**: OTel-span is the trace-side standard but is (a) experimental, (b) currently a disjoint no-op system here (`tracing.py:49-55`). Ragbot already has a **relational** per-call ledger substrate (`request_steps` with `cost_usd`+`step_name`+FK to `request_logs`) — for a billing/per-tenant-ledger use-case the **DB child table is the correct primitive** (queryable, joinable, survives sampling; OTel spans get sampled/dropped). EVOLVE = formalize `request_steps`→`request_llm_calls` (Q21) for the ledger, keep OTel for traces, and add `gen_ai.*` attrs to spans when conventions stabilize. Do NOT make billing depend on experimental sampled spans.

---

## 2. CONFIG-DRIFT LIST + FIX

### DRIFT-1 (🐛 verified) — `init_system_config.py` diverged from alembic 0020
| key | alembic 0020 | init script | factor |
|---|---|---|---|
| `llm_default_max_tokens` | `"450"` (`...0020...py:24`) | `"1024"` (`init_system_config.py:30`) | 2.3× |
| `rag_rerank_top_n` | `"5"` (`:32`) | `"10"` (`:38`) | 2× |
| `rag_top_k` | literal `"20"` (`:31`) | `str(DEFAULT_TOP_K)`=20 (`:37`) | value-equal, **source mismatch** (literal vs import) |

Init also seeds keys absent from 0020: `bm25_normalization_flags`, `bm25_use_cover_density` (`init_system_config.py:39-40`). **A fresh DB seeded by the script gets a 2.3× larger answer budget + 2× rerank set than one migrated via alembic** — silent perf/cost/quality divergence depending on bootstrap path.

**FIX (EVOLVE, surgical):** Make alembic 0020 the sole bootstrap-of-record; **delete the duplicate seed list in `init_system_config.py`** and have the script call the migration's `SEED_CONFIGS` (import the list) or run `alembic upgrade head` then UPSERT only the genuinely script-only keys. Keys not in any migration (`bm25_*`) → add a forward migration so they're tracked (Application MINDSET rule: no out-of-band content state). Effort ~1h. Risk LOW (changes a dev-bootstrap path, not runtime).

**CI test sketch (don't commit):**
```python
# tests/unit/test_seed_paths_agree.py
def test_init_script_matches_alembic_0020():
    alembic_seed = {k: v for k, v, *_ in _import("...0020...").SEED_CONFIGS}
    script_seed  = {k: v for k, v, *_ in _import("scripts.init_system_config").SEED_CONFIGS}
    shared = alembic_seed.keys() & script_seed.keys()
    drift = {k: (alembic_seed[k], script_seed[k]) for k in shared if alembic_seed[k] != script_seed[k]}
    assert not drift, f"seed drift: {drift}"   # today FAILS on llm_default_max_tokens, rag_rerank_top_n
```

### DRIFT-2 (🐛 verified) — `validate_constants.sh` is a dead guard
`validate_constants.sh:18` targets `src/ragbot/shared/constants.py`; that file was deleted by split `1446fef` (now `constants/` package, `__init__.py` present). Lines `:19-20` `if [ ! -f ]: echo "...not found"` → the hook **prints a warning and exits 0**: version-ref + temporal-comment checks (`:27,:35`) never execute. The 22 module names include date-refs (`_17_260509_*`, `_21_streaming_upload_wb_2_p1_5`) that this guard was meant to catch — it now catches nothing.

**FIX:** Repoint to the package: `CONSTANTS_DIR=src/ragbot/shared/constants` and `grep -rnE ... "$CONSTANTS_DIR"`. Effort ~15min. Risk LOW. (Then triage the `_NN_<date>_*` module names against no-version-ref rule — separate scope.)

### DRIFT-3 (context, not a fix target) — migration-freeze + later UPSERTs
0020 freezes string literals; later alembics UPSERT (0057/0067/0068/0085/0190-0191) → live value = 0020 XOR latest UPSERT, no single file shows current state. **Legitimate** (migration history is immutable by design) but **undocumented as a hazard**. EVOLVE = the Q22 lint reads the *latest* UPSERT per key, not 0020 blindly.

---

## 3. COST-ATTRIBUTION GAP + `request_llm_calls` DESIGN (Q21)

### What P1-G got right and what I revise
P1-G GAP-1 said "Cost-per-pipeline-stage per-tenant is NOT recoverable from DB." **Re-verified: this is too strong.** `request_steps` rows ARE written with `step_name`, `record_tenant_id`, `model_used`, `input_tokens`, `output_tokens`, `cost_usd` (`models_monitoring.py:175-190`), populated by `record_llm()` at 12 LLM nodes (`query_graph.py` callers above; persist `request_log_repository.py:283-285`). The `purpose` Prometheus labels (`generation/grading/grounding/decompose/hyde/rewriting/condensing/reflection/routing/multi_query/understand_query` — `grep purpose=` = 14 values) map ~1:1 onto these `step_name`s. **So per-stage per-tenant cost IS in the DB today** — joined `request_steps.cost_usd GROUP BY step_name, record_tenant_id`.

### The REAL residual gap (3 holes, all verified)
1. **No read-query sums it.** The only step-aggregate query sums `duration_ms` only — `request_log_repository.py:582-592` (`func.avg(duration_ms)`, `percentile_cont` on duration); `cost_usd` is never aggregated. Data exists, no report exposes it. *(Cheapest fix of the three — add a cost-by-step query, ~30min.)*
2. **Ingest LLM spend = 0 / unledgered (worst).** Ingest narrate/enrich/metadata LLM calls return hardcoded `"cost_usd": 0.0` (`document_service.py:3881`) and ingest steps have **no `request_logs` parent** (`request_steps.record_request_id` FK requires a chat turn). So CR-enrichment + Haiku narrate batch (`anthropic_haiku_batch.py` `estimate_batch_cost_usd` exists `:214` but isn't written to any ledger) are **invisible to the per-tenant cost ledger entirely.** This is the worst gap: ingest can be the dominant spend for a large-corpus tenant and it's $0 on the books.
3. **`purpose` is `step_name`-coupled, not a first-class column.** Recoverability today relies on `step_name` semantics being stable; a refactor renaming a node silently breaks historical cost-by-purpose. And `request_logs.cost_usd` per-turn (`request_log_repository.py:138`) carries no purpose breakdown — only the child rows do.

### Q21 — `request_llm_calls` child table design
**Recommendation: do NOT add a 3rd parallel table. EVOLVE the existing `request_steps` into the canonical per-call ledger** (it already has 90% of the columns) + a sibling ingest path. Concretely:

**Option A (preferred, low-risk) — promote `request_steps` to the ledger:**
- Add `purpose VARCHAR(32)` column (alembic) so cost-by-purpose survives step renames; backfill `purpose := step_name`.
- Add the missing read-query (cost-by-(tenant,purpose,model)).
- For ingest: allow `record_request_id` NULL OR introduce a parent `ingest_jobs` row, and write real `cost_usd` from `estimate_batch_cost_usd` instead of `0.0`.

**Option B (if a clean per-call ledger is preferred) — new `request_llm_calls` (the Q21 design):**
```sql
CREATE TABLE request_llm_calls (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_request_id  UUID NULL REFERENCES request_logs(request_id) ON DELETE CASCADE, -- NULL for ingest
    record_tenant_id   UUID NOT NULL,
    workspace_id       VARCHAR(64) NOT NULL,
    record_bot_id      UUID NULL,
    purpose            VARCHAR(32) NOT NULL,        -- generation|grading|grounding|decompose|hyde|narrate|enrich...
    kind               VARCHAR(8)  NOT NULL DEFAULT 'chat',  -- chat | ingest  (decouples ingest from request FK)
    record_model_id    UUID NULL,
    model_name         VARCHAR(128) NULL,
    record_binding_id  UUID NULL,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(12,6) NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_rllm_kind CHECK (kind IN ('chat','ingest'))
);
CREATE INDEX ix_rllm_tenant_purpose ON request_llm_calls (record_tenant_id, purpose, created_at);
CREATE INDEX ix_rllm_request        ON request_llm_calls (record_request_id);
CREATE INDEX ix_rllm_tenant_bot     ON request_llm_calls (record_tenant_id, record_bot_id, created_at);
```
- Writer: replace `complete_runtime(purpose=...)`'s Prometheus-only emit (`dynamic_litellm_router.py:432`) with a ledger insert carrying the real `purpose`; the 12 chat nodes already pass `cost_usd` to `record_llm` so the value is in-hand. Ingest path inserts `kind='ingest', record_request_id=NULL`.
- Keep RLS-scoped (`record_tenant_id` first index column) — must be covered when RLS hook is finally wired.

**Effort:** Option A ~3-4h (1 alembic + 1 read-query + ingest writer). Option B ~6-8h (new table + dual-write + reconcile vs `request_logs.total_tokens`). **Risk:** A LOW (additive column on existing populated table), B MED (a second cost source of truth must reconcile to per-turn aggregate or you get double-count). **Recommendation: Option A** — Simplicity-First; the table to make canonical already exists and is already populated. Reserve Option B only if ingest+chat must share one queryable surface and the NULL-FK on `request_steps` is judged too leaky.

---

## 4. CONFIG-LINT DESIGN (Q22) — 4-way value-equality lint

**Assertion per shared key:** `constants.DEFAULT_X` == `init_seed[x]` == `PLAN_LIMIT_SCHEMA[x]["default"]` == `latest_alembic_upsert[x]`.

```python
# tests/unit/test_config_4way_lint.py  (sketch — do not commit yet)
import re, importlib, pathlib
from ragbot.shared import constants
from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA

# Map: shared system_config key -> constants symbol (the only hand-maintained bit)
KEY_TO_CONST = {
    "llm_default_max_tokens": "DEFAULT_LLM_MAX_TOKENS",
    "rag_top_k":              "DEFAULT_TOP_K",
    "rag_rerank_top_n":       "DEFAULT_RERANK_TOP_N",
    # ... extend to every key present in >=2 of the 4 sources
}

def _latest_alembic_upsert(key):
    # scan alembic/versions/*.py newest->oldest for ("<key>", "<value>"...) or UPDATE ... WHERE key='<key>'
    ...

def test_four_way_config_agreement():
    init = {k: v for k, v, *_ in importlib.import_module("scripts.init_system_config").SEED_CONFIGS}
    drift = []
    for key, const_name in KEY_TO_CONST.items():
        c = str(getattr(constants, const_name))
        s = PLAN_LIMIT_SCHEMA.get(key, {}).get("default")
        i = init.get(key)
        a = _latest_alembic_upsert(key)           # newest migration wins (handles DRIFT-3)
        vals = {n: x for n, x in (("const",c),("schema",s),("init",i),("alembic",a)) if x is not None}
        if len(set(map(str, vals.values()))) > 1:
            drift.append((key, vals))
    assert not drift, f"4-way config drift: {drift}"
```

**Keys currently drifted (verified this audit):**
- `llm_default_max_tokens` — const/0020 = `450`, init = `1024` → **DRIFT** (factor 2.3×).
- `rag_rerank_top_n` — 0020 = `5`, init = `10` → **DRIFT** (factor 2×).
- `rag_top_k` — values equal (20) but **source-form drift** (0020 literal `"20"` vs init `str(DEFAULT_TOP_K)`): passes value-equality, would be caught by a source-form sub-check (optional).

**Design notes:** (1) lint must read the *latest* alembic UPSERT (not 0020) to be correct under DRIFT-3; (2) only `KEY_TO_CONST`-mapped keys are checked — keys living in only one source (operator-global like `max_total_graph_iterations`) are correctly out of scope; (3) wire as a `pytest` unit so it's a per-commit gate (the missing "eval-in-CI"/"drift-in-CI" SOTA gap from P1-G e.2). **Effort ~3h** (the `_latest_alembic_upsert` scanner is the only non-trivial part). **Risk LOW** (test-only).

---

## 5. TIER-COUNT CHỐT (↔️ resolved)

**Verdict: there are 5 enforced resolve tiers; the "6th" is a comment, not code.** Cited each, top→bottom, all in `bot_limits.py:resolve_bot_limit` (`:376-456`):

1. `bot_cfg.threshold_overrides[key]` — JSONB per-bot override — `:410-411,423`
2. `bot_cfg.<key>` dedicated hot-path column (`max_documents, max_history, prompt_max_tokens, rerank_top_n` via `_COLUMN_KEYS` `:373`) — `:404-406`
3. `bot_cfg.plan_limits[key]` — JSONB, clamped by `validate_plan_limits` `:515` — `:414-415`
4. `system_default` — the `system_config` row the caller passes — `:420`
5. `PLAN_LIMIT_SCHEMA[key]["default"]` — mirrors a `DEFAULT_*` constant — `:419-420`

The charter's **"6-tier"** counts a **workspace/tenant layer** that exists **only as a comment** — `chat_worker.py:1410` `"bot.col -> plan_limits -> workspace_config -> tenants ->"`. There is **no `workspace_config` read in `resolve_bot_limit`** (grep: the resolver never references workspace). Consistent with P1-SYNTHESIS §2 "workspace = slug, chưa entity" and §5 conflict item. **Chốt: 5 enforced tiers + a constants caller-side fallback (the `_pcfg(state, key, DEFAULT_X)` pattern in `query_graph.py`) as an implicit 6th *outside* the resolver.** If you want to honestly call it "6-tier", the 6th is *constants-as-ultimate-fallback*, NOT workspace. The workspace tier is aspirational (depends on workspace becoming an entity — same blocker as RLS `app.workspace_id` GUC).

---

## 6. ĐÃ CHUẨN — ĐỪNG ĐỤNG (don't refactor these — breaking them is the worst regression)

1. **5-tier resolve chain with min/max range-guard** (`bot_limits.py:376-456`) — per-key schema + write-time clamp + out-of-range silent fallback is correct fail-safe design, ahead of typical RAG stacks. Adding tiers or removing the clamp = regression.
2. **33-step `request_steps` instrumentation** (`query_graph.py`, write `request_log_repository.py:241-300`) — P1-G's own MEMORY note "12 live / 15 missing" is OBSOLETE; all top-5 previously-missing steps now present. This is genuinely good observability — don't rip it out for OTel; *correlate* OTel to it instead.
3. **Per-step LLM cost capture already wired** (`record_llm()` + 12 callers) — the substrate for cost-by-purpose already exists and is populated. Q21 should EVOLVE this, not replace it. Building a parallel ledger that double-counts vs `request_logs.cost_usd` (`request_log_repository.py:138`) is the trap to avoid.
4. **Config propagation via outbox + Redis invalidation** (`system_config_service.py:62-66`) — no-redeploy config flip is 12-factor-correct. Keep.
5. **Eval harness: DB ground-truth + LLM-judge + failure-layer attribution + 3-run flip detection** (`loadtest_graded.py`) — ahead of RAGAS-only setups; flip-detection ≈ determinism testing. Don't simplify away the 3-run flip logic.
6. **Migration-as-bootstrap-of-record principle** (alembic 0020) — the fix for DRIFT-1 is to make the script defer to the migration, NOT to "sync" the script by editing 4 places again. The principle is right; the second seed path is the bug.

---

### Re-verified orphan plans (did NOT trust plan self-claims — grepped the feature in src)
- **#19 query-graph-split** (`260609-query-graph-split`, claims anchor `bf5b77f`) → `wc -l query_graph.py` = **8087 lines**, unchanged → **TRUE ORPHAN, code absent.** (plan claim contradicted by code.)
- **#15 multitenant-hardening RLS hook** (`260608-multitenant-hardening`, claims "Phase 0 migrations landed") → `attach_rls_session_hook` `session.py:154` has **0 production callsites** (3 grep hits all in `session.py`: docstring/def/`__all__`) → **DOING, hook NOT wired.** Migrations real, enforcement dead. (matches `260610-ga-hardening` ISSUE-1 P0.)
- **#4 action-architecture L3** (`260604-expert-rag-action-architecture`, claims L3 tool-use exec) → no `ActionExecutor`/`execute_action`/`tool_use_loop` class anywhere in `src/ragbot/` (dedicated grep = 0 hits) → **L3 TRUE ORPHAN** (L2 `action_config` DTO/ORM real per P1-G; L3 execution absent).

All three confirm the project's self-named meta-pattern: **"built-but-not-wired."**
