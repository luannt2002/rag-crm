# [T2-Security] RLS enforcement — request/system role split

**Status:** Phase 0 DONE (evidence + grant migration written, NOT applied).
Phases 1–3 GATED on user approval (live-DB mutation + code surgery + DSN flip).

**Tier:** T2 (tenant-isolation hardening / defense-in-depth). NOT a live hole —
app-layer scoping (every internal query filters `record_bot_id`/`record_tenant_id`)
is already solid; RLS is the *second* layer that catches an app-layer bug. So this
does not preempt T1 smartness work; it is opt-in correctness.

---

## 1. Bug / gap (evidence)

RLS needs 3 layers ALL live (`infrastructure/db/session.py` docstring):
1. policies on `app.tenant_id` — **LIVE**: 20 tables FORCE RLS, 21 policies.
2. a NOBYPASSRLS login role the app connects as — **DEAD**.
3. per-txn `SET LOCAL app.tenant_id` — **BUILT** (ADR-W1-D3 `after_begin` hook,
   wired in bootstrap via `create_rls_session_factory`), inert under superuser.

Live-DB evidence (`psql`, 2026-06-19):
- App connects as `ragbot` = `rolsuper=t, rolbypassrls=t` → **every policy bypassed** (RLS inert).
- `ragbot_app` exists but `rolcanlogin=f` + **0 table grants** → cannot be used.
- `.env`: `DATABASE_URL`, `DATABASE_URL_SYNC`, `DATABASE_URL_APP` ALL → `ragbot`.
- Squash baseline `20260618_squash_baseline.py` has **no** GRANT/CREATE ROLE DDL →
  role provisioning was lost in the squash (a fresh clone can't enforce RLS at all).

## 2. Direct cause

The app has a **single** engine: `bootstrap.Container.db_engine = create_engine_app`
(→ `DATABASE_URL_APP`). Every repo + every embedded worker shares the one
RLS-hooked `session_factory`. `create_engine` (`DATABASE_URL`) is alembic/ops-only.

## 3. Root cause (immutable)

Background/system jobs legitimately run **cross-tenant** (no single tenant ctx),
but share the one factory that — once it points at NOBYPASSRLS `ragbot_app` — will
fail-closed (0 rows) on the RLS-forced tables they scan. Flipping the DSN naively
breaks FOUR paths (all `container.session_factory()`, no tenant ctx bound):

| Worker | RLS-forced table, cross-tenant | Break under ragbot_app |
|---|---|---|
| `outbox_publisher` (`run_outbox_loop`, `outbox_repo` line 464) | `outbox` FOR UPDATE SKIP LOCKED drain | publishing **stuck** |
| `recovery_worker._scan_stuck_documents` (worker line 318 plain `session_factory()`) | `documents` + `outbox` scan | recovery **blind** |
| `cache_purge` (C1, embedded_workers line 183) | `semantic_cache` DELETE | GC **no-op** |
| `cost_cap_alerter` → `evaluate_tenants` | **`request_logs`** aggregate (RLS-forced) | alerter **blind** |

GLOBAL config tables are correctly RLS-OFF (`system_config`, `ai_models`,
`ai_providers`, `language_packs`, `token_ledger`, `tenants`) → platform/config
reads survive enforcement. Only the 4 cross-tenant scans above are at risk.

The HTTP **request** path is safe: `TenantContextMiddleware` binds `tenant_id_ctx`
→ the D3 hook issues `SET LOCAL app.tenant_id` → RLS enforces correctly. The
document **consumer/ingest** path operates on ONE tenant's doc (tenant in the
event payload) — needs verification that it binds `tenant_id_ctx` per event
(open item P2-a).

## 4. Expert solution — request/system role split (industry standard)

The canonical RLS + background-jobs pattern (PostgREST / Supabase / Postgres RLS
cookbook): **enforce RLS on the user-facing request role; run trusted system jobs
on a privileged connection.** EVOLVE, not rewrite — the codebase already has the
seam (`create_engine` admin vs `create_engine_app` runtime).

- **Request role** `ragbot_app` (NOBYPASSRLS): HTTP request `session_factory`. RLS enforces.
- **System engine** (privileged): a 2nd factory WITHOUT the RLS hook for the 4
  cross-tenant workers. Two role options — **DECISION NEEDED**:
  - **A. reuse `ragbot`** (`DATABASE_URL`, superuser). Zero new provisioning, but
    workers keep superuser privilege.
  - **B. dedicated `ragbot_system`** (NOSUPERUSER + **BYPASSRLS** + DML grants).
    Least-privilege (can't DROP TABLE); one more role to provision. *(Recommended
    — it's the defense-in-depth this whole effort is for.)*

## 5. Phases

### Phase 0 — DONE (no live mutation)
- [x] Evidence gathered (roles, RLS tables, engine routing, 4 broken paths).
- [x] `alembic/versions/20260619_rls_app_role_grants.py` — provisions `ragbot_app`
      LOGIN (no password) + DML grants + default privileges. Idempotent, reversible.
      **Written, NOT applied.**

### Phase 1 — GATED: apply grants + runtime isolation PROOF (rule #0)
- [ ] `alembic upgrade head` against live dev DB (provisions `ragbot_app`; inert —
      app still uses `ragbot`).
- [ ] Probe AS `ragbot_app` (temp ops password, reset to NULL after): prove
      `SET app.tenant_id=<c2f66cb2…>` → 9 docs; wrong tenant → 0; no-ctx → 0
      (fail-closed). This is the runtime proof the mechanism enforces.

### Phase 2 — GATED: 2-engine code split (no-op under current .env; both DSNs=ragbot)
- [ ] `bootstrap.py`: add `db_engine_system` (`create_engine`) + `system_session_factory`
      (`create_session_factory`, NO RLS hook).
- [ ] Reroute the 4 cross-tenant workers to `system_session_factory`:
      `outbox_repo` (line 464) → system; `cache_purge` + `cost_cap_alerter` use
      `container.system_session_factory()`; `run_recovery_loop` scan uses system factory.
- [ ] P2-a: verify `handle_document_uploaded` binds `tenant_id_ctx` from the event
      payload; if not, add the bind (so ingest enforces correctly under ragbot_app).
- [ ] Tests: pin that the 4 system workers use the system factory; pin migration role
      name == `session.py::RUNTIME_DB_ROLE`; existing unit suite green.

### Phase 3 — GATED: the flip + load-test gate (HIGH RISK, ~irreversible window)
- [ ] Ops: `ALTER ROLE ragbot_app PASSWORD '<gen>'`; set `DATABASE_URL_APP` →
      `ragbot_app` DSN in `.env` (NOT tracked).
- [ ] Restart; **load-test gate**: HALLU=0 + coverage intact on 3 bots + verify
      outbox publishes, recovery scans, cache-purge deletes, cost-cap flags (the 4
      workers on the system engine still see all tenants).
- [ ] Monitor `rls_session_hook_bad_tenant_ctx` + any fail-closed 0-row regressions.
- [ ] Rollback = revert `.env` DATABASE_URL_APP → `ragbot` (1 line, instant).

## 6. CLAUDE.md compliance
- Rule #0: every claim above is psql/code-evidenced; Phase 1 probe = runtime proof
  before any "RLS enforces" assertion.
- No psql content hot-fix: grants via tracked alembic; password ops-side (secret).
- EVOLVE not rewrite: reuse existing engine seam + D3 hook; add a 2nd factory.
- Domain-neutral, no version-ref, zero-hardcode (role name pinned to constant).
- Async rule 7: system factory is a separate engine (no cross-session gather).
