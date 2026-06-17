# ADR-W2-D2 — Workspace slug → entity thật + RBAC workspace-scope + quota cascade

> Phase 3 ADR · Wave W2 · Tier **[T2-CostPerf/Safety · workspace boundary]** · Date 2026-06-10
> Nguồn gap: P2-C §3 🕰-2 + Q6/Q7/Q8 · P2-H 🐛 IQ-1/WS-1/WS-2 (psql + code-proven)
> STANCE = **EVOLVE, không rewrite**: GIỮ 4-key identity + slug-in-identity (P2-C §6.1 "đừng đụng"); chỉ THÊM entity BÊN CẠNH. Backfill `workspace_id ← str(record_tenant_id)` đã có (alembic 0062).
> Cross-ref: **D3** (ADR-W1-D3 — workspace GUC `app.workspace_id` vừa wire) · **D8** (ingest fairness) · **S10** (sysprompt — không đụng).

---

## 1. Context — SỰ THẬT (psql + code, 2026-06-10)

Slug-only ĐỦ cho **identity/data-scoping** nhưng KHÔNG mang được **RBAC/quota boundary**. Bằng chứng:

- **SỰ THẬT** — `bots.workspace_id VARCHAR(64) NOT NULL` trên `bots` + 16 data tables (alembic `20260504_0062_workspace_4key_identity.py:111`); backfill `bots.workspace_id = record_tenant_id::text` (`:116-120`), FK-chain tables inherit từ `bots.workspace_id` via JOIN (`:124`). **KHÔNG có** bảng `workspaces`, **KHÔNG** FK, **KHÔNG** lifecycle (create/soft-delete/offboard). (P2-C §1 row "Workspace = slug not entity".)
- **SỰ THẬT (WS-1)** — RBAC là **global-per-tenant**: một JWT `role` string → numeric level qua hardcoded `ROLE_LEVELS` dict (`shared/rbac.py:17-32`). `module_permissions` **không có cột workspace** (cols `module, permission, min_role_level`). `role_definitions.scope` cột tồn tại (`20260422_0036_rbac_tables.py:24` `scope VARCHAR(32) DEFAULT 'workspace'`) nhưng **đọc nơi nào = 0** (P2-H WS-1 grep). ↔️ **DRIFT cần verify ở Phase 4**: 0036 CÓ seed rows (`:48 INSERT INTO role_definitions ...`) nhưng P2-H psql báo `role_definitions = 0 rows` — nghĩa là seed bị wipe/không apply trên `ragbot_v2_dev`. Một tenant admin (level 80) có **quyền y hệt trên MỌI workspace** dưới tenant → authorization **workspace-blind**.
- **SỰ THẬT (WS-2)** — `quotas` CÓ cột `workspace_id` (origin `20260504_0062` + `20260608_0187`) nhưng `IngestQuotaService.check_and_increment` filter `WHERE record_tenant_id = :tenant_id` **only** (`ingest_quota_service.py` SELECT…FOR UPDATE, không có workspace predicate). Quota cascade tenant→**workspace**→bot vắng mặt theo schema.
- **SỰ THẬT (IQ-1 orphan)** — `IngestQuotaService` KHÔNG trong `bootstrap.py`; `grep check_and_increment` trong `documents.py` + `documents_stream_upload.py` = **0**; callsite duy nhất = demo route `test_chat.py:2532`. Cả hai route upload thật resolve 4-key đúng (`documents.py:103 resolve_workspace_id`, `documents_stream_upload.py:235`) nhưng **không gọi quota gate** trước INSERT.
- **SỰ THẬT (D3 vừa wire)** — ADR-W1-D3 đã chốt thêm `workspace_id_ctx` + `SET LOCAL app.workspace_id` ở `session_with_tenant` (`engine.py:143`) + hook (`session.py:110`); policy workspace-aware (`20260529_0141_workspace_aware_rls.py`) chạy trên `bots.workspace_id` VARCHAR. → entity mới PHẢI giữ `bots.workspace_id` slug nguyên (D3 leak-test dựa vào nó).

**GIẢ THUYẾT (chưa verify, để Phase 4 đo):** per-workspace RBAC là yêu cầu sản phẩm thật hay chỉ là cột thừa từ 0036 — quyết định (b) bên dưới đưa 2 nhánh.

---

## 2. Decision

### (a) Schema — `workspaces` entity THÊM bên cạnh, KHÔNG đổi 4-key tuple

```sql
CREATE TABLE workspaces (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_tenant_id UUID NOT NULL REFERENCES tenants(id),  -- RLS scope, index lead
    slug             VARCHAR(64) NOT NULL,                   -- == bots.workspace_id value
    name             VARCHAR(255) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ NULL,                       -- soft-delete lifecycle
    CONSTRAINT uq_workspaces_tenant_slug UNIQUE (record_tenant_id, slug)
);
CREATE INDEX ix_workspaces_tenant ON workspaces (record_tenant_id) WHERE deleted_at IS NULL;
```

- **Backfill (no downtime)**: `INSERT INTO workspaces (record_tenant_id, slug, name) SELECT DISTINCT record_tenant_id, workspace_id, workspace_id FROM bots ON CONFLICT DO NOTHING;` — `bots.workspace_id` mặc định = `str(record_tenant_id)` (0062) → mỗi tenant tối thiểu 1 ws "default", trùng `str(record_tenant_id)`. Đúng tinh thần `null→default ws`.
- **GIỮ `bots.workspace_id VARCHAR(64)`** là cột identity (4-key tuple KHÔNG đổi). `workspaces` là **tham chiếu THÊM** (lookup slug→entity cho RBAC/quota/lifecycle), KHÔNG phải FK NOT-NULL trên `bots` ở Phase 4 đầu (tránh chặn write path). Liên kết qua `(record_tenant_id, workspace_id slug)` — KHÔNG thêm `record_workspace_id` UUID vào 4-key. Lý do: P2-C §6.1 "slug-in-identity là đúng, leverage tốt cho URL/cache"; thêm UUID vào tuple = vi phạm "đừng đụng".

### (b) RBAC workspace-scope — **CHỐT: giữ global-per-tenant cho W2, defer `workspace_members` sang Wave 6** (recommend)

Trade-off `workspace_members(workspace_id, user_id, role_level)` vs giữ global-per-tenant:

| Tiêu chí | `workspace_members` (per-ws role) | Giữ global-per-tenant |
|---|---|---|
| Đúng chuẩn 2026 (WorkOS/Permit hierarchical FGA, P2-H §3 🕰-2) | ✅ membership row, role flow down org→ws→resource | ↔️ tenant-aware nhưng ws-blind |
| Thay đổi JWT claim shape | ❌ cần per-workspace role trong claim → đụng `tenant_context.py` auth boundary | ✅ claim không đổi |
| Số tenant/ws thực tế hiện tại | psql `count(DISTINCT workspace_id)=1` per tenant (P2-H WS-2) → **chưa ai dùng multi-ws** | phù hợp thực trạng |
| Sacred-#0 (no premature infra) | ❌ build authz hierarchy cho 0 user multi-ws = premature | ✅ Simplicity-First |
| Effort | ~M (bảng + resolver + claim + middleware) | ~XS (drop cột thừa) |

**RECOMMEND**: W2 **giữ global-per-tenant** (tenant admin quyền đều trên mọi ws của mình). Hệ quả bắt buộc:

1. **DROP `role_definitions.scope`** (cột unused, advertise capability code không honor — P2-H WS-1) — alembic `ALTER TABLE role_definitions DROP COLUMN scope`. Đồng thời reconcile drift 0036-seed-vs-empty: nếu giữ bảng, re-seed; nếu bảng thật sự dead (ROLE_LEVELS dict là SSoT) → ghi rõ trong ADR là known-limitation, KHÔNG xoá bảng (audit FK).
2. **Data-scope vẫn workspace-aware** qua RLS (D3 `app.workspace_id`) — intra-tenant 2-ws KHÔNG đọc chéo *dữ liệu*; nhưng *authorization* (ai được PATCH bot ws nào) vẫn tenant-level. Phân biệt rõ: **D2 đóng data-boundary workspace, KHÔNG đóng authz-boundary workspace** (defer).
3. **Wave 6 trigger**: khi có tenant thật cần "user X chỉ quản ws W1", mở lại nhánh `workspace_members` qua ADR riêng (D2-bis). Khi đó `workspaces` entity (a) đã sẵn làm FK target.

### (c) Quota cascade tenant→workspace→bot — wire + enforce ở `guard_input`/upload boundary

1. **Wire IngestQuotaService (đóng IQ-1 orphan)**: construct trong `bootstrap.py`; gọi `await svc.check_and_increment(session, record_tenant_id=..., workspace_id=..., increment_by=len(docs))` **TRƯỚC INSERT** trong `documents.py` (ingest, single + batch) **và** `documents_stream_upload.py`. Cùng session (giữ atomic SELECT FOR UPDATE trong transaction route — `ingest_quota_service` docstring đã yêu cầu).
2. **Cascade chain (đọc budget theo tầng)**: resolver trả `(tenant_limit, ws_limit, bot_limit)`; enforce = `min` headroom còn lại của chain (tenant cạn → 429 dù ws còn). Quota row `quotas(record_tenant_id, workspace_id)` — `workspace_id = WORKSPACE_SYSTEM_SLUG` cho dòng tenant-tier; per-ws row cho ws-tier; bot-tier dùng `plan_limits` per-bot (đã có, P2-G). `0 = unlimited` giữ nguyên semantics.
3. **Vị trí enforce**: ingest path = trong 2 route upload (trên). **Query path** (guard_input entry node) đọc cùng budget chain cho rate/token cap — nhưng query-path tenant rate-limit ĐÃ chạy (`tenant_context.py:208-302`); D2 chỉ THÊM nhánh đọc `workspace_id` vào key `rl:tenant:{uuid}` → `rl:ws:{tenant}:{ws}` khi ws-limit set, fallback tenant-only khi không set (backward-compat).
4. **Hằng số**: mọi default limit/window từ `system_config` DB / `shared/constants` — KHÔNG inline (zero-hardcode). `WORKSPACE_SYSTEM_SLUG = "system"` đã có (CLAUDE.md identity rule).

### (d) RLS — `workspaces` entity scope `record_tenant_id` (khớp D3)

- `workspaces` ENABLE + FORCE ROW LEVEL SECURITY; policy `USING (record_tenant_id = current_setting('app.tenant_id', true)::uuid)` — **không** workspace predicate trên chính bảng workspaces (entity được scope bằng tenant; ws-id là payload của nó). Khớp pattern 0141/0187.
- `quotas` (đã có workspace_id) thêm/giữ policy tenant-scoped; per-ws row đọc qua app-WHERE `workspace_id` + RLS tenant belt.
- Leak-test D3 (intra-tenant 2-ws) phủ luôn: `workspaces` của ws W2 KHÔNG hiện khi `app.workspace_id=W1`? → KHÔNG, vì workspaces scope tenant-only (đúng — owner cần list mọi ws của mình). Data tables (bots/documents) mới là chỗ ws-isolation áp dụng (D3 đã cover).

---

## 3. Migration plan backward-compat (no downtime · thứ tự alembic)

> Head hiện tại = `0197` (`20260610_0197_null_out_api_keys_value_plain.py`). D2 nối tiếp sau D3/D8 của W1/W2.

1. **`00NN_workspaces_entity`** — `CREATE TABLE workspaces` (a) + backfill `INSERT … SELECT DISTINCT FROM bots ON CONFLICT DO NOTHING` + RLS ENABLE/FORCE/policy (d). Additive, không khoá write path (không thêm FK NOT-NULL trên bots). **null→default ws**: tenant không có bot vẫn được seed 1 ws khi onboarding (separate INSERT), legacy rows đã có slug = `str(record_tenant_id)`.
2. **`00NN+1_drop_role_definitions_scope`** — `ALTER TABLE role_definitions DROP COLUMN scope` (b·1). Reversible (down = ADD COLUMN DEFAULT 'workspace'). Nếu Phase 4 verify bảng còn dùng → đổi thành re-seed thay vì drop.
3. **`00NN+2_quotas_workspace_tier`** (nếu cần row ws-tier riêng) — KHÔNG đổi schema `quotas` (cột workspace_id đã có 0062); chỉ seed/migrate nếu introduce ws budget. Phần lớn là code-wiring (c), không alembic.
4. **Code-only (không alembic)**: wire IngestQuotaService vào bootstrap + 2 route (c·1); resolver cascade (c·2); WorkspaceRepository lookup slug→entity.

**Backward-compat invariant**: trước khi ws-quota được set per tenant, cascade `min()` degrade về tenant-tier (ws_limit = 0/unlimited) → behavior y hệt hôm nay + gate ingest mới bật. Rollback từng migration độc lập (1/2/3 reversible).

---

## 4. Alternatives rejected

| Alt | Lý do bác |
|---|---|
| **Thêm `record_workspace_id` UUID vào 4-key tuple** | **CẤM** — P2-C §6.1 "4-key identity đừng đụng"; slug-in-identity đúng, đổi tuple = vỡ unique constraint + Redis key + leak-test D3. Entity là tham chiếu THÊM, không thay identity. |
| **Per-customer schema (1 schema/tenant)** | Vỡ pooling + connection-per-tenant; research D3 §1 (Bytebase/Crunchy) bác; mâu thuẫn RLS pattern đã chốt. |
| **Workspace = tenant alias (1:1 mãi mãi)** | Phủ nhận DoD charter "1 tenant có N workspace"; chặn multi-ws tương lai. Backfill 1:1 chỉ là default, KHÔNG là constraint. |
| **Build `workspace_members` ngay W2** | Premature (psql: 0 user multi-ws hiện tại); đụng JWT claim shape = đụng auth boundary nhạy cảm. Defer Wave 6 qua D2-bis. |
| **Wire quota gate vào riêng worker (không route)** | Quá muộn — doc đã INSERT; fairness phải chặn ở write boundary (route) trước HNSW bloat (P2-H IQ-1 impact). |

---

## 5. Implementation plan Phase 4 (failing-test-first) + gate metric

**Failing-test-first:**
1. **RED** `test_workspaces_backfill` — sau migration 1, mỗi distinct `bots.workspace_id` có đúng 1 row trong `workspaces` (scope tenant). Assert UNIQUE(record_tenant_id, slug) chặn dup.
2. **RED** `test_ingest_quota_fires` (đóng IQ-1) — tenant A `quotas.documents_per_day_limit=2`, POST `/documents/stream-upload` 3× → lần 3 trả **429 QuotaExceeded** (hôm nay GREEN-fail vì gate vắng → chứng minh test có răng). Lặp lại cho `/documents/ingest` batch.
3. **RED** `test_quota_cascade_tenant_workspace` — set ws_limit < tenant_limit → upload cạn ws trước → 429 dù tenant còn headroom; ngược lại tenant cạn → 429 dù ws còn.
4. **RED** `test_workspaces_rls_tenant_scope` (khớp D3 leak-test) — as `ragbot_app`, `SET LOCAL app.tenant_id=A` → `SELECT count(*) FROM workspaces WHERE record_tenant_id=B` = **0**; role-guard `assert rolbypassrls=false` (tái dùng harness D3).
5. **RED** `test_role_definitions_scope_dropped` — sau migration 2, `scope` cột không còn (hoặc bảng re-seeded nếu giữ); grep `\.scope` trong rbac path = 0.
6. Code → GREEN; full pytest 0 regression.

**Gate metric (charter AN TOÀN + RẺ):**
- **Intra-tenant 2-ws leak-test (data) = 0 cross-workspace row** trên bots/documents — khớp D3 leak-test (`@pytest.mark.rls_integration`, skip khi thiếu `DATABASE_URL_APP`).
- **Quota cascade test pass** (tenant + ws tier độc lập đếm).
- **Ingest-quota fires**: 3rd upload → 429 trên CẢ 2 route thật (không chỉ demo).
- 91Q graded HALLU=0 + ≥85/91 (không regression do gate mới).

---

## 6. CLAUDE.md compliance self-audit

| Rule | Check | Reason |
|---|---|---|
| #0 No-guess (evidence) | ✅ | Mọi claim có `file:line`/psql/alembic; drift 0036-seed-vs-empty đánh nhãn ↔️ + defer verify Phase 4. GIẢ THUYẾT (RBAC product-need) label rõ. |
| #1 Think-before-coding | ✅ | Trade-off RBAC (b) 2 nhánh + recommend; alternatives §4 explicit. |
| #2 Simplicity-First | ✅ | Giữ global-per-tenant, defer `workspace_members` (no premature FGA); entity additive, không FK chặn write. |
| #3 Surgical | ✅ | THÊM bảng workspaces + wire quota; KHÔNG đổi 4-key, KHÔNG đổi 2 graph. |
| #4 Goal-driven (test reproduce) | ✅ | §5 failing-test-first 5 RED test + gate metric đo được. |
| Sacred #6 4-key identity | ✅ | Tuple KHÔNG đổi; entity tham chiếu qua slug; `record_workspace_id`-in-tuple bị reject §4. |
| Sacred #10 no app-inject/override | ✅ | D2 KHÔNG đụng answer-path / sysprompt (S10 riêng); chỉ data/quota/authz boundary. |
| Zero-hardcode | ✅ | Default limit/window từ `system_config`/`constants`; `WORKSPACE_SYSTEM_SLUG`/`DEFAULT_*_LEVEL` reuse hằng có sẵn; 0=unlimited semantics giữ. |
| Domain-neutral | ✅ | Không brand/industry; ws slug do tenant đặt, validate format only. |
| No-version-ref | ✅ | Tên `workspaces`/`workspace_members` purpose-named; alembic numbered = exception hợp lệ. |
| No-psql-hotfix | ✅ | Mọi thay đổi qua alembic tracked (migration 1/2/3) + route audited; không UPDATE tay. |
| Tenant isolation (RLS) | ✅ | (d) workspaces scope `record_tenant_id` ENABLE+FORCE; khớp D3 GUC vừa wire. |
| Strategy+DI | ✅ | IngestQuotaService construct ở bootstrap (DI), inject vào route; WorkspaceRepository qua port. |
| T1/T2/T3 declared | ✅ | Tier [T2-Safety/CostPerf] — workspace boundary + fairness; KHÔNG đụng T1 retrieval. |
| Model-tier | ✅ | ADR-author = main session; subagent chỉ research (program override Fable 5, read-only). |

**Verdict**: APPROVED WITH FIX — fix = Phase 4 phải reconcile drift `role_definitions` seed-vs-empty (0036 INSERT vs psql 0 rows) TRƯỚC khi drop `scope`; nếu bảng thật sự là dead-weight thì known-limitation, không xoá bảng (chỉ cột).
