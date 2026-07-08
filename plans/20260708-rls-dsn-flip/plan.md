# [T2-Security] RLS DSN flip — activate row-level tenant isolation (staged)

**Ngày**: 2026-07-08 · Nhánh: `fix-260623-ingest-expert` · Gap: security #1 (deep-analysis 2026-07-08)
**Chuẩn**: rule#0 evidence-only, one-change-measure, staged with go/no-go gates, rollback-ready. **KHÔNG flip production ngoài maintenance window + owner-go.**

> Đây là PLAN — chưa execute. Flip = 1 ENV change (+restart), reversible. Nhưng trên service chat LIVE, sai = tenant mất access = service sập → phải staged.

---

## 0. Vấn đề (verified live 2026-07-08, rule#0)

RLS được build expert nhưng **INERT ở runtime**: app connect DB bằng **`postgres` superuser** → bypass toàn bộ RLS kể cả FORCE. Tenant isolation hiện dựa **100% app-filter** (`record_bot_id`/`record_tenant_id` trong query) — 1 query quên filter = leak, không có DB-level net.

**Evidence (self-verified):**
- `current_user` (app DSN) = `postgres`, `usesuper=True` → superuser, RLS bypassed.
- 24 tables `FORCE ROW LEVEL SECURITY` + 24 policies (pg_class/pg_policies count).
- `ragbot_app` role: `rolsuper=false, rolbypassrls=false` (NOBYPASSRLS) — **provisioned đúng**.
- `create_engine_app` (`infrastructure/db/engine.py:60-79`): dùng `settings.database.url_app` (DATABASE_URL_APP); unset → superuser fallback qua escape `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`.

---

## 1. Preconditions — ĐÃ verify ✅ (tại sao flip khả thi)

| # | Precondition | Trạng thái (evidence) |
|---|---|---|
| P1 | `ragbot_app` role tồn tại, NOBYPASSRLS | ✅ `pg_roles`: super=F bypassrls=F |
| P2 | `ragbot_app` có GRANTs đủ (SELECT/INSERT/UPDATE/DELETE + sequences + ALTER DEFAULT) | ✅ `alembic/20260619_rls_app_role_grants.py` — least-privilege, idempotent, reversible |
| P3 | `ragbot_system` role (BYPASSRLS) cho workers | ✅ `20260619_rls_system_role_grants.py` |
| P4 | Workers dùng **system factory** (BYPASSRLS), không app factory | ✅ `interfaces/http/embedded_workers.py:154,187` `container.system_session_factory()` |
| P5 | App request-path dùng RLS session factory set `app.tenant_id` GUC | ✅ `bootstrap.py:185-186` `create_rls_session_factory`; hook `after_begin` đọc `tenant_id_ctx` → `SET LOCAL app.tenant_id` (`infrastructure/db/session.py:24-25,102`) |
| P6 | Tenant contextvar set per-request bởi `TenantContextMiddleware` (`bind_request_context`) | ✅ dùng ở ingest_stages_*, uow, router; middleware set từ JWT |
| P7 | Global config tables (system_config/language_packs/ai_models/ai_providers) **KHÔNG** bị RLS | ✅ không có trong 24 FORCE-RLS list → app đọc cross-tenant OK dù ragbot_app |
| P8 | 24 FORCE-RLS tables đều tenant-scoped (có record_tenant_id) | ✅ list verified — bots/documents/document_chunks/conversations/messages/… |

---

## 2. Rủi ro CÒN LẠI — phải verify trong canary (rule#0, chưa chứng minh)

Cơ chế fail-closed: một session app-factory MÀ contextvar `tenant_id_ctx` **UNSET** → hook không `SET LOCAL` → dưới ragbot_app, policy `current_setting('app.tenant_id',true)::uuid` = NULL → **0 rows** (`session.py:21-24,32-34` tự ghi rõ điều này).

| R | Rủi ro | Cần verify (canary) |
|---|---|---|
| R1 | HTTP path chạm tenant-table TRƯỚC khi middleware set ctx (ordering) | Middleware phải chạy trước mọi DB access; test 1 chat + 1 ingest end-to-end |
| R2 | App-factory dùng ngoài request (startup preload, cache-warm, health) chạm tenant-table | grep app-factory usage ngoài request scope; nếu có → phải dùng system-factory hoặc bind ctx |
| R3 | `BotRegistryService.lookup` (đọc `bots` RLS'd) — có trong request-ctx không? | trace: lookup xảy ra sau middleware (resolve bot cho chat); verify không có warm-up lookup thiếu ctx |
| R4 | Platform-admin cross-tenant reads qua app-factory (nếu có) | admin routes: phải dùng system-factory cho cross-tenant, hoặc RLS chặn đúng ý |
| R5 | Semantic-cache / token_ledger writes trong worker-ctx | P4 nói workers = system-factory; verify aux_usage/token_ledger cũng vậy |

---

## 3. Staged rollout (canary TRƯỚC prod; go/no-go mỗi gate)

### STAGE 0 — Preflight (read-only, no change)
- [ ] Re-run P1–P8 evidence checks (script §6.1).
- [ ] grep audit R2: `grep -rn "session_factory" src/ragbot/interfaces/http/app.py` + startup lifespan — mọi DB access startup phải là system-factory hoặc lazy-in-request.
- [ ] Confirm `DATABASE_URL_APP` + `DATABASE_URL_SYSTEM` DSN strings sẵn sàng (ragbot_app / ragbot_system creds — **env only, KHÔNG commit vào file tracked**, sacred secret rule).
- **GATE 0**: tất cả P verified + R2 audit sạch → tiếp; else fix code trước.

### STAGE 1 — Canary trên DB KHÔNG-prod (clone hoặc dev DB)
- [ ] Clone schema+seed sang dev DB (hoặc dùng dev DB riêng).
- [ ] Apply role migrations `20260619_rls_app_role_grants` + `system_role_grants` nếu chưa.
- [ ] Set `DATABASE_URL_APP=<ragbot_app dsn>`, `DATABASE_URL_SYSTEM=<ragbot_system dsn>`, **unset** `RAGBOT_ALLOW_SUPERUSER_RUNTIME` (hoặc ≠ escape value).
- [ ] Restart app trên dev → health 200.
- [ ] **Smoke**: 1 chat (factoid) + 1 ingest (upload→active) end-to-end → phải trả đúng (R1/R3). Nếu 0-rows/empty → contextvar gap → **NO-GO**, fix.
- [ ] **Isolation probe** (§6.2): tenant A JWT query bot tenant B → 0 rows / 403. tenant A thấy data tenant A đầy đủ.
- [ ] Full unit+integration test suite pass.
- **GATE 1**: smoke + isolation + suite pass trên dev → tiếp; else fix + lặp.

### STAGE 2 — Load-gate trên canary
- [ ] Chạy load-test/eval nhỏ (20-30q, 2 bot) dưới ragbot_app → so coverage/HALLU với baseline superuser. Kỳ vọng **không đổi** (RLS chỉ chặn cross-tenant, không đổi in-tenant answer).
- [ ] Monitor structlog cho `0-rows` / RLS-deny events bất thường.
- **GATE 2**: parity coverage + 0 unexpected deny → sẵn sàng prod window.

### STAGE 3 — Production maintenance window (owner-go)
- [ ] **Owner approve** + thông báo window (service chat LIVE).
- [ ] Snapshot/backup + ghi rollback command sẵn.
- [ ] Set prod env `DATABASE_URL_APP` + `DATABASE_URL_SYSTEM`, unset superuser escape.
- [ ] Restart → health 200 → smoke (chat+ingest) → isolation probe.
- [ ] Monitor 15-30 phút: latency, 0-row rate, error rate.
- **GATE 3**: smoke+isolation pass + metrics bình thường → giữ; else **ROLLBACK ngay**.

### STAGE 4 — Post
- [ ] Update STATE_SNAPSHOT: RLS ENFORCED. Update DEEP_ANALYSIS (gap #1 closed).
- [ ] Giữ superuser escape env sẵn (revert path) 1 tuần trước khi remove.

---

## 4. Rollback (mọi stage — reversible)
```
# revert = env only + restart (KHÔNG DDL, KHÔNG data change)
unset DATABASE_URL_APP           # hoặc trỏ lại superuser dsn
export RAGBOT_ALLOW_SUPERUSER_RUNTIME=1   # re-enable superuser fallback
systemctl restart ragbot-py
# create_engine_app → superuser fallback → RLS inert như cũ (byte-for-byte behaviour)
```
Role/policy DDL để nguyên (inert khi superuser). Rollback = 0 data risk.

## 5. Cái KHÔNG làm (scope guard)
- KHÔNG remove superuser escape code cho tới khi prod ổn ≥1 tuần.
- KHÔNG đổi policy/role DDL trong plan này (đã provision, chỉ activate).
- KHÔNG flip prod ngoài window/owner-go.
- KHÔNG commit DSN creds vào file tracked (env only — sacred secret).

## 6. Scripts

### 6.1 Preflight evidence
```sql
SELECT current_user, usesuper FROM pg_user WHERE usename=current_user;             -- expect ragbot_app / false post-flip
SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname LIKE 'ragbot%';   -- app=F/F, system=?/T
SELECT count(*) FROM pg_class WHERE relforcerowsecurity AND relkind='r';           -- 24
SELECT count(*) FROM pg_policies;                                                  -- 24
```

### 6.2 Isolation probe (the proof)
```sql
-- as ragbot_app, no tenant set → must be 0 (fail-closed)
SET app.tenant_id = '00000000-0000-0000-0000-000000000000';
SELECT count(*) FROM bots;          -- expect 0 (bogus tenant)
-- real tenant A → only A's rows
SET app.tenant_id = '<tenant_A_uuid>';
SELECT count(*) FROM bots;          -- expect only tenant A's bots
SELECT count(*) FROM documents WHERE record_tenant_id <> '<tenant_A_uuid>';  -- expect 0 (RLS hides others)
```
Under superuser today: all 3 return ALL rows (RLS inert) — that IS the gap this flip closes.

---

## 7. STAGE 0 + policy-probe RESULTS (executed 2026-07-08, read-only, rule#0)

**Đã chạy phần AN TOÀN (không đụng production DSN):**

### Policy isolation probe — AS ragbot_app (SET ROLE, session riêng, không đổi app)
- superuser hiện tại: `bots` = **6** (RLS inert, thấy hết).
- AS ragbot_app + bogus tenant → **0 bots** ✅ (fail-closed đúng).
- AS ragbot_app + tenant A → **6 bots, cross-tenant leak = 0** ✅; `documents` leak = **0** ✅.
- → **POLICY HOẠT ĐỘNG ĐÚNG** (fail-closed + tenant-scoped). (DB chỉ 1 tenant → A-vs-B mẫu nhỏ, nhưng fail-closed chứng minh cơ chế.)

### R1–R5 code-truth audit — app RESILIENT với flip
| R | Kết quả (verified file:line) |
|---|---|
| R1 startup crash? | ✅ KHÔNG — `app.py:363-368` 4 bootstrap task đều try/except **graceful-degrade** (log skip, service stays up) |
| R2 bot-registry warm | ⚠️ `bootstrap_cache` (`bot_registry_service.py:76-101`) gọi `list_active(record_tenant_id=None)` = cross-tenant, dùng **app-factory** → dưới ragbot_app = **0 rows** → cache cold. **NHƯNG graceful** (`:318-319` skip) |
| R3 lookup DB-miss | ✅ `lookup` (`:103`) cache-miss → `_fetch_and_cache(record_tenant_id,...)` = **per-tenant, context-bound** → works dưới ragbot_app (GUC set trong request) |
| R4 token/CORS warm | ✅ `api_tokens` + `system_config` **KHÔNG RLS'd** → warm OK dưới ragbot_app |
| R5 workers | ✅ system-factory (BYPASSRLS) |

**Kết luận STAGE 0**: flip **an toàn hơn lo ngại** — app START + FUNCTION dưới ragbot_app (graceful degradation khắp nơi), policy proven. Cost = bot-registry cache cold (self-healing per-request). **KHÔNG có hard-break path.**

### Pre-fix — ✅ DONE (2026-07-08, committed + verified)
- **`bootstrap_cache` → system-factory**: DONE. `BotRegistryService` giờ nhận `system_repo` (optional, BYPASSRLS `system_session_factory`) cho cross-tenant warm; per-tenant `lookup` giữ app-repo. Backward-compat (unwired → falls back to app-repo = no-op dưới superuser). **Verified**: 11/11 unit test (2 mới: warm dùng system_repo, lookup dùng app repo), compile OK, boot health 200, chat end-to-end đúng ("LANDSPIDER 205/65R16 = 1.170.000đ"). Files: `bot_registry_service.py`, `bootstrap.py`, `test_bot_registry_service.py`.
- → **Không còn cache-cold degradation** dưới ragbot_app. Flip giờ = ENV-only, không code-fix chờ.

### Còn lại (cần owner-go, KHÔNG tự làm)
- STAGE 1 app-smoke dưới ragbot_app = **flip DSN trên DB này = production** (DB `ragbot_v2_dev` là DB service LIVE dùng) → **owner-go + window**.
- Không có DB clone riêng → canary đầy đủ cần clone hoặc chính là prod window.

## 8. Chốt
Preconditions P1–P8 ✅ · **policy proven ✅** (isolation probe) · **code RESILIENT ✅** (graceful degrade, no hard-break) · 1 pre-fix self-healing (bootstrap_cache system-factory). Flip = ENV change reversible. **Bước tiếp = owner-go + maintenance window** (smoke app dưới ragbot_app trên prod/clone). STAGE 0 = XONG, an toàn.
