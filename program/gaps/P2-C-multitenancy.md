# P2-C — MULTI-TENANCY & SECURITY AUDIT (Phase 2 · adversarial)

> Auditor: P2-C (was P1-C). Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key`.
> READ-ONLY src/alembic/tests; ran read-only `psql` on `ragbot_v2_dev` to verify RLS state. Only this file written.
> STANCE = EVOLVE not rewrite. Every claim carries `file:line` / `commit` / `psql-output` / `link`.
> CHARTER AN TOÀN axis = "RLS leak test 2-tenant pass trong CI · 0 cross-tenant row".
> **Headline (psql-proven 2026-06-10): RLS is enabled+FORCED on tables but 100% INERT at runtime — the app connects as `postgres` (rolsuper=t rolbypassrls=t), so the 23 policies are bypassed. Empirically confirmed: a bogus `app.tenant_id` still returns all 21 bot rows.**
>
> **PRAISE FIRST (charter mandate): the 4-key identity contract is excellent and must NOT be touched** — JWT-only tenant claim (anti-spoof), DB unique constraint enforcing it, 4-key Redis registry key, fail-loud GUC binding. This is genuinely SOTA-shaped. The single failure is the *activation wiring* of RLS-as-defence-in-depth, not the identity design.

---

## (1) Labeled component table

| Component | Label | Evidence (file:line / psql / commit) | Note (charter axis) |
|---|---|---|---|
| **4-key identity contract** | ✅ ĐÃ CHUẨN | `bot_registry_service.py:103-130` `lookup(record_tenant_id, workspace_id, bot_id, channel_type)` — all 4 required, empties rejected `:124,:127`; psql: `uq_bots_record_tenant_workspace_bot_channel UNIQUE (record_tenant_id, workspace_id, bot_id, channel_type)` | AN TOÀN: the design is correct. Two tenants/workspaces can both pick `bot_id='support'` safely. **Đừng đụng.** |
| **JWT-only tenant (anti-spoof)** | ✅ ĐÃ CHUẨN | `tenant_context.py:3-6` docstring "Tenant identity carried by JWT claim `record_tenant_id`"; `:131-137` `rt_claim = payload.get("record_tenant_id")→UUID`; `:146-162` non-owner without claim → HTTP 401 `tenant_claim_required`. Body NEVER carries tenant. | AN TOÀN: caller cannot spoof tenant via request body. Canonical 2026 pattern. |
| **DB unique constraint (4-key)** | ✅ ĐÃ CHUẨN | psql `pg_constraint`: `uq_bots_record_tenant_workspace_bot_channel UNIQUE (...)` LIVE on `bots` | AN TOÀN: uniqueness enforced at DB, not convention. |
| **Redis registry cache key** | ✅ ĐÃ CHUẨN | `bot_registry_service.py:68-74` `f"{PREFIX}:{record_tenant_id}:{workspace_id.strip()}:{bot_id.strip()}:{channel_type.strip()}"`; poisoned-tenant eviction `lookup` docstring `:113-115` | AN TOÀN: cache key carries all 4 — no cross-workspace state bleed. |
| **`session_with_tenant` fail-loud GUC bind** | ✅ ĐÃ CHUẨN | `engine.py:122-146`: raises `RuntimeError` if ctx unbound `:129-135`; UUID-validates before interpolation `_assert_uuid_str:33-41`; pgvector uses it `pgvector_store.py:124,171,284` | AN TOÀN: where it IS used, it cannot silently skip SET LOCAL. Excellent. |
| **UoW tenant enforce** | ✅ ĐÃ CHUẨN | `uow.py:40-54` `SET LOCAL app.tenant_id`, raises on unbound | AN TOÀN: worker writes can't bypass. |
| **Semantic-cache scoping** | ✅ ĐÃ CHUẨN | filter `record_bot_id AND record_tenant_id` BEFORE cosine — `semantic_cache.py:419-430` (exact) + `:479-490` (cosine) | AN TOÀN: no cross-tenant vector scan even with RLS off. App-WHERE belt holds here. |
| **23 RLS policies present in DB** | ✅ present / 🐛 inert | psql `pg_policies` count = **23**; 1 policy each on bots/documents/document_chunks/semantic_cache/conversations/messages | DB-side infra COMPLETE — see 🐛 RLS-1 for why inert. |
| **RLS ENABLE+FORCE on tables** | ✅ present / 🐛 inert | psql: `bots / documents / document_chunks / semantic_cache` all `relrowsecurity=t relforcerowsecurity=t` | FORCE is correctly set (defeats *table-owner* bypass) but NOT *superuser/BYPASSRLS* bypass — see 🐛 RLS-1. |
| **`ragbot_app` NOBYPASSRLS role exists** | ✅ present / 🐛 unused | psql: `ragbot_app rolbypassrls=f rolsuper=f`; `postgres rolbypassrls=t rolsuper=t` | The safe role EXISTS but the app does not connect as it. |
| **RLS hook 0-callsite** | 🐛 BLOCKER | `attach_rls_session_hook` def `session.py:154`; grep production callsites = **0** (only `tests/unit/test_rls_set_local.py:112,116,137`); `bootstrap.py:160-165` builds factory, never attaches | AN TOÀN: bare-session repos issue no SET LOCAL → see 🐛 RLS-2. |
| **Superuser DSN at runtime** | 🐛 BLOCKER | `.env`: `grep -c DATABASE_URL_APP = 0`; `.env:108 RAGBOT_ALLOW_SUPERUSER_RUNTIME=<set>` → `engine.py:67-81` superuser fallback w/ WARNING; psql `current_user=postgres rolsuper=t` | AN TOÀN: **#1 leak risk — see 🐛 RLS-1.** |
| **`app.workspace_id` GUC never SET** | 🐛 (subtle) | grep `SET LOCAL app.workspace_id` in src = **0**; only docstring `jsonb_conversation_state.py:8`. `session_with_tenant`/`session.py` hook both set ONLY `app.tenant_id` (`engine.py:143`, `session.py:110`). psql bots policy: `... OR (COALESCE(current_setting('app.workspace_id',true),'')='' OR workspace_id::text = current_setting(...))` | AN TOÀN: workspace clause degrades to tenant-only even if RLS were live — see 🐛 RLS-3. |
| **Bare-session repos (no GUC)** | 🐛 | `_new_session` def `_base.py:30`; bare callsites: `bot_repository.py`=6, `conversation_repository.py`=3, `document_repository.py`=7 | AN TOÀN: isolation = app-WHERE only on these paths. Even after the hook, these would fail-CLOSED (0 rows) under `ragbot_app` unless ctx bound — see 🐛 RLS-2. |
| **`_upsert_doc_summary` bare session** | 🐛 (low) | `document_service.py:894-905` `self._sf()` no GUC | PK-scoped write, low leak risk, but inconsistent. |
| **`document_chunks` JOIN-policy is tenant-only** | ↔️ (drift vs P1-C) | psql: chunk policy = `EXISTS(SELECT 1 FROM documents p WHERE p.id=record_document_id AND p.record_tenant_id=current_setting('app.tenant_id')::uuid)` — **no `record_bot_id` predicate, no workspace** | P1-C/0108 claimed denormalized `record_bot_id` → "direct" policy; live DB shows JOIN-to-parent only. Note for Phase 3 (HNSW predicate-pushdown cost). |
| **RLS pattern itself (SET LOCAL GUC)** | 🕰 (eval) | see §3 — vs 2026 defence-in-depth standard | The chosen pattern IS the 2026 standard; the gap is enforcement, not pattern choice. |
| **Workspace = slug not entity** | 🕰 (eval) | `workspace_id VARCHAR(64)` on 16 tables (0062); no `workspaces` table, no `workspace_members`, no FK, no lifecycle (P1-C §c, pre-seed §4) | see §3 — slug-only is below 2026 org→workspace→resource standard for RBAC/quota/lifecycle. |
| **STATE_SNAPSHOT "4-key enforced at DB via RLS"** | ↔️ LỆCH | `STATE_SNAPSHOT.md:273` ✅ "4-key identity enforced at DB layer via RLS tier 1 + tier 2" + `:1349` "RLS enforcement: Dead → Active" — contradicts psql-proven inert + own line `:25,:53` "RLS inert" | see §4 — doc claims enforcement that code/DB disprove. README is honest (`:221` "enforcement WIP"). |
| **Per-tenant rate limit + token cap** | ✅ ĐÃ CHUẨN | `tenant_context.py:208-302` key `rl:tenant:{uuid}:{minute}` HTTP 429 (P1-C §e #6) | RẺ/AN TOÀN: tenant fairness on the *query* path exists. |
| **Ingest fairness (per-tenant)** | 🐛 (LACK) | 1 global stream + shared `Semaphore(5)` `redis_streams_bus.py:153-170` (pre-seed §3) | NHANH: noisy-neighbour on *ingest* path. Not a leak; a fairness gap. |

**Count per label:** ✅ = 9 · 🐛 = 6 (RLS-1 superuser DSN, RLS-2 hook 0-callsite + bare repos, RLS-3 workspace GUC, doc-summary bare, ingest fairness, chunk-policy weak) · 🕰 = 2 (RLS pattern eval, workspace-as-slug) · ↔️ = 2 (STATE_SNAPSHOT enforcement claim, chunk-policy P1-C drift).

---

## (2) 🐛 Each dangerous gap + leak-test sketch

### 🐛 RLS-1 — Superuser DSN at runtime = RLS 100% bypassed (THE #1 LEAK RISK)
- **Evidence (psql 2026-06-10):**
  ```
  rolname    | rolbypassrls | rolsuper
  postgres   | t            | t          ← app connects as this
  ragbot_app | f            | f          ← the safe role, UNUSED
  current_user = postgres
  ```
  Empirical bypass proof:
  ```
  SET app.tenant_id = '00000000-0000-0000-0000-000000000000';
  SELECT count(*) FROM bots;  → 21   (should be 0 if RLS enforced)
  ```
  Root cause chain: `.env` has **no `DATABASE_URL_APP`** (`grep -c = 0`) + `.env:108 RAGBOT_ALLOW_SUPERUSER_RUNTIME` set → `create_engine_app` (`engine.py:67-81`) takes the explicit superuser fallback (logged WARNING `engine.app_dsn_superuser_fallback`). This is the **"most common RLS gotcha"** named in the 2026 literature (table-owner/superuser bypass) — except worse, because it's `rolsuper` not merely owner, so even `FORCE ROW LEVEL SECURITY` does not save it.
- **Leak-test sketch (the test that would CATCH this):** integration test, two real tenants A/B, **connecting as `ragbot_app` (NOT postgres)**:
  1. As admin, seed tenant A 1 doc + tenant B 1 doc.
  2. Open a `ragbot_app` connection, `SET LOCAL app.tenant_id = '<A-uuid>'`, `SELECT count(*) FROM documents` → assert == A-count, and `SELECT count(*) FROM documents WHERE record_tenant_id = '<B-uuid>'` → assert **0 rows**.
  3. Assert the same SELECT run as `postgres` returns BOTH (proving the test is sensitive to the role, i.e. it would have been GREEN-but-meaningless on the superuser DSN — the test must FAIL CI if `current_user` is superuser/bypassrls). Add a guard: `SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user` → `assert false`, else `pytest.fail("leak-test ran as bypass role — RLS not exercised")`.
  - This guard is the crucial bit: existing `tests/integration/test_rls_cross_tenant.py` may pass *silently* if it connects as postgres (RLS never engaged). The charter's "leak test in CI" gate must assert the *connection role*, not just row counts.

### 🐛 RLS-2 — RLS hook 0-callsite + bare-session repos
- **Evidence:** `attach_rls_session_hook` (`session.py:154`) 0 production callsite; `bootstrap.py:160-165` builds `session_factory` but never attaches. Bare `_new_session()` callsites: bot=6, conversation=3, document=7. So even after switching to `ragbot_app`, these repos would issue **no SET LOCAL** → fail-CLOSED (0 rows) on every read, breaking the app, OR (current state) rely purely on app-level `record_*` WHERE.
- **Leak-test sketch:** with hook attached + `ragbot_app` DSN, bind tenant A ctx, call `bot_repository.find_by_...` for a tenant-B bot_id → assert returns None (RLS hides it) NOT the B row. Negative-control: same call without binding ctx → assert `RuntimeError`/0 rows, never a cross-tenant row. Run the suite once WITHOUT the hook to confirm the test currently FAILS (proves it has teeth).

### 🐛 RLS-3 — `app.workspace_id` GUC never SET → workspace isolation degrades to tenant-only
- **Evidence:** grep `SET LOCAL app.workspace_id` in `src/` = 0 (only docstring `jsonb_conversation_state.py:8`). Both binders set ONLY tenant: `engine.py:143`, `session.py:110`. psql bots policy literal: `... AND (COALESCE(current_setting('app.workspace_id',true),'')='' OR workspace_id::text = current_setting('app.workspace_id',true))`. When the GUC is empty (always), `COALESCE(...)=''` is TRUE → the OR short-circuits → **workspace predicate disabled**. 0141's workspace-aware policies are supply-side dead.
- **Leak-test sketch:** two workspaces W1/W2 under ONE tenant T (same `record_tenant_id`). As `ragbot_app`, `SET LOCAL app.tenant_id='<T>'` + (proposed) `SET LOCAL app.workspace_id='W1'`; `SELECT count(*) FROM bots WHERE workspace_id='W2'` → assert **0**. Today (no workspace GUC) this returns W2 rows → test RED, proving intra-tenant workspace isolation is absent. The fix needs a `workspace_id_ctx` contextvar (none exists today) + a `SET LOCAL app.workspace_id` in `session_with_tenant` and the hook.

### 🐛 RLS-4 — `_upsert_doc_summary` bare session (low)
- `document_service.py:894-905` `self._sf()` no GUC. PK-scoped; leak-test: as A, attempt summary upsert on a B-owned doc id → assert 0 rows affected / RLS denies once enforced.

### 🐛 Ingest fairness (not a leak, a fairness gap)
- `redis_streams_bus.py:153-170` global stream + `Semaphore(5)`. Test = load 100 docs as tenant A, measure tenant B's ingest p95 latency vs baseline; assert < N× degradation under a per-tenant token-bucket once added.

---

## (3) 🕰 LỖI THỜI — 2026 standard + source

### 🕰-1 Is "SET LOCAL GUC per-session" the 2026 standard, or filter-first / app-tenant-id + RLS-as-defence-in-depth?
- **Verdict: SET LOCAL + custom GUC IS the canonical 2026 pattern — Ragbot chose correctly.** The 2026 consensus is explicitly **defence-in-depth**: app-level tenant filtering is the *additional* layer, RLS at the DB is the *security-critical* primary control ("moves isolation from convention developers must follow to a constraint the database enforces"). Crucially the literature names Ragbot's exact failure: *"If your application connects as the same role that owns the tables... your RLS policies do nothing — this is the most common RLS gotcha"* and *"SET LOCAL combined with connection pooling is the secure combination... that is the entire difference between a secure system and a data breach."* Ragbot has `FORCE ROW LEVEL SECURITY` (beats table-owner bypass) and `SET LOCAL` (beats pooler bleed) — both correct — but runs as **superuser**, which `FORCE` cannot stop. So the *pattern* is SOTA; the *deployment* (superuser DSN + unwired hook) is the gap. **This is an EVOLVE: flip the DSN + wire the hook, do NOT redesign isolation.** ([techbuddies.io 2026](https://www.techbuddies.io/2026/01/01/how-to-implement-postgresql-row-level-security-for-multi-tenant-saas/); [AWS RLS multi-tenant](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/); [ricofritzsche.me RLS mastery](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/))
- **Note on the "app-enforced tenant_id + RLS-as-belt" alternative:** Ragbot's semantic-cache + pgvector paths ALREADY do filter-first (app WHERE `record_*`), which is exactly the recommended belt. The hole is that the belt is currently the *only* layer (RLS suspenders unbuttoned). Keeping both = correct target state.

### 🕰-2 Workspace-as-slug vs entity — standard SaaS model (tenant→workspace→resource)?
- **Verdict: 2026 standard is workspace-as-ENTITY with a membership table; slug-only is below standard for RBAC + quota + lifecycle.** The reference model is `organization (tenant) → workspace/team → resource`, where workspace is a real entity and assignments go through a **membership** row (`organization_id` first in the tuple for index locality; "items assigned to the user's membership, not the user directly"). Ragbot has the *tuple* right (`record_tenant_id` leads every index) but workspace is a bare `VARCHAR(64)` slug: no `workspaces` table, no `workspace_members`, no per-workspace role, no workspace quota, no lifecycle/offboarding. Slug-in-identity is *fine* (and the 4-key design leverages it well for URL/cache context), but it cannot carry an RBAC or quota boundary. **EVOLVE path (charter "MIGRATE schema"):** add a `workspaces` entity + optional `workspace_members` when per-workspace RBAC/quota is actually required (D2) — backward-compat backfill `workspace_id ← str(record_tenant_id)` already exists (0062). Do NOT rewrite identity; add the entity beside it. ([Logto multi-tenant guide](https://logto.medium.com/build-a-multi-tenant-saas-application-a-complete-guide-from-design-to-implementation-d109d041f253); [WorkOS SaaS multi-tenant](https://workos.com/blog/developers-guide-saas-multi-tenant-architecture); [flightcontrol data modeling](https://www.flightcontrol.dev/blog/ultimate-guide-to-multi-tenant-saas-data-modeling))

---

## (4) psql verification results (2026-06-10, `ragbot_v2_dev` @ 10.0.1.160:5432)

| Check | Query | Result | Verdict |
|---|---|---|---|
| 1. App role | `SELECT rolname,rolbypassrls,rolsuper FROM pg_roles WHERE rolname IN ('postgres','ragbot_app')` | `postgres\|t\|t` · `ragbot_app\|f\|f` | App connects as **postgres = superuser + bypassrls**. `ragbot_app` (safe) exists but unused. |
| 1b. current_user | `SELECT current_user, session_user` | `postgres\|postgres` | Confirmed runtime identity = superuser. |
| 2. DATABASE_URL_APP | `grep -c DATABASE_URL_APP .env` | **0** | Not set → `create_engine_app` superuser fallback (`engine.py:67-81`), escape env `.env:108` active. |
| 3. RLS on tables | `relrowsecurity,relforcerowsecurity` for bots/documents/document_chunks/semantic_cache | all `t\|t` | RLS ENABLED + FORCED on all four — DB infra correct. |
| Policy count | `SELECT count(*) FROM pg_policies` | **23** (1 each on the 6 core tables sampled) | Policies present (0069/0141/0187). |
| **Bypass proof** | `SET app.tenant_id='000...0'; SELECT count(*) FROM bots` | **21** (all rows, bogus tenant) | **RLS INERT at runtime — definitively proven, not "cần thực nghiệm".** |
| Workspace clause | `pg_policies.qual` for bots | `... COALESCE(current_setting('app.workspace_id',true),'')='' OR workspace_id=...` | Confirms 🐛 RLS-3: empty GUC → workspace predicate disabled. |
| Chunk policy | `pg_policies.qual` for document_chunks | `EXISTS(... documents p WHERE p.record_tenant_id=current_setting('app.tenant_id')::uuid)` | JOIN-to-parent, tenant-only — no record_bot_id/workspace (drift vs P1-C 0108 "direct" claim). |
| `ragbot_app` login | `SELECT rolcanlogin FROM pg_roles WHERE rolname='ragbot_app'` | `t` | Drift note: P1-C said 0186 set NOLOGIN; live DB = LOGIN-capable. Verify before ops provisions credential. |

**RLS-inert verdict: PROVEN INERT** (not theoretical). The DB-side is complete; both activation wires (DSN switch + hook attach + workspace-GUC supply) are unplugged.

---

## (5) Answers to Q1-8 (P1-SYNTHESIS §4 "An toàn / multi-tenant")

**Q1 — `DATABASE_URL_APP` set in prod .env?** **NO** (`grep -c = 0`, this env). Runtime = superuser (psql proven). For prod, ops must set it to the `ragbot_app` DSN. Current state: RLS dead everywhere. *(Evidence: §4 row 2 + RLS-1.)*

**Q2 — Least-invasive way to wire `attach_rls_session_hook` (all sessions incl. worker + script)?** Attach once in `bootstrap.py` immediately after `create_session_factory` (`:162-165`) on the `session_factory` Singleton — the hook resolves the async→sync session class (`session.py:131-151`) so a single attach covers every repo session. It is a **no-op when ctx unbound** (`session.py:126-127`), so admin/migration/script sessions are byte-unchanged. Workers already bind ctx (`document_worker.py:73`), so they'd start enforcing automatically. **Caveat:** attach is safe to land FIRST (no-op under superuser), but it only bites once the DSN switches to `ragbot_app` — stage the DSN switch with a rollback ADR (ADR 0001 exists). **The hook as-written sets only `app.tenant_id`** — Q3/Q4 must be solved in the same change or workspace isolation stays dead.

**Q3 — `app.workspace_id` GUC: which contextvar, set where?** **None exists today** — there is no `workspace_id_ctx` (only `tenant_id_ctx` at `config/logging.py:25`). Must add a `workspace_id_ctx`, populate it in `bind_request_context()` from the resolved bot's `workspace_id`, and emit `SET LOCAL app.workspace_id` in BOTH `session_with_tenant` (`engine.py:143`) and `_set_local_tenant` (`session.py:110`). Until then the 0141 workspace clause is supply-side dead (🐛 RLS-3, psql-proven via `COALESCE(...)=''`).

**Q4 — Leak test: real integration (connect ragbot_app, assert 0 rows) or mock?** Must be REAL + must assert the connection role is NOT bypassrls (else it passes vacuously on superuser). Existing files (`test_rls_cross_tenant.py`, `test_rls_set_local.py`) — `test_rls_set_local.py` uses a fake factory (unit). It is unverified whether the integration ones connect as `ragbot_app`; given no `ragbot_app` DSN in env they likely skip or run as postgres (green-but-meaningless). **The CI gate must add a `assert rolbypassrls=false` guard** (sketch in 🐛 RLS-1). This is charter gate "AN TOÀN" and is currently UNMET.

**Q5 — HNSW filters `record_bot_id` pre/post vector scan (recall-cliff on small bot in multi-tenant table)?** Needs `EXPLAIN ANALYZE` under `ragbot_app` with the JOIN-policy active (Phase 3 — cannot measure meaningfully while superuser bypasses the policy). The chunk policy is a parent-JOIN `EXISTS` (psql §4) with no direct `record_bot_id` predicate, so under enforcement the planner may not push the tenant predicate into the HNSW index → potential over-scan. **Marked: cần thực nghiệm với `ragbot_app` role + EXPLAIN ANALYZE.**

**Q6 — Workspace → entity: schema + backward-compat?** Add `workspaces(id UUID PK, record_tenant_id FK, slug, ...)` + optional `workspace_members`; backfill is trivial because `bots.workspace_id` already holds `str(record_tenant_id)` by default (0062). Keep the slug in identity; the entity is additive. 2026-standard model = org→workspace→resource with membership (§3). Do NOT change the 4-key tuple. *(D2.)*

**Q7 — RBAC workspace-scope: `workspace_roles` map or `workspace_members` table?** Today RBAC is **global-per-tenant**: one role string per JWT (`tenant_context.py:124,382,423`), numeric levels `shared/rbac.py:19-33`; `role_definitions.scope='workspace'` (0036) is declared but **never enforced** (no per-workspace resolution in code). If per-workspace RBAC is in scope, the 2026 pattern = a `workspace_members(workspace_id, user_id, role)` table + a JWT claim shape change (per-workspace role). Decision needed: is "global per-tenant role" the intended GA model (then drop the unused `scope` column) or is per-workspace RBAC a real requirement? *(D2.)*

**Q8 — Quota cascade tenant→workspace→bot, enforce where?** Today quota is **tenant-only**: `rl:tenant:{uuid}` rate limit (`tenant_context.py:208-302`) + monthly token cap; `plan_limits` per-bot only tunes pipeline, not a counted quota. No workspace tier. If cascade is required, enforce at `guard_input` (entry node) reading a resolved `(tenant, workspace, bot)` budget chain — but this depends on Q6 (workspace entity must exist to hold a workspace budget). *(D2/D8.)*

---

## (6) ĐÃ CHUẨN — đừng đụng (charter praise mandate)

These are genuinely SOTA and must be PRESERVED through Phase 4 (đập = lỗi nặng nhất):

1. **4-key identity contract** — `(record_tenant_id, workspace_id, bot_id, channel_type)`, all 4 required at the resolve boundary, then `record_bot_id` alone internally. `bot_registry_service.py:103-130` + DB unique constraint. Best-in-class design; the slug-in-identity choice is correct.
2. **JWT-only tenant claim** — body never carries tenant; 401 if missing for non-owner (`tenant_context.py:131-162`). Anti-spoof, canonical 2026.
3. **Redis registry key** carries all 4 keys + poisoned-tenant eviction (`bot_registry_service.py:68-74,113-115`).
4. **`session_with_tenant` / UoW fail-loud** — raise on unbound ctx, UUID-validate before interpolation (`engine.py:122-146`, `uow.py:40-54`). Never silently skips SET LOCAL. This is the *right* primitive — it just needs to be the *only* session primitive (wire the hook so bare repos inherit it).
5. **RLS DB-side infra is COMPLETE and correct** — 23 policies, ENABLE+FORCE, `current_setting('app.tenant_id', true)` (the `true` = admin sessions see 0 rows not error), GUC pattern = exact 2026 standard. The work remaining is *activation wiring*, not policy design.
6. **Semantic-cache filter-first scoping** (`semantic_cache.py:419-430,479-490`) — tenant+bot WHERE before cosine; the app-WHERE belt that holds even with RLS off. Keep it as defence-in-depth alongside RLS.
7. **Per-tenant query rate limit + token cap** (`tenant_context.py:208-302`) — tenant fairness on the read path.
8. **Explicit, logged superuser escape hatch** (`engine.py:67-81` WARNING) — even the *bypass* is observable, not silent. Good governance; keep the WARNING, just stop relying on the fallback in prod.

**The one-line truth: Ragbot's tenant-isolation DESIGN is expert-grade; its tenant-isolation ENFORCEMENT is currently off because the app runs as `postgres` superuser and the per-session hook is unwired. EVOLVE = (1c) attach hook in bootstrap, (1d) set `DATABASE_URL_APP=ragbot_app`, (Q3) add `workspace_id_ctx` + `SET LOCAL app.workspace_id`, (Q4) a role-asserting leak test in CI. No rewrite.**
