# Security — RAGbot

> Multi-tenant security model: defence-in-depth at schema, Pydantic, middleware, repository, and Redis layers.

---

## 3-key identity — the foundation

Every bot on the platform is uniquely identified by three external keys — **ALL THREE REQUIRED, NOT NULL**:

```
(tenant_id: int, bot_id: str, channel_type: str)
```

**Why three keys?** Two different tenants can independently choose the same `bot_id` slug (e.g., `"support"`). Resolving by `(bot_id, channel_type)` alone causes cross-tenant data leak. `tenant_id` is **not optional** — it is `NOT NULL` at the DB schema level, `int` (not `int | None`) in Pydantic, and required in every lookup path.

### Enforcement layers

| Layer | Enforcement |
| :--- | :--- |
| DB schema | `tenant_id`, `bot_id`, `channel_type` all `NOT NULL` on `bots` table |
| Unique constraint | `uq_bots_tenant_bot_channel(tenant_id, bot_id, channel_type)` |
| Pydantic | All three fields `int` / `str` required (no `Optional`) — `422` if missing |
| Middleware | JWT `tenant_id` vs body `tenant_id` mismatch → `403 Forbidden` |
| BotRegistryService | `lookup(tenant_id: int, bot_id: str, channel_type: str)` — no optional args |
| Redis registry key | `ragbot:bot:{tenant_id}:{bot_id}:{channel_type}` → `record_bot_id` |
| Internal queries | Use `record_bot_id` UUID **only** — no repeated 3-key scan inside DB |

### Anti-patterns (FORBIDDEN)

```python
# WRONG — missing tenant_id in query
SELECT * FROM bots WHERE bot_id = :bid AND channel_type = :ch

# WRONG — optional tenant_id
def lookup(tenant_id: int | None, bot_id: str, channel_type: str): ...

# RIGHT
SELECT * FROM bots WHERE tenant_id = :tid AND bot_id = :bid AND channel_type = :ch
def lookup(tenant_id: int, bot_id: str, channel_type: str): ...
```

---

## RBAC — 7-tier numeric levels

| Level | Role | Permissions |
| ---: | :--- | :--- |
| 100 | `super_admin` | Full platform access |
| 80 | `admin` | Provider/model CRUD + policy write |
| 60 | `tenant_admin` | Bot CRUD + document ingest + analytics |
| 40 | `editor` | Document ingest only |
| 20 | `viewer` | Read-only analytics |
| 0 | `guest` | Chat only (with `chat:send` permission) |

- 60 `module_permissions` seeded in DB
- 35 routes wired with `Depends(require_permission_dep)` (Sprint 11B)
- 117 red-team tests + 11 tenant-scope red-team tests — all pass
- Role strings NEVER hardcoded — use numeric levels from `shared/rbac.py`

```python
# WRONG
if role not in ("admin", "superadmin"):
    raise HTTPException(403)

# RIGHT
require_min_level(60)
```

---

## PostgreSQL Row-Level Security (RLS)

```sql
-- Every session sets tenant context before queries
SET LOCAL app.tenant_id = <tenant_id>;

-- RLS policy on data tables
CREATE POLICY tenant_isolation ON documents
  USING (record_tenant_id = current_setting('app.tenant_id')::uuid);
```

`TenantScopedRepository` base class handles `_ensure_tenant_scope()` before every query. Direct raw SQL without tenant filter is a CI lint violation.

---

## Authentication

| Token type | Algorithm | Use |
| :--- | :--- | :--- |
| Service token | HS256 | Server-to-server (NestJS backend → RAGbot) |
| User token | RS256 | Direct user requests (admin UI) |

Token revocation: `token_version` field in Redis, checked per request. Rotating `token_version` instantly invalidates all sessions for a user without key rotation.

---

## Rate limiting

| Scope | Default | Config key |
| :--- | :--- | :--- |
| Per user (`connect_id`) | 100 req / 3s | `rate_limit_per_user_value` + `rate_limit_per_user_window_s` |
| Per tenant (monthly token cap) | configurable | `PATCH /admin/tenant-policy` |
| Owner bypass | `bypass_rate_limit=true` on bot row | `bots.bypass_rate_limit` |

**Fails closed**: on Redis error, non-owner requests are rejected (P25-L6 safety default).

---

## Input validation

- Body size: 256 KB for chat, 16 MB for ingest (413 middleware)
- Unicode: NFKC normalization on all user input
- Pydantic: strict type validation on all request schemas
- CORS: explicit allowlist from `system_config.cors_allowed_origins` (empty by default)
- SSRF: URL allowlist on webhook delivery endpoints

---

## Secrets management

- All secrets in `.env` (gitignored)
- Code reads via `os.getenv()` or pydantic `BaseSettings(env_file=".env")`
- 0 brand literal / 0 credential in any tracked file (verified by pre-commit grep)
- `.env.example` contains only placeholder keys — never real values

### Scrub workflow (if violation found)

```bash
# 1. Find all occurrences
grep -rn -i "<brand-or-credential>" . \
  --exclude-dir=.venv --exclude-dir=.git --exclude-dir=node_modules

# 2. Fix: code → env-read; docs → replace with <placeholder>
# 3. Update .env.example with new env-var name (no real value)
# 4. Verify grep = 0 hits
# 5. Commit with honest message: "refactor: move tenant-specific config to env"
```

If credential was already pushed to remote: **rotate credential first**, then consider `git filter-repo` / BFG (destructive force-push — requires explicit user approval).

---

## GDPR

```bash
# Erase all data for a connect_id (super_admin only)
curl -X POST http://localhost:3004/admin/gdpr/erase \
  -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  -d '{"tenant_id": 1, "connect_id": "user-to-erase"}'
```

Cascade soft-delete: conversations → messages → request_logs. Hard delete available via separate migration script for compliance audits.

---

## Webhook security

- Per-bot HMAC-SHA256 signing on all outbound webhook payloads
- Callback URLs stored per-bot (not per-tenant); rotation via `PATCH /admin/bots/{id}`
- URL allowlist enforced before dispatch (SSRF protection)
