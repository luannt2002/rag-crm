# P2-H — BOT-OWNER CONTROL-PLANE & SELF-SERVICE AUDIT (Phase 2 · adversarial)

> Auditor: P2-H. Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key` · anchor `7dd1f84` · alembic head `0260`.
> READ-ONLY src/alembic/tests; ran read-only `psql` on `ragbot_v2_dev` (DSN from `.env`, `DATABASE_URL` minus `+asyncpg`, SELECT-only). Only this file written.
> STANCE = **EVOLVE, không rewrite** (strangler fig). Every claim = `file:line` / `commit` / `psql-output`. **SỰ THẬT** = measured; **GIẢ THUYẾT** = labelled.
> Input read: `00-charter.md`, `00-DECISION-REGISTER.md` (D2/D9), `P2-C-multitenancy.md`, `P2-G-platform-config-cost.md`.

> **PRAISE FIRST (charter mandate):** the control-plane is a genuinely well-built thin-controller layer — metadata-driven RBAC (`module_permissions` DB table + single-flight cache), per-mutation forensic audit with hash-chain (`insert_audit_row`), cross-tenant ownership pre-verify (`require_binding_ownership`) **plus** atomic tenant-filter in the UPDATE WHERE (defence-in-depth), outbox+Redis cache-bust on every bot mutation, header-based schema-versioning (no `/v1/` URL rot). The 4-key resolve on the ingest/sync write paths is correct. **The gaps are "dây chưa nối" (orphan quota gate, unenforced workspace-scope, no sysprompt preview), not "khung sai".**

> **Headline #1 (psql + code-proven): `IngestQuotaService` is a TRUE ORPHAN** — not in `bootstrap.py`, called by ZERO production routes (`grep check_and_increment` in `documents.py`/`documents_stream_upload.py` = **0**); only callsite is the demo route `test_chat.py:2532`. The per-tenant ingest fairness gate the service was written to provide does NOT run on the real `/documents/ingest` or `/documents/stream-upload` paths. (D8/D2.)
> **Headline #2 (code-proven): `SysPromptAssembler` is LIVE in the answer path and the application APPENDS ~6 KB of platform-authored rule text (rules 15-19) to every bot's `system_prompt`** — `chat_worker.py:1436`, `chat_stream.py:295`. This is an **application-injected-text** pattern that sits in direct tension with sacred #10 ("Application KHÔNG inject text/template/rule vào answer LLM"). It is *governed* (alembic-seeded, locale-resolved, per-bot opt-out) but it is still text the platform adds, not the owner. **Adjudication required — see 🐛 SP-1.**
> **Headline #3 (psql-proven): workspace-scope RBAC + quota are DOUBLY DEAD** — `role_definitions` table is **EMPTY (0 rows)** so the declared `scope` column carries nothing; RBAC levels come from the hardcoded `ROLE_LEVELS` dict (`shared/rbac.py:17-32`). `module_permissions` has **no workspace dimension** (cols: `module, permission, min_role_level`). `quotas` HAS a `workspace_id` column but `IngestQuotaService` filters `WHERE record_tenant_id` only — workspace quota slot is schema-present, code-unused. (Confirms P2-C Q7/Q8.) (D2.)

---

## (1) Labeled component table

| # | Component | Label | Evidence (`file:line` / psql) | Note |
|---|---|---|---|---|
| 1 | **Metadata-driven RBAC** (`module_permissions` DB + Redis single-flight) | ✅ ĐÃ CHUẨN | `rbac.py:80-128` DB lookup + `AsyncSingleFlight`; deny-by-default on undefined perm `:143-146`; psql `module_permissions` 45 rows across 9 modules | No hardcoded role strings in the *dependency* path. Praise. |
| 2 | **Per-mutation forensic audit (hash-chain)** | ✅ ĐÃ CHUẨN | `bot_management_service.py:295-336` `_write_audit` RAISES on failure (never swallows); `insert_audit_row` row_hash chain (alembic 010g) | Every bot create/update/delete + AI mutation audited. CLAUDE.md "no psql-hotfix" satisfied — all content-state changes go via audited route. Praise. |
| 3 | **Cross-tenant ownership pre-verify + atomic WHERE** | ✅ ĐÃ CHUẨN | `_resource_ownership.py:22-44` (404 collapse, no enum oracle, super-admin bypass) + comment `:41-44` "mutating repo paths enforce tenant filter atomically in UPDATE WHERE (Issue #20)" | Defence-in-depth: SELECT-guard + atomic write-filter. Praise. |
| 4 | **Config flip via outbox + Redis bust** | ✅ ĐÃ CHUẨN | `bot_management_service.py:338-365` `bot.registry.changed.v1`; `system_config_service.py:62-66` (per P2-G) | No-redeploy. 12-factor. Praise. |
| 5 | **Header-based schema-versioning** | ✅ ĐÃ CHUẨN | `schema_version.py:52-96` `X-Schema-Version` → 400 on unsupported; URL stays purpose-named | Satisfies no-version-ref rule (no `/v1/` URL). Praise. |
| 6 | **4-key on ingest/sync write paths** | ✅ ĐÃ CHUẨN | `documents.py:103-109,195-201` `resolve_workspace_id`; `sync.py:259-280` exists-check scoped `(record_tenant_id, workspace_id)`; cross-tenant guard `sync.py:112-120` 403 on mismatch | External resolve boundary uses all 4. Praise. |
| 7 | **AI provider/model = platform-shared, level-100 gated** | ✅ ĐÃ CHUẨN | `admin_ai.py:10-15` docstring + `provider_create`/`model_create` seeded level 100; binding mutate adds `require_binding_ownership` `:379,:403` | Correct: provider/model have no `record_tenant_id` col → super-admin only; per-bot bindings are tenant-scoped. Praise. |
| 8 | **`SysPromptAssembler` appends platform text to bot.system_prompt** | 🐛 SP-1 | LIVE: `chat_worker.py:1436`, `chat_stream.py:295`, `test_chat.py:3088,3605`; appends `language_packs[locale].sysprompt_default_rules` (~6 KB, alembic 0146 `_VI_RULES`/`_EN_RULES`) AFTER `bot.system_prompt`; `sysprompt_assembler.py:126` `return base + platform_rules` | **Tension with sacred #10.** Governed (alembic-seeded, opt-out via `plan_limits["sysprompt_rules_disabled"]`) but still app-injected text. Adjudicate. |
| 9 | **No sysprompt preview / dry-run** | 🐛 SP-2 | grep `preview\|dry.?run` in routes (excl test_chat) → 0 sysprompt hits; only `admin_ai.py:451` `effective-config` for *model params*, NOT the assembled prompt; owner edits via blind `PATCH /bots/{uuid}` (`admin_bots.py:76`) | Owner cannot see assembled prompt (own + platform rules − opt-outs) before save. Below 2026 norm (prompt playground/staging). §3. |
| 10 | **`IngestQuotaService` orphan** | 🐛 IQ-1 | service `ingest_quota_service.py:67`; `grep check_and_increment` in `documents.py`+`documents_stream_upload.py` = **0**; not in `bootstrap.py` (grep=0); only caller `test_chat.py:2532` (demo route) | Per-tenant ingest fairness gate does NOT run on prod upload paths. D8/D2. |
| 11 | **Hardcoded numeric RBAC levels** | 🐛 RB-1 | `admin_policy.py:20` `require_min_level(request, 80)`; `admin_rate_limits.py:51` `60`; `admin_audit.py:24` `60`; `admin_metrics.py:19` `60`; `admin_gdpr.py:28` `80`; `health_models.py:460` `80`; `admin_documents_debug.py:52` `60` — while `DEFAULT_*_ADMIN_LEVEL` exist `constants/_10_rbac.py:9-11` and `admin_bots.py:31` uses them correctly | Zero-hardcode drift: magic `60`/`80` instead of the existing constants. §2. |
| 12 | **Workspace-scope RBAC declared, never enforced** | 🐛 WS-1 / ↔️ | `role_definitions.scope` col exists (alembic 0036) but psql: `role_definitions` = **0 rows**; `scope` read nowhere (grep in `rbac.py`/`shared/rbac.py` = 0); RBAC is global-per-tenant (one JWT role → `ROLE_LEVELS` dict `shared/rbac.py:17-32`) | Confirms P2-C Q7. `scope='workspace'` is supply-side dead. D2. |
| 13 | **Workspace quota tier: schema-present, code-unused** | 🐛 WS-2 | psql `quotas` HAS `workspace_id` col; `ingest_quota_service.py:97` `WHERE record_tenant_id = :tenant_id` only (no workspace predicate); psql `count(DISTINCT workspace_id)=1` per tenant | Quota cascade tenant→**workspace**→bot absent. Confirms P2-C Q8. D2. |
| 14 | **`_resource_ownership.py` path drift** | ↔️ LỆCH | Prompt + module docstring imply `middlewares/_resource_ownership.py`; actual file = `interfaces/http/_resource_ownership.py` (one level up); import `admin_ai.py:31` confirms real path | Doc-vs-layout drift only; code correct. Cosmetic. |
| 15 | **`role_definitions` empty → RBAC source-of-truth is code dict** | ↔️ LỆCH | `rbac.py` module docstring `:3-4` "Role names + numeric levels live in DB, never inlined"; but psql `role_definitions`=0 rows + `shared/rbac.py:17-32` hardcodes `ROLE_LEVELS` | The *permission→level* map IS in DB (`module_permissions`); the *role→level* map is the hardcoded dict. Docstring overstates DB-drivenness. |

**Count per label:** ✅ = 7 · 🐛 = 6 (SP-1 app-inject, SP-2 no-preview, IQ-1 quota orphan, RB-1 hardcoded levels, WS-1 ws-RBAC dead, WS-2 ws-quota unused) · ↔️ = 3 (ownership path, role_definitions empty, + WS-1 doubles as drift) · 🕰 = 2 (see §3: no-preview vs 2026 prompt-playground; ws-as-slug RBAC vs FGA hierarchy).

---

## (2) 🐛 Each hole + repro test sketch

### 🐛 SP-1 — Application appends ~6 KB platform-authored rules to every bot's system_prompt (sacred #10 tension) — **NEEDS ADJUDICATION**
- **Evidence:** `SysPromptAssembler.assemble()` returns `base + platform_rules` (`sysprompt_assembler.py:126`); LIVE callers `chat_worker.py:1436` (worker answer path) + `chat_stream.py:295` (SSE answer path). `platform_rules` = `language_packs[locale].sysprompt_default_rules` seeded by alembic 0146 (`_VI_RULES` = rules 15-19, verbatim spa-extracted, ~6 KB; `_EN_RULES` parallel). The final string fed to the LLM is **owner content + platform-authored behavioral rules** the owner never wrote.
- **Sacred #10 reads:** "Application KHÔNG inject text/template/rule vào answer LLM ... Bot owner's `system_prompt` is THE single source of truth." Appending rules 15-19 is literally injecting platform rule-text. **However** the design has 3 mitigations: (a) alembic-tracked (no psql-hotfix), (b) domain-neutral text (migration 0146 docstring argues this), (c) per-bot opt-out `plan_limits["sysprompt_rules_disabled"]`. So it is *governed injection*, not silent override.
- **GIẢ THUYẾT (label):** this was a deliberate J1 multi-tenant scaling trade-off (avoid N-alembic per bot). Whether it VIOLATES sacred #10 or is an APPROVED exception is a Phase-3 ADR call, not an auditor verdict. The honest framing: **the platform owns "how to answer" defaults; the bot owner owns "what to answer".** That is the same line `oos_answer_template` already straddles. **Map → D9** (config/governance) + flag to charter sacred-rule owner.
- **Repro/audit sketch:** unit assert `assemble(bot=SimpleNamespace(system_prompt="OWNER", plan_limits={}), language="vi")` returns a string `len > len("OWNER")` containing `"SYNTHESIS_COMPLETE"` → proves platform text reaches the LLM input. Negative-control: set `plan_limits={"sysprompt_rules_disabled":["rule_15","rule_16","rule_17","rule_18","rule_19"]}` → assert returns exactly `"OWNER"` (opt-out fully neutralises). The presence of the first assertion is the sacred-#10 evidence.

### 🐛 SP-2 — No sysprompt preview / dry-run before save
- **Evidence:** owner edits prompt via `PATCH /bots/{bot_uuid}` (`admin_bots.py:76-99`) blind — request body `system_prompt` written straight to `bots.system_prompt`. No endpoint returns the *assembled* prompt (own + platform rules − opt-outs). Only `admin_ai.py:451 /ai/models/{id}/effective-config` previews **model params**, not the prompt. grep `preview|dry_run` in routes = 0 sysprompt hits.
- **Why it matters:** with SP-1 active, the owner cannot see what the LLM actually receives (their text + 6 KB appended rules). A prompt that looks complete in the editor silently carries platform rules — surprising and unauditable from the owner's seat.
- **Repro sketch:** integration — owner PATCHes a sysprompt, then GET the bot → response echoes only `bots.system_prompt`, NOT the assembled string. Assert no route exposes `assembler.assemble(bot)` output. The fix (EVOLVE): add `GET /bots/{uuid}/system-prompt/effective` returning `assembler.assemble(bot)` (read-only, reuses the live service) — pure additive, no answer-path change.

### 🐛 IQ-1 — `IngestQuotaService` orphan: per-tenant ingest fairness gate never runs in prod
- **Evidence:** `ingest_quota_service.py:67` `check_and_increment` — production callsites in `documents.py` + `documents_stream_upload.py` = **0** (grep). Not constructed in `bootstrap.py` (grep `IngestQuotaService|ingest_quota` = 0). Only caller = demo route `test_chat.py:2532-2533`. The service docstring `:22-24` says "route handler MUST call this BEFORE INSERT INTO documents" — that contract is unmet on the real routes.
- **Impact (charter NHANH/RẺ):** the noisy-neighbour scenario the service was written to prevent (`ingest_quota_service.py:6-11`: one tenant floods upload → starves workers, bloats HNSW, burns shared embed budget) is **unmitigated on the production upload path.** This is the ingest leg of P2-C's "ingest fairness" 🐛 — same root, different surface.
- **Repro sketch:** integration — as tenant A with `quotas.documents_per_day_limit=2`, POST `/documents/stream-upload` 3× → today all 3 are accepted (no gate); expected (after wiring) the 3rd returns 429 `QuotaExceeded`. Run once against current code to confirm RED (gate absent). Wire = inject service in bootstrap + one `await svc.check_and_increment(session, record_tenant_id=...)` before the INSERT in both upload routes.

### 🐛 RB-1 — Hardcoded numeric RBAC levels (zero-hardcode drift)
- **Evidence:** 7 admin routes use magic `60`/`80`: `admin_policy.py:20`, `admin_rate_limits.py:51`, `admin_audit.py:24`, `admin_metrics.py:19`, `admin_gdpr.py:28`, `health_models.py:460`, `admin_documents_debug.py:52` — while the constants `DEFAULT_ADMIN_LEVEL=60` / `DEFAULT_TENANT_ADMIN_LEVEL=80` / `DEFAULT_SUPER_ADMIN_LEVEL=100` exist (`constants/_10_rbac.py:9-11`) and `admin_bots.py:31` + `_resource_ownership.py:27` import them correctly. So the pattern is already established; these 7 are drift.
- **Impact:** low security risk (values currently correct) but a rename/re-tier of levels would silently desync these 7 routes. Violates Quality-Gate #2 (zero-hardcode) + #5 (RBAC numeric level via constants).
- **Repro sketch:** pre-commit grep `require_min_level\(request, [0-9]+\)` in `routes/` → assert 0 hits (today 7). Fix = swap literal for the matching `DEFAULT_*_LEVEL` import. Pure surgical, no behavior change.

### 🐛 WS-1 — Workspace-scope RBAC declared, never enforced (global-per-tenant only)
- **Evidence:** psql `role_definitions` = **0 rows** (table seeded structurally by 0036 but empty); `scope` column read nowhere (`grep scope src/ragbot/.../rbac.py shared/rbac.py` = 0). RBAC resolves one JWT `role` string → numeric level via hardcoded `ROLE_LEVELS` (`shared/rbac.py:17-32`). `module_permissions` has no workspace column. So a tenant admin (level 80) has identical rights across ALL workspaces under their tenant — there is no per-workspace role.
- **Impact (D2):** the 4-key identity carries `workspace_id` correctly for data scoping, but **authorization is workspace-blind.** Matches P2-C Q7 verdict exactly.
- **Repro sketch:** two workspaces W1/W2 under tenant T; owner with role scoped (conceptually) to W1 PATCHes a bot in W2 → today succeeds (no workspace check). Expected after D2: 403. Today the test is impossible to even write because no per-workspace role exists.

### 🐛 WS-2 — Workspace quota tier: column present, code-unused
- **Evidence:** psql `quotas` has `workspace_id` column; `ingest_quota_service.py:90-101` SELECT/UPDATE filter `WHERE record_tenant_id` only. No workspace budget read anywhere. (And IQ-1 means even the tenant tier doesn't run in prod.)
- **Impact (D2/D8):** quota cascade tenant→workspace→bot is single-level (tenant) by schema and absent by wiring. The `workspace_id` slot is dead weight until D2 makes workspace an entity.
- **Repro sketch:** same as IQ-1 but assert per-workspace counters increment independently once the workspace predicate is added.

---

## (3) 🕰 LỖI THỜI — 2026 standard + verdict (≤3 web searches used)

### 🕰-1 · Self-service sysprompt: blind-PATCH vs 2026 prompt-versioning + preview/staging
**Verdict: below 2026 norm — EVOLVE (add preview + version pin), do NOT rewrite.** The 2026 consensus is that production LLM platforms "treat prompts with version-control rigor similar to application code, including dry-run capabilities through preview/staging environments and automated evaluation gates before production deployment" — git-based prompt management (branch/commit/approve), a **prompt playground for side-by-side comparison before deployment**, and linking every trace to a prompt version so a quality drop is attributable to a specific change ([Confident AI](https://www.confident-ai.com/knowledge-base/compare/best-ai-prompt-management-tools-with-llm-observability-2026); [Braintrust](https://www.braintrust.dev/articles/best-llm-monitoring-tools-2026); [buildmvpfast system-prompt best practices](https://www.buildmvpfast.com/blog/system-prompt-design-best-practices-llm-instructions-engineering-2026)). Ragbot has the *audit/version substrate* (hash-chained audit rows capture before/after on every PATCH — `bot_management_service.py:199-208`) but exposes **no preview** (🐛 SP-2) and the owner edits blind while the app silently appends 6 KB (🐛 SP-1). **EVOLVE move (Simplicity-First, no new infra):** (a) add a read-only `GET /bots/{uuid}/system-prompt/effective` reusing the live `SysPromptAssembler` so the owner sees the *actual* LLM input; (b) the audit before/after rows already give version history — surface them, don't build a new registry. Do NOT import a Braintrust/Confident-class playground for ~21 bots; that violates "no premature infra".

### 🕰-2 · RBAC: workspace-as-slug global-per-tenant vs 2026 hierarchical FGA (org→workspace→resource + membership)
**Verdict: below 2026 norm for workspace-scope — EVOLVE via D2 (add workspace entity + membership), keep the 4-key tuple.** The 2026 standard is hierarchical, tenant-aware authorization: "every authorization decision must be tenant-aware — you don't just check 'is user an admin?', you check 'is user an admin in this tenant?'", with roles scoped to resource types and **permissions flowing DOWN the hierarchy** (org → project/workspace → resource), assignments through a **membership** row rather than a global role ([WorkOS multi-tenant RBAC](https://workos.com/blog/how-to-design-multi-tenant-rbac-saas); [WorkOS FGA](https://workos.com/docs/fga); [Permit.io best practices](https://www.permit.io/blog/best-practices-for-multi-tenant-authorization)). Ragbot is tenant-aware (JWT tenant claim gates every mutation) but **workspace-blind**: one role per tenant, `role_definitions` empty, `scope='workspace'` dead (🐛 WS-1). This mirrors P2-C §3 exactly. **EVOLVE (charter "MIGRATE schema"):** when per-workspace RBAC is genuinely required, add a `workspaces` entity + `workspace_members(workspace_id, user_id, role)` and a per-workspace claim shape — the `record_tenant_id`-leading index locality is already correct. Until then, **either** implement D2 **or** drop the unused `scope` column to stop advertising a capability the code doesn't honor. Do NOT rewrite the 4-key identity (it is SOTA per P2-C).

---

## (4) Answers to the 5 mandated questions

**Q1 — Sysprompt editor + preview + audit + assembler injection?**
- **Editor:** `PATCH /bots/{bot_uuid}` (`admin_bots.py:76-99`, gated `bot:update`) writes `bots.system_prompt` (`UpdateBotCommand.system_prompt`, `bot_management_service.py:71`, max `MAX_SYSTEM_PROMPT_CHARS`). Also `POST /sync/bot-upsert` (`sync.py:244`) for bulk/legacy upsert.
- **Preview/dry-run:** **NO** (🐛 SP-2). Owner edits blind; no endpoint returns the assembled prompt. Below 2026 norm (§3 🕰-1).
- **Audit trail:** **YES** — `_write_audit` captures `before`/`after` snapshots and RAISES on failure (`bot_management_service.py:199-208,295-336`), hash-chained (010g). CLAUDE.md "no psql-hotfix" satisfied: all sysprompt changes flow through an audited route OR alembic. ✅
- **Assembler:** `SysPromptAssembler` (`sysprompt_assembler.py`) **DOES inject platform text** — appends `language_packs[locale].sysprompt_default_rules` (rules 15-19, ~6 KB) after `bot.system_prompt`, LIVE in worker + stream answer paths. **Sacred #10 tension — adjudicate (🐛 SP-1).** Mitigations: alembic-seeded, domain-neutral (claimed), per-bot opt-out.

**Q2 — RBAC: correct levels? workspace-scope real? cross-tenant?**
- **Mechanism:** metadata-driven via `module_permissions` (DB, Redis single-flight) + `require_permission_dep("module","perm")` — the *good* path (`rbac.py:131-182`), deny-by-default. ✅ But 7 routes bypass it with hardcoded `require_min_level(request, 60/80)` magic numbers (🐛 RB-1) while the constants exist.
- **Workspace-scope:** **NOT real — global-per-tenant only.** `role_definitions` empty (psql 0 rows), `scope` unread, `module_permissions` workspace-blind (🐛 WS-1). Confirms P2-C Q7.
- **Cross-tenant (owner A edit B's bot):** **BLOCKED.** `admin_bots` passes `admin_record_tenant` (JWT-derived, `None` only for level-100) → service raises `CrossTenantForbiddenError` on mismatch (`bot_management_service.py:119-123`) and repo `get_by_id(record_tenant_id=...)` scopes reads; `admin_ai` bindings add `require_binding_ownership` (404 collapse + atomic WHERE, `_resource_ownership.py:22-44`). ✅ Solid.

**Q3 — Quota cascade tenant→workspace→bot?**
- **Tenant tier:** `IngestQuotaService.check_and_increment` exists (atomic SELECT-FOR-UPDATE + daily rollover, `ingest_quota_service.py`) **but is ORPHAN** — not wired into prod upload routes (🐛 IQ-1). Query-path tenant rate-limit + token cap DOES run (`tenant_context.py`, per P2-C). 
- **Workspace tier:** **ABSENT** — `quotas.workspace_id` column present, code filters tenant-only (🐛 WS-2).
- **Bot tier:** `plan_limits` per-bot tunes *pipeline* knobs (5-tier resolve, per P2-G) but is not a *counted* ingest quota.
- **Verdict:** cascade is single-level (tenant) by design and **zero-level by wiring on ingest** (orphan). Confirms P2-C Q8. (D2/D8.)

**Q4 — Self-service completeness (what can an owner do end-to-end via API)?**
- ✅ Create bot (`POST /bots`), set sysprompt (`PATCH /bots/{uuid}`), set model/embedding binding (`POST /bots/{uuid}/bindings`), upload docs (`POST /documents/ingest`, `/documents/stream-upload`), rechunk/delete docs, set per-bot policy (`admin_policy`), inspect rate-limits, list audit-log.
- **GAPs (still admin/psql-tand or absent):**
  1. **Provider/model creation = super-admin only** (level 100) — a tenant owner cannot register their OWN API key/provider/model; they can only *bind* to platform-shared ones (`admin_ai.py:10-15`). Acceptable for a curated platform, but it IS a self-service ceiling for BYO-key tenants. **GIẢ THUYẾT:** intended (provider table has no `record_tenant_id`); revisit if BYO-model is a product goal.
  2. **Platform sysprompt rules** (rules 15-19) editable only via **alembic** (`language_packs` UPDATE) — not owner-self-service; owner's only lever is the opt-out list. By design (platform default), but means "tune the default rule" = engineering ticket, not UI.
  3. **No assembled-prompt preview** (🐛 SP-2) — owner cannot self-inspect the real LLM input.
  4. **Ingest quota un-enforced** (🐛 IQ-1) — not an owner-facing gap but a platform-fairness gap.

**Q5 — 4-key + tenant isolation on control-plane?**
- **Write paths (ingest/sync/bot-create):** resolve all 4 keys at the boundary — `resolve_workspace_id` + JWT tenant + `(bot_id, channel_type)` (`documents.py:103`, `sync.py:259-280`, `CreateBotCommand`). ✅
- **Mutation-by-UUID paths (`PATCH/DELETE /bots/{uuid}`, bindings):** resolve by **internal UUID PK** (`bot_uuid`, `binding_id`) scoped by `record_tenant_id` — correct per the identity rule ("once `record_bot_id` resolved, internal queries use it alone"). Cross-tenant blocked by tenant-filter (Q2). ✅
- **No bot_id-only leak found** on the control-plane. The one residual isolation gap is **RLS-inert at the DB** (P2-C 🐛 RLS-1, superuser DSN) — but that is P2-C's scope; on the control-plane, app-level `record_tenant_id` WHERE + ownership pre-verify are the active belt and they hold.

---

## (5) ĐÃ CHUẨN — đừng đụng (charter praise mandate)

1. **Thin-controller + service split** — routes delegate to `BotManagementService`/`AIConfigService`; no business logic in handlers. `admin_bots.py` / `admin_ai.py`. Clean hexagonal boundary.
2. **Forensic audit, fail-loud** — `_write_audit` RAISES on write failure (never swallows), before/after snapshots, hash-chain (`bot_management_service.py:295-336`). This is the no-psql-hotfix guarantee in code.
3. **Cross-tenant ownership: SELECT-guard + atomic UPDATE WHERE** — `_resource_ownership.py:22-44`, 404-collapse (no enum oracle), super-admin bypass. Belt + suspenders, TOCTOU-safe.
4. **Metadata-driven RBAC with single-flight cache** — `rbac.py`, deny-by-default on undefined permission, Redis-cached `module_permissions`. The *dependency* path has no hardcoded roles.
5. **Header-based schema-versioning** — `schema_version.py`, URL stays canonical, no `/v1/` rot. Satisfies no-version-ref.
6. **Outbox + Redis registry-bust on every bot mutation** — peer-replica cache coherence (`bot_management_service.py:338-365`). No-redeploy config flip.
7. **Provider/model tiering** — platform-shared resources correctly gated at level 100; per-bot bindings tenant-scoped. The trust boundary is drawn in the right place.
8. **`IngestQuotaService` internal design** (when wired) — atomic SELECT-FOR-UPDATE, daily rollover, fail-loud on missing row, `0=unlimited` premium override. The *logic* is correct; only the *wiring* is missing (🐛 IQ-1).

---

## (6) Label tally + D-mapping

**Tally:** ✅ ×7 · 🐛 ×6 · ↔️ ×3 · 🕰 ×2.

| 🐛 | Maps to | Why |
|---|---|---|
| SP-1 (app appends platform rules to sysprompt) | **D9** (config/governance) + sacred-#10 owner | Governed injection — needs ADR: approved-exception vs violation. |
| SP-2 (no sysprompt preview) | **D12** (production feedback/self-service) | Add read-only effective-prompt endpoint (reuse live assembler). |
| IQ-1 (quota orphan) | **D8** (ingest fairness) + **D2** | Wire `check_and_increment` into prod upload routes + bootstrap. |
| RB-1 (hardcoded RBAC levels) | quality-gate hygiene (no D) | Swap 7 magic `60`/`80` for existing `DEFAULT_*_LEVEL` constants. |
| WS-1 (workspace-RBAC dead) | **D2** (workspace→entity + RBAC ws-scope) | Add `workspace_members` OR drop unused `scope` column. |
| WS-2 (workspace-quota unused) | **D2** (quota cascade tenant→ws→bot) | Depends on workspace becoming an entity to hold a budget. |

**One-line truth:** Ragbot's control-plane is a well-architected thin-controller layer with excellent audit + cross-tenant isolation; the self-service story is **NOT yet expert** because of three unwired/ungoverned seams — an orphan ingest quota gate (IQ-1), no sysprompt preview while the app silently appends 6 KB of platform rules (SP-2 + SP-1, the latter a sacred-#10 adjudication), and workspace-scope RBAC+quota that are schema-declared but code-dead (WS-1/WS-2, all → D2). **EVOLVE, not rewrite:** wire the quota gate, add a read-only effective-prompt endpoint, swap 7 hardcoded RBAC levels for the constants already in the repo, and let D2 decide the workspace-entity question. No framework is wrong; the dây chưa nối hết.
