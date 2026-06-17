# P1-C · Multi-tenancy / Security — CONTEXT (Phase 1, UNDERSTAND only)

> Synthesizes the pre-seed (`P1-C-PRESEED-multitenancy.md`) + new git-evolution / full-path / RBAC / plans research.
> Every claim = `file:line` or `commit`/`migration`. Phase 1 = describe, NOT judge.
> STANCE = EVOLVE not rewrite. Pre-seed already covered: RLS hook 0-callsite, 23 policies, repo session split, semantic-cache scoping, worker GUC, workspace-slug-only, RBAC-global, quota tenant-only, FK cascade, embedding dim. This doc ADDS: pre-seed verification (§0), the timeline (§a), the JWT→query path (§b), RBAC map (§c), plan status (§d), SOTA HAS/LACKS (§e), open questions (§f).

---

## (0) Pre-seed verification — 3 claims re-grepped (2026-06-10)

| Pre-seed claim | Re-verify | Verdict |
|---|---|---|
| `attach_rls_session_hook` 0 production callsite | grep `src/ragbot` + `scripts/`: only def `src/ragbot/infrastructure/db/session.py:154` + export `:182`; `bootstrap.py:160-163` builds engine+factory, no attach | ✅ CONFIRMED |
| Repos bare `_new_session()` no GUC | def at `src/ragbot/infrastructure/repositories/_base.py:30` — **pre-seed path/line drift**: it cites `repositories/_base.py:34` and implies `db/repositories/`; actual package is `infrastructure/repositories/`. Callsites confirmed: `bot_repository.py:145,180,199,218,253,277`, `conversation_repository.py:94,132,162`, `document_repository.py:99,127,157,196,217,250,327`, `job_repository.py:34,64,91` | ✅ CONFIRMED (path corrected) |
| Semantic cache filters tenant+bot before cosine | WHERE `record_bot_id AND record_tenant_id` at `semantic_cache.py:419-430` (exact-hash) + `:479-490` (cosine) — minor line drift vs pre-seed 416-434/474-496 | ✅ CONFIRMED |

**NEW evidence beyond pre-seed (resolves former open-Q1):** `.env:108` SETS the escape env `RAGBOT_ALLOW_SUPERUSER_RUNTIME` (constant `shared/constants/_17_260509_a1_pipeline_audit_6_c.py:12-13`) and `.env` contains **NO `DATABASE_URL_APP`** (grep -c = 0). So `create_engine_app` (`engine.py:60-92`) takes the explicit superuser fallback with WARNING `engine.app_dsn_superuser_fallback` (`engine.py:77-80`). **FACT: dev/current runtime = superuser, RLS bypassed — but explicit + observable, not silent.**

---

## (a) Schema evolution timeline — tenant / workspace / bot identity / RLS

| When | Migration / commit | Change | WHY (cite docstring) |
|---|---|---|---|
| 2026-04-15 | `0001_initial_schema` | bots/conversations carry an INT `tenant_id` (transitional upstream bridge) | initial |
| 2026-04-16 | `0006_tenant_nullable_channel_type`, `0012_tenant_id_nullable` | `tenant_id` allowed NULL | early multi-channel work |
| 2026-04-21 | **`0034_rename_record_prefix`** | `documents/conversations.tenant_id → record_tenant_id` (and other UUID FKs) | "Rename internal UUID FK columns to `record_` prefix" — naming convention split EXTERNAL vs INTERNAL |
| 2026-04-22 | **`0036_rbac_tables`** | `role_definitions` (col `scope VARCHAR(32) NOT NULL DEFAULT 'workspace'`) + `module_permissions` | "Roles and permissions defined in DB, not code. New role = SQL INSERT." — **scope='workspace' default declared here but never enforced per-workspace (see RBAC map)** |
| 2026-04-24 | **`0039_bots_tenant_unique`** | UniqueConstraint `(tenant_id, bot_id, channel_type)` | "Tenant A and B could both create bot_id='support' … composite was not unique across tenants → `find_by_bot_channel` may return wrong tenant when upstream leaks slugs" |
| 2026-04-28 | **`0041_bots_tenant_id_not_null`** | `bots.tenant_id` → NOT NULL | "NULL tenant_id = bot sits outside any tenant scope → silently bypasses tenant-scoped queries + two NULL-tenant rows collide on (bot_id, channel_type)" |
| 2026-04-29 | **`0049_nullable_tenant_id_to_not_null`** | `record_tenant_id` NOT NULL on 14 secondary tables | "P0-BUG-3 audit: 14 tables nullable. App guards via `_ensure_tenant()` but DB does not → a bug bypassing app layer persists NULL tenant rows → silent cross-tenant leak" |
| 2026-05-03 | **`0058_bots_tenant_uuid`** | `bots.tenant_id` INT → `record_tenant_id` UUID FK `tenants(id)` | "Closes schema drift where `bots.tenant_id` was the only INT tenant ref … External body NO LONGER carries tenant_id; JWT bearer carries `record_tenant_id` UUID claim; middleware lifts onto state. 3-key identity remains `(record_tenant_id, bot_id, channel_type)`" |
| 2026-05-04 | **`0062_workspace_4key_identity`** | `workspace_id VARCHAR(64) NOT NULL` on `bots` + 16 data tables; unique `(record_tenant_id, workspace_id, bot_id, channel_type)` | "slug is pass-through value supplied by tenants — same tenant can isolate teams without provisioning new tenants. Backfill `bots.workspace_id ← str(record_tenant_id)`" → **identity 3-key → 4-key** |
| 2026-05-08 | **`0069_enable_rls_tenant_isolation`** | RLS ENABLE + 23 `CREATE POLICY` on tenant tables; JOIN-based policy for `document_chunks`/`knowledge_edges` (no direct tenant col) | "App already calls SET LOCAL app.tenant_id … but until policy enabled, any raw-SQL path that forgets WHERE = cross-tenant leak. `current_setting('app.tenant_id', true)` — `true` = admin-shell sessions w/o tenant see [zero rows] not error. FOR ALL (read+write). RLS opt-in" |
| 2026-05-09 | **`0073_create_ragbot_app_role`** | `CREATE ROLE ragbot_app NOSUPERUSER NOBYPASSRLS LOGIN` (password via GUC) | "superuser & BYPASSRLS roles ignore policies … until app connects as non-superuser, RLS layer is dead and isolation rests on app layer" |
| 2026-05-16 | **`0108_chunks_record_bot_id`** | denormalize `record_bot_id` onto `document_chunks` (HNSW activate) + tighten chunk RLS | "stop planner pushing bot-isolation predicate INTO HNSW; tenant isolation policy on document_chunks now [direct]" |
| 2026-05-29 | **`0141_workspace_aware_rls`** | extend 23 policies with `app.workspace_id` second USING clause | "Audit found 0/23 RLS policies enforce workspace_id; all 23 only filter record_tenant_id … two workspaces under same tenant can see each other's rows if app forgets WHERE. GUC `app.workspace_id`" — **NOTE: this GUC is never SET in code (see break-point B5)** |
| 2026-06-08 | **`0186_create_app_role_grants`** + **`0187_rls_policies_use_setting`** (commit `5d2a25b` "S1") | re-assert `ragbot_app NOSUPERUSER NOBYPASSRLS NOLOGIN` + DML-only grants; DROP+CREATE every policy to compare `current_setting('app.tenant_id', true)::uuid` (no literal skips) | "app historically connects as superuser (rolbypassrls) so 0069/0141 policies are dead … enforce-gate re-assertion so drifted env (manual CREATE POLICY edit, partial history) snaps back to canonical" |

**One-liner:** INT `tenant_id` (0001) → `record_tenant_id` UUID rename (0034) → NOT-NULL hardening (0041/0049) → UUID FK (0058) → **4-key `workspace_id`** (0062) → RLS policies (0069) → app-role (0073) → workspace-aware RLS (0141) → enforce-gate re-assert (0186/0187, 2026-06-08) — **but the runtime is still superuser + the per-tx hook is default-OFF, so all 23 policies are inert in practice.**

---

## (b) `record_tenant_id` full path: JWT → contextvar → GUC → query (with break-points)

Hop-by-hop (file:line):

1. **JWT decode** — `tenant_context.py:118-124` `JwtTokenService` validates Bearer; `role = payload.get("role", "service")` (`:124`).
2. **Claim resolve** — `:131-137` `rt_claim = payload.get("record_tenant_id")` → `UUID(...)`; legacy INT fallback `:138-144` `_resolve_upstream_int_tenant(...)` (tenants.config lookup `:69`).
3. **Reject if missing** — `:146-162` non-owner/super_admin without tenant claim → HTTP 401 `tenant_claim_required`.
4. **Bind to request.state** — `:382` `request.state.role = role`; record_tenant_id bound onto state (module docstring `:6`).
5. **Bind to contextvars** — `:383` `bind_request_context(...)` sets `tenant_id_ctx` (def `config/logging.py:25`, default `"UNSET"`) + `bot_id_ctx`/`user_id_ctx`. Same helper also used by `trace_context.py:25` and worker `chat_worker.py:491` / `document_worker.py:73`.
6. **Session GUC (tenant-scoped path)** — `engine.py:124` `tenant_id_ctx.set(tid_str)` / `:126 .get()` → `:142 _assert_uuid_str` → `:143` `SET LOCAL app.tenant_id = '<uuid>'`. Raises `RuntimeError` if ctx unbound (`:130-134`) — fail-loud, no silent skip.
7. **UoW enforce** — `uow.py:44-47` reads `tenant_id_ctx.get()`, raises if unbound.
8. **Policy filter** — Postgres `current_setting('app.tenant_id', true)::uuid` (0069/0187).

**Break-points (where the chain does NOT reach the query):**

- **B1 — RLS hook not wired (BLOCKER, pre-seed §1).** `attach_rls_session_hook` (`session.py:154`) is **default-OFF/opt-in** (docstring `session.py:30`), **0 production callsite** — only `tests/unit/test_rls_set_local.py:112/116/137`. `bootstrap.py:159-163` builds `session_factory` but does NOT attach it. So sessions NOT opened via `session_with_tenant` never run `SET LOCAL`.
- **B2 — Bare repo sessions skip GUC (pre-seed §1).** `repositories/_base.py:34`, `bot_repository.py:145`, `conversation_repository.py:94`, `document_repository.py:99` use `_new_session()` with no GUC. Isolation relies on app-level `record_tenant_id`/`record_bot_id` WHERE only.
- **B3 — Runtime role still superuser.** Even where `SET LOCAL` IS set (pgvector `pgvector_store.py:361-363`), `postgres` is `rolsuper=t rolbypassrls=t` (plan `260608-multitenant-hardening` Phase 0 measured 2026-06-08) → policies ignored. Gated by `DATABASE_URL_APP` (`engine.py:70`); when unset + escape env, `create_engine_app` warns and falls back to admin DSN (`engine.py:60-81`). Bootstrap DOES use `create_engine_app` (`bootstrap.py:160`) — gate is live, and **VERIFIED 2026-06-10**: `.env:108` sets the escape env + no `DATABASE_URL_APP` key → runtime IS on the superuser fallback (see §0).
- **B4 — `_upsert_doc_summary` bare session (pre-seed §3).** `document_service.py:905` `self._sf()` no GUC (PK-scoped, low risk).
- **B5 — `app.workspace_id` GUC never set (NEW, most subtle).** 0141 made all 23 policies workspace-aware via a SECOND USING clause on `current_setting('app.workspace_id')`, but **no code path ever executes `SET LOCAL app.workspace_id`** — grep finds only a docstring ref (`jsonb_conversation_state.py:8`) and the policy SQL itself. `session_with_tenant` (`engine.py:104-148`) sets ONLY `app.tenant_id`. So the workspace clause degrades to its tenant-only fallback: two workspaces under one tenant are NOT RLS-isolated even after the hook is wired. (0187 docstring acknowledges "workspace-aware shape" but the GUC supply side is missing.)

---

## (c) RBAC map — 7-tier, global-per-tenant (confirms pre-seed §4)

- **Levels** — `shared/rbac.py:19-33` `ROLE_LEVELS`: super_admin/owner/platform_admin/system=100, tenant/tenant_admin=80, admin=60, service=60, operator=40, user=20, viewer=10, guest=0. Gaps of 20 "for future insertion" (docstring `:4`). Unknown role → 0/guest (`get_role_level :37`). Enforce via `require_min_level(request, N)` (`:50-53`), reads `request.state.role` (`check_min_level :41`).
- **Claim parse** — single role STRING from JWT: `tenant_context.py:124` `payload.get("role", "service")` → `:382` `request.state.role = role` (service path) and `:423` `claims.get("role", "user")` (user path). **One role per token, tenant-wide — NOT per-workspace.**
- **DB metadata** — `role_definitions` (migration `0036`) has `scope VARCHAR(32) DEFAULT 'workspace'`; DB-driven RBAC middleware `rbac.py:3` reads `role_definitions + module_permissions` (Redis-cached 5min). BUT there is **no `workspace_members` / `workspace_roles` mapping table** — grep for `scope.*workspace` in code finds only docstrings/comments, no per-workspace role resolution. The `scope='workspace'` column value is declared but unused as an enforcement dimension.
- **Verdict (Phase-1 description, not judgment):** a tenant-admin (level 60+) is admin over ALL workspaces of that tenant; workspace is an isolation/labelling slug, not an RBAC boundary. Matches pre-seed §4 "RBAC global per-tenant" with the parse site now pinned to `tenant_context.py:382/423`.

### bot_registry_service — 4-key resolve + Redis cache key
- `application/services/bot_registry_service.py:103-126` `lookup(record_tenant_id, workspace_id, bot_id, channel_type)` — all 4 required; rejects empty workspace/bot/channel (`:124`).
- Redis cache key — `:71-73` `f"{REDIS_PREFIX}:{record_tenant_id}:{workspace_id.strip()}:{bot_id.strip()}:{channel_type.strip()}"` (docstring `:3`). Matches CLAUDE.md `ragbot:bot:{tenant}:{ws}:{bot}:{channel}`.
- Warm-load `:78` `list_active(record_tenant_id=None)` then indexes by 4-key tuple (`:90-93`).

---

## (d) Related plans — status

| Plan | Status (from plan.md header) | Scope |
|---|---|---|
| `plans/260608-multitenant-hardening/plan.md` | **Phase 0 investigated (DONE, evidence 2026-06-08); Phases 1–5 NOT started.** Anchor `df044d6`. | RLS enforce (app role + per-conn GUC + policy verify + DATABASE_URL switch + pgbouncer caveat), gap close, governance. Phase-0 measured `current_user=postgres rolsuper=t rolbypassrls=t` → "RLS BYPASSED 100%". |
| `plans/260610-ga-hardening/plan.md` | Active; anchor `dcf63a4`; source `reports/DEEPDIVE_ALL_20260609.md`. ISSUE 1 = P0 RLS (1a alembic role [shipped via 0186/0187], 1b leak-test, **1c wire `attach_rls_session_hook` in bootstrap — NOT done**, 1d ops set `DATABASE_URL_APP`). ISSUE 2 = retrieval determinism (reverted, commit `2f5ed41`). ISSUE 3 = silent-degrade. | GA gate. |
| commit `5d2a25b` "S1" (2026-06-08) | Shipped alembic `0186`+`0187` + `session.py` opt-in hook + ADR `0001-rls-enforce-app-role` + 8 tests. **Hook landed but default-OFF and unwired** → step 1c of ga-hardening still open. | RLS infra ship. |

So: RLS DB-side infra is COMPLETE (role + 23 policies re-asserted), but the two activation steps — (1c) wire the hook in bootstrap, (1d) set `DATABASE_URL_APP` to the `ragbot_app` DSN in prod ops env — are both PENDING. Until both land, isolation = app-level WHERE only.

---

## (e) vs SOTA SaaS multi-tenancy 2026 — HAS / LACKS (objective)

**HAS**
1. Pooled single-DB + RLS policies on 23 tables, GUC-driven `current_setting('app.tenant_id', true)`, FORCE RLS (0069/0141/0187) — the canonical Postgres pattern (Supabase/Citus/AWS SaaS Factory shape).
2. Tenant identity from JWT claim only; body NEVER carries tenant (0058 docstring + `tenant_context.py:131-144/403-415`) — anti caller-spoof.
3. 4-key hierarchical identity with DB unique constraint + slug CHECK regex + 6 hot-path indexes (0062).
4. NOT NULL tenant columns on core tables (0041/0049) + double FK ON DELETE CASCADE for chunks (0107c/0108).
5. Fail-loud tenant binding: `session_with_tenant`/`UoW` raise on unbound ctx (`engine.py:129-135`, `uow.py:44-47`) + UUID validation before SQL interpolation (`engine.py:33-41`).
6. Per-tenant rate limit + monthly token cap (0045; enforced `tenant_context.py:208-302`, HTTP 429) + per-tenant CORS (0059) + audit_log hash-chain tamper detection (commit `7275809`).
7. DB-metadata RBAC (new role = SQL INSERT, 0036) + numeric-level SSoT (`shared/rbac.py`) — 36 + 50 callsites across routes.
8. Worker context propagation via event payload with try/finally `clear_request_context` (contextvar leak-proof, `document_worker.py:74-87`).
9. Governance: ADR 0001 for role switch + idempotent re-assert migration vs env drift (0187) + explicit, logged superuser escape hatch (`engine.py:67-81`).

**LACKS**
1. **Actual RLS enforcement = 0** — both activation wires unplugged (hook unattached + superuser DSN). The single biggest gap vs any 2026 reference stack where RLS is on by default.
2. Workspace is a slug, not an entity: no `workspaces` table, no membership, no workspace-scoped roles, no workspace quota (pre-seed §4; 0062 design choice).
3. Workspace RLS clause is supply-side dead: `app.workspace_id` GUC never SET anywhere (break B5) → even post-enforcement, intra-tenant workspace isolation = app WHERE only.
4. No per-tenant ingest fairness: 1 global stream + shared `Semaphore(5)` (`redis_streams_bus.py:153-170`) — noisy-neighbor exposure.
5. No tenant offboarding/export pipeline (GDPR / Nghị định 13 → charter D11): `soft_delete_tenant` exists (`tenant_repository.py:360`, route `admin_tenants.py:319`) but no cascade purge of semantic_cache/Redis keys, no data-export API.
6. No per-tenant encryption / BYOK, no data-residency option (2026 enterprise-tier table stakes).
7. No CI leak-gate: leak tests exist (`tests/integration/test_rls_cross_tenant.py`, `test_rls_tenant_scope_enforced.py`) but charter's "RLS leak test 2-tenant pass trong CI" gate is not wired; unknown whether they skip silently without a `ragbot_app` DSN.
8. Admin/system path not designed for the RLS-enforced world: zero-UUID `_SYSTEM_TENANT_ID` sentinel (`tenant_context.py:39,377-378`) will fail-closed (0 rows) on every tenant table once enforcement lands.
9. No pgbouncer/transaction-pooling story for `SET LOCAL` (plan 260608 Phase 1 step 5 flags it; unresolved) and no per-tenant connection-pool partitioning.
10. Two parallel RBAC systems (numeric levels vs DB module-permissions) with no unification, and `role_definitions.scope` declared since 0036 but never enforced.

---

## (f) 10 open questions for Phase 2

1. **Activation order 1c vs 1d:** attach `attach_rls_session_hook` first (no-op under superuser per `session.py:158-163`) or switch `DATABASE_URL_APP` first (bare-session repos instantly fail-closed to 0 rows)? What is the staged rollout + rollback per ADR 0001, and who provisions the `ragbot_app` LOGIN credential (0186 made it NOLOGIN, ops-owned)?
2. **Does any script / worker bypass RLS?** Worker binds GUC (`document_worker.py:73`, `chat_worker.py:491`) but bare repo sessions (B2) + `_upsert_doc_summary` (`document_service.py:905`) + any `scripts/*.py` using admin DSN need a sweep for `_new_session()` / direct `create_engine(...)`.
3. **Should `attach_rls_session_hook` be wired in bootstrap (ga-hardening 1c)?** It touches EVERY transaction incl. admin/system/alembic (no-op when ctx unbound). What is the blast radius on admin paths that legitimately need cross-tenant reads (e.g. `list_active(record_tenant_id=None)` in registry warm-load `:78`)?
4. **Workspace RLS supply gap (B5):** 0141 policies read `app.workspace_id` but nothing SETs it. Should `session_with_tenant` also `SET LOCAL app.workspace_id`, and where does the workspace slug live in `tenant_id_ctx`-equivalent contextvar (there is none today)?
5. **Cross-tenant test coverage cardinality:** files exist (`test_rls_cross_tenant.py`, `test_3key_cross_tenant_isolation.py`, `test_cross_tenant_impersonation_guard.py`, `test_pgvector_store_tenant_scoping.py`) — but are they integration (real PG + `ragbot_app` role) or unit (mocked)? `test_rls_set_local.py` uses a fake factory. Do any actually connect as `ragbot_app` and assert 0 rows? (ga-hardening 1b leak-test is listed as a to-write file.)
6. **RBAC workspace boundary:** is the `role_definitions.scope='workspace'` (0036) ever meant to be enforced, or is "global per-tenant role" the intended GA model? If per-workspace RBAC is in scope, a `workspace_members` table + JWT claim shape change is needed.
7. **Semantic-cache orphan on bot delete (pre-seed §2):** `delete_bot` soft-delete does NOT purge `semantic_cache` → stale rows until TTL. Is a `BotLifecycleService` purge needed, or is RLS+`is_deleted` filter sufficient?
8. **Ingest fairness / noisy-neighbor (pre-seed §3):** 1 global stream + `Semaphore(5)` shared across all tenants (`redis_streams_bus.py:153-170`). No per-tenant ingest quota. Acceptable for GA or needs per-tenant fairness?
9. **Embedding-model swap guard (pre-seed §5):** `update_bot` (`bot_management_service.py:170-215`) does NOT block changing embed model when chunks exist (fixed `vector(1280)`, 0085). Detection-only counter (`query_graph.py:702-727`) never raises. Should this fail-loud, and should `corpus_version` bump on binding change?
10. **`document_chunks` / `knowledge_edges` JOIN-policy cost:** these have no direct tenant col → RLS via parent JOIN (0069/0108 denormalized `record_bot_id` to chunks). Under the `ragbot_app` role with the hook on, does the JOIN-based policy degrade HNSW vector-scan performance (planner can't push predicate into the index)? Needs EXPLAIN ANALYZE in Phase 2.
