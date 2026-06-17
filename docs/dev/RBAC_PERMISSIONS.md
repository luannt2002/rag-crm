# RBAC — Phân quyền hệ thống ragbot

> File chuẩn cho mọi vấn đề authorization. SSoT code: `src/ragbot/shared/rbac.py`. SSoT data: bảng `role_definitions` + `module_permissions`.

---

## 1. Mô hình 7-tier numeric level

```
100 super_admin / owner / system    Platform-wide; nuốt mọi tenant
 80 tenant / tenant_admin           Workspace owner; quản lý tài nguyên trong tenant
 60 admin / service                 Vận hành (đọc/sửa cấu hình, audit); service token
 40 operator                        Vận hành read + một số write ngách
 20 user                            End-user gọi API chat
 10 viewer                          Read-only
  0 guest                           Chưa auth
```

**Tại sao numeric thay vì enum string?**
- Compare đơn giản: `level >= 60` thay vì `role in ("admin", "superadmin", ...)`.
- Khoảng cách 20 giữa các tier → chèn role mới (vd `tenant_viewer=70`) không phá legacy.
- Alias bằng cùng level — tránh string-typo cross-system (`owner ≡ super_admin ≡ system = 100`).

**Bảng ROLE_LEVELS** (`src/ragbot/shared/rbac.py:18`):

| role_name | level | Alias của |
|---|---|---|
| `super_admin` / `superadmin` / `platform_admin` / `owner` / `system` | 100 | nhau |
| `tenant` / `tenant_admin` | 80 | nhau |
| `admin` / `service` | 60 | service token = admin level |
| `operator` | 40 | |
| `user` | 20 | |
| `viewer` | 10 | |
| `guest` (default unknown) | 0 | |

---

## 2. Nguồn role — đến từ đâu

| Layer | Source | Khi nào set | Ghi vào |
|---|---|---|---|
| **JWT bearer** | `api_tokens.role` column | Lúc cấp token | `request.state.role` (TenantContextMiddleware) |
| **Self-token** (`/test/tokens/self`) | Hardcoded `"owner"` cho dev | Endpoint dev-only (RAGBOT_DEV_TOKEN_ENABLED) | Same |
| **JWT iss claim** | JWT verifier reject nếu issuer mismatch | Mỗi request | trước khi role lift |
| **Tenant scope** | JWT claim `record_tenant_id` (UUID) | Mỗi request | `request.state.record_tenant_id` |

**Workflow request → role**:

```
HTTP request với Authorization: Bearer <jwt>
     │
     ├─→ JwtVerifier.verify(jwt)               (infrastructure/security/jwt_auth.py)
     │      └─ iss == "ragbot", exp OK, signature OK
     │      └─ claims: {sub, role, ver, record_tenant_id, ...}
     │
     ├─→ TenantContextMiddleware                (interfaces/http/middlewares/tenant_context.py)
     │      └─ request.state.role = claims["role"]
     │      └─ request.state.record_tenant_id = UUID(claims["record_tenant_id"])
     │
     └─→ Route handler
            └─ require_min_level(request, 60)   (raise ForbiddenError nếu fail)
```

---

## 3. API enforcement — 3 cách dùng

### 3.1 Numeric (preferred — code-level)

`src/ragbot/shared/rbac.py:42`:

```python
from ragbot.shared.rbac import require_min_level

@router.delete("/admin/bots/{bot_id}")
async def delete_bot(bot_id: str, request: Request):
    require_min_level(request, 80)   # tenant or higher
    ...
```

→ `ForbiddenError` (HTTP 403) khi role < 80.

### 3.2 Helper `_require_owner` (legacy, chỉ trong `test_chat.py`)

```python
def _require_owner(request: Request) -> None:
    require_min_level(request, 100)
```

→ Tương đương `require_min_level(request, 100)`. Dùng cho admin endpoint nhạy cảm (system_config update, API key rotate).

### 3.3 DB-driven (`module_permissions` table)

`alembic 0036_rbac_tables` ship 2 bảng:

**`role_definitions`**: mapping role_name → level. Currently empty (code-driven `ROLE_LEVELS` dict dùng thay).

**`module_permissions`**: 1 row mỗi (module, permission) tuple → `min_role_level` int.

```
 module |       permission        | min_role_level
--------+-------------------------+----------------
 tenant | policy_update           |            100
 ai     | binding_update          |             80
 ai     | provider_rotate_key     |             80
 admin  | audit_query_detail_read |             60
 ai     | cache_reload            |             60
 bot    | cache_status            |             60
```

→ Route handler dùng `effective_permission_level(module, permission)` → fetch min level từ DB → enforce. Cho phép admin flip permission qua SQL UPDATE mà không deploy code.

---

## 4. Tenant scope — phân quyền cross-tenant

Numeric level **CHỈ check 1 chiều dọc** (level đủ cao). Cần kết hợp với **tenant scope** để chống cross-tenant leak.

### Rule TUYỆT ĐỐI (CLAUDE.md "IDENTITY RULE — 4-KEY"):

```python
# WRONG — chỉ check level, không check tenant scope
require_min_level(request, 80)
bot = await bot_repo.get_by_id(bot_id)   # ← cross-tenant leak!

# RIGHT — kết hợp level + tenant
require_min_level(request, 80)
record_tenant_id = request.state.record_tenant_id
bot = await bot_repo.get_by_id_and_tenant(bot_id, record_tenant_id)  # atomic
if bot is None:
    raise NotFound  # tenant không match → giả vờ chưa từng tồn tại
```

**TOCTOU defense** (commit `24f6b43` Issue #20 fix): mutate phải dùng atomic UPDATE WHERE id+tenant_id, không SELECT-check-UPDATE.

**Super admin (level 100) bypass tenant scope**: chỉ duy nhất role `super_admin` / `system` được nhìn toàn platform. Mọi role khác **PHẢI** giới hạn ở `record_tenant_id` claim.

---

## 5. Route catalogue — endpoint nào yêu cầu level gì

### `/admin/*` (highest gate)

| Endpoint | Level | Note |
|---|---|---|
| `PUT /admin/config/{key}` | **100** (`_require_owner`) | system_config write |
| `PUT /admin/api-keys/{provider}` | 100 | Provider key rotate (commit 29c00df) |
| `DELETE /admin/api-keys/{provider}/{label}` | 100 | Key soft-delete |
| `GET /admin/api-keys` | 100 | List (fingerprint only) |
| `GET /admin/redis/keys` | 100 | Redis introspect |
| `GET /audit/list` | 60 | Audit log query |
| `GET /audit/overview` | 60 | Audit aggregate |
| `GET /analytics/bots/*` | 40 (operator) | Read-only stats |

### `/api/ragbot/test/*` (per-bot ops)

| Endpoint | Level | Note |
|---|---|---|
| `POST /test/chat` | 20 (user) | End-user chat |
| `POST /bots/{bot_id}/{channel}/documents` | 80 (tenant) | Upload doc |
| `DELETE /bots/{bot_id}` | 80 | Bot lifecycle |
| `GET /bots/{bot_id}/{channel}/documents` | 60 | List corpus |

### Per-tenant resource ownership

Ngoài level check, **luôn check `record_tenant_id` match**:

```python
@router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: str, request: Request):
    require_min_level(request, 80)
    record_tenant_id = request.state.record_tenant_id
    # Atomic — không có TOCTOU
    result = await session.execute(
        text("DELETE FROM bots WHERE bot_id = :bid AND record_tenant_id = :tid"),
        {"bid": bot_id, "tid": record_tenant_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, "not found")
```

---

## 6. Token role + rate-limit (`api_tokens` table)

`alembic 0018_api_tokens_role_ratelimit`:

```
api_tokens (
  id UUID,
  service_name VARCHAR(128),
  token_hash VARCHAR(64),       -- SHA256 của token plaintext
  role VARCHAR(16),              -- 'owner' / 'admin' / 'service' / 'user' / ...
  rate_limit_value INTEGER,      -- 0 = unlimited (paid tier), N = N req/window
  rate_limit_window INTEGER,     -- giây
  revoked_at TIMESTAMPTZ,
  ...
)
```

**Pattern revenue feature** (xem `feedback_bypass_flags_revenue_feature.md`):
- Free tier: `rate_limit_value=120, rate_limit_window=60` (120 req/phút)
- Paid tier: `rate_limit_value=0` (unlimited)
- Owner internal: `rate_limit_value=0, role='owner'` (level 100)

`bypass_cache` / `bypass_rate_limit` flags trên body request **chỉ honor** khi token có `role >= admin (60)` HOẶC paid plan flag set.

---

## 7. Anti-abuse middleware (orthogonal với RBAC)

`AntiAbuseMiddleware` (`interfaces/http/middlewares/anti_abuse.py`) chạy **TRƯỚC** RBAC enforcement. Ban IP dựa trên:
- Auth-fail counter (3 lần 401/403 trong 5 phút → ban 30 phút)
- 4xx response ratio (>50% trong 100 req → "suspicious" flag → rate-limit chặt hơn)
- Loadtest bypass token (operator-only, `RAGBOT_LOADTEST_BYPASS_TOKEN` env) skip cả 2 counter (loopback only)

Lưu Redis key `ragbot:antiabuse:ban:{ip}`. Operator clear ban: `redis-cli DEL ragbot:antiabuse:ban:<ip>`.

---

## 8. Audit trail — mọi mutation level≥60 phải log

`audit_log` table mỗi action sửa cấu hình:

```python
audit_repo.write_audit(
    _audit_entry(
        request,
        action="system_config_update",
        resource_type="system_config",
        resource_id=key,
        before={"value": old_value},
        after={"value": new_value},
    )
)
```

Field bắt buộc: `actor_role`, `actor_token_id`, `actor_record_tenant_id`, `action`, `resource_type`, `resource_id`, `before` (JSONB), `after` (JSONB), `timestamp`.

→ Forensic trail cho compliance (GDPR, ISO 27001).

---

## 9. Pattern code chuẩn

### ✅ ĐÚNG

```python
from ragbot.shared.rbac import require_min_level

@router.put("/admin/config/{key}")
async def update_config(key: str, request: Request):
    require_min_level(request, 100)            # 1. level gate
    record_tenant_id = request.state.record_tenant_id  # 2. tenant scope (nếu cần)
    # ... business logic
    await audit_repo.write_audit(...)           # 3. audit trail
```

### ❌ SAI

```python
# Hardcode role string — fragile, không scale
if request.state.role != "admin":
    raise Forbidden
```

```python
# SELECT-then-UPDATE — TOCTOU race
bot = await session.get(Bot, bot_id)
if bot.record_tenant_id != request.state.record_tenant_id:
    raise Forbidden
await session.delete(bot)        # ← race window
```

```python
# Bỏ qua audit
require_min_level(request, 80)
await session.execute("UPDATE ...")  # missing audit
```

---

## 10. Test coverage

| Test file | Cover |
|---|---|
| `tests/unit/test_rbac.py` | `get_role_level`, `check_min_level`, `require_min_level` |
| `tests/integration/test_rbac_admin_routes.py` | Admin route 401/403 matrix |
| `tests/integration/test_rbac_role_matrix.py` | Full 7-tier × 20+ endpoint matrix |
| `tests/integration/test_rbac_admin_ai_resource_ownership.py` | Tenant scope + ownership atomic check |
| `tests/integration/test_rls_cross_tenant.py` | Postgres RLS cross-tenant leak |
| `tests/integration/test_resource_ownership_toctou.py` | Issue #20 TOCTOU race fix |

---

## 11. SSoT files (1-look reference)

| File | Vai trò |
|---|---|
| **`src/ragbot/shared/rbac.py`** | Code SSoT — `ROLE_LEVELS` + `require_min_level` |
| **`alembic/versions/20260422_0036_rbac_tables.py`** | DB schema (`role_definitions`, `module_permissions`) |
| **`alembic/versions/20260417_0018_api_tokens_role_ratelimit.py`** | Token role + rate-limit columns |
| `src/ragbot/infrastructure/security/jwt_auth.py` | JWT verify + claims extract |
| `src/ragbot/interfaces/http/middlewares/tenant_context.py` | Lift JWT claims → `request.state.*` |
| `src/ragbot/interfaces/http/middlewares/anti_abuse.py` | IP ban orthogonal layer |
| `scripts/seed_rbac_permissions_s11b.py` | Seed `module_permissions` rows |

---

## 12. Roadmap

| Việc | Status |
|---|---|
| 7-tier numeric levels | ✅ done |
| `role_definitions` + `module_permissions` DB | ✅ schema ship, data seed partial |
| Numeric `require_min_level` qua codebase | ✅ ~95% routes converted (Issue #20) |
| `bots.record_tenant_id` enforcement | ✅ atomic UPDATE WHERE (commit 24f6b43) |
| Token role + rate-limit | ✅ |
| Audit trail mutations | ✅ |
| TOCTOU race fix | ✅ (Issue #20) |
| RLS Postgres-level (defense-in-depth) | ✅ partial (`document_chunks`, `documents`) |
| Per-endpoint permission via `module_permissions` DB lookup | 🟡 ~30% routes adopted |
| Role hierarchy override (super_admin bypass tenant) | ⚠ implicit, chưa explicit test |
| RBAC admin UI | ❌ defer (CLI/SQL admin tạm đủ) |

---

## 13. Quick reference cho code reviewer

Khi review PR thêm route mới:

1. ✅ Có `require_min_level(request, X)` ngay đầu handler không?
2. ✅ Level X có phù hợp với severity (read=40, write=60, admin=80, system=100)?
3. ✅ Resource handler có check `record_tenant_id` match (nếu liên quan tenant)?
4. ✅ Mutate dùng atomic SQL (`WHERE id AND tenant_id`) không phải SELECT-check-UPDATE?
5. ✅ Có `audit_repo.write_audit(...)` cho mutation level≥60?
6. ✅ Test coverage: ít nhất 1 turn 401, 1 turn 403, 1 turn 200 cho mỗi endpoint mới.

→ Thiếu 1 trong 6 = REJECT.
