# IDENTITY_RULE_DETAIL — 4-key bot identity

> Detail file for the **IDENTITY RULE — 4-KEY REQUIRED** section of `CLAUDE.md`. Use the short rule there; come here for the resolve flow, anti-pattern catalogue, and layer-by-layer key usage.

---

## Identity contract

A bot on the platform is identified by **4 keys**, split between wire body and JWT bearer:

- **HTTP body 2-key**: `(bot_id: str, channel_type: str)` REQUIRED + `workspace_id: str | None` OPTIONAL.
- **JWT bearer claim**: `record_tenant_id: UUID` REQUIRED, lifted by `TenantContextMiddleware` onto `request.state.record_tenant_id`. Body NEVER carries the tenant UUID (defence vs. caller-spoofed claims).
- **Internal 4-key bot identity**: `(record_tenant_id: UUID, workspace_id: str, bot_id: str, channel_type: str)`.

### Workspace pass-through philosophy

Platform validates the slug FORMAT only — NEVER manages workspace lifecycle (no create / list / delete API). Tenant truyền sao platform lưu vậy. Truyền slug chưa từng tồn tại = data tự nhiên empty (no 404). Missing / null body field falls back to `str(record_tenant_id)`. Invalid format → HTTP 422 `WORKSPACE_ID_INVALID`.

### Legacy upstream INT-tenant translation

Legacy upstream clients still posting INT `tenant_id` in the body are accepted ONLY inside `/sync/*` and `/test-chat/*` routes, which translate INT → UUID via `tenants.config->>'upstream_tenant_id'` before hitting the `bots.record_tenant_id` UUID FK column. Production chat + document routes have NO body-supplied tenant claim — JWT only.

---

## Resolve flow (NOT NULL từ A đến Z)

```
JWT bearer Authorization: Bearer <token>
    -> TenantContextMiddleware verify -> request.state.record_tenant_id (UUID)
HTTP request body: { bot_id: str (req), channel_type: str (req), workspace_id: str | None (opt) }
    -> Pydantic validate — reject 422 if missing required keys / wrong type
    -> resolve_workspace_id(body_value, record_tenant_id=...) -> str
    -> WorkspaceIdValidator.validate(slug) -> 422 if format invalid
BotRegistryService.lookup(record_tenant_id, workspace_id, bot_id, channel_type)  # all 4 required
    -> Redis key: ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}
    -> DB fallback: find_by_4key(record_tenant_id: UUID, workspace_id: str, bot_id: str, channel_type: str)
        SQL: WHERE record_tenant_id = :rt AND workspace_id = :ws AND bot_id = :bid AND channel_type = :ch
record_bot_id (UUID INTERNAL) — 1-1 with the 4-key external tuple
```

Once `record_bot_id` is in hand, internal queries use **`record_bot_id` only** (it is unique). Keep `workspace_id` alongside `record_bot_id` on multi-workspace forensic tables when scoped lookup is required.

---

## Schema rules

- 4 columns on `bots`:
  - `record_tenant_id` UUID NOT NULL FK `tenants(id)`
  - `workspace_id` VARCHAR(64) NOT NULL
  - `bot_id` VARCHAR NOT NULL
  - `channel_type` VARCHAR NOT NULL
- HTTP body: 2-key REQUIRED + optional slug. JWT bearer carries `record_tenant_id` UUID.
- Slug format: `^[a-zA-Z0-9-]+$`, length 1-64. Accent / space / underscore rejected at ingress (Pydantic `Field`) and DB (`CHECK` regex).
- Tenant-level / forensic rows write `WORKSPACE_SYSTEM_SLUG = "system"`.
- Resolve via `BotRegistryService.lookup(record_tenant_id, workspace_id, bot_id, channel_type)` — never optional.
- Repository: `BotRepository.find_by_4key(record_tenant_id, workspace_id, bot_id, channel_type)`.
- Unique constraint: `uq_bots_record_tenant_workspace_bot_channel(record_tenant_id, workspace_id, bot_id, channel_type)`.

### Why 4 keys REQUIRED

- Two tenants × two workspaces can independently set `bot_id="support"` + `channel_type="web"` — slug is tenant-defined, no review.
- Resolving with fewer keys → **cross-tenant / cross-workspace data leak**.
- If `record_tenant_id` or `workspace_id` is optional/nullable, the unique constraint cannot enforce uniqueness; fallback paths regress to key collision.

---

## Layer-by-layer key usage

| Layer | Keys |
|---|---|
| HTTP request schema (body) | `(bot_id, channel_type)` REQUIRED + `workspace_id` OPTIONAL |
| JWT bearer claim | `record_tenant_id: UUID` REQUIRED |
| External resolve (lookup `bots`) | `(record_tenant_id, workspace_id, bot_id, channel_type)` — ALL 4 REQUIRED |
| Redis registry cache key | `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}` |
| DB unique constraint | `uq_bots_record_tenant_workspace_bot_channel(...)` — 4 cột NOT NULL |
| Internal queries (pgvector, documents, conversations, semantic_cache) | `record_bot_id` ONLY |
| DB composite index (data tables) | `record_bot_id` (+ `workspace_id` where multi-workspace forensic scoping) |
| Tenant-level / forensic rows (audit_log, system tables) | `workspace_id = WORKSPACE_SYSTEM_SLUG ("system")` |

---

## Anti-pattern catalogue (CẤM TUYỆT ĐỐI)

Each WRONG below = cross-tenant / cross-workspace leak risk.

```python
# WRONG — missing workspace_id in resolve SQL
SELECT * FROM bots WHERE record_tenant_id = :rt AND bot_id = :bid AND channel_type = :ch

# WRONG — workspace_id optional in resolve signature
def lookup(record_tenant_id: UUID, bot_id: str, channel_type: str,
           *, workspace_id: str | None = None): ...

# WRONG — column nullable in bots
workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

# WRONG — accept tenant UUID from body (caller-spoofable)
class ChatRequest(BaseModel):
    record_tenant_id: UUID  # NEVER from wire — JWT claim only

# WRONG — skip slug format validation
ws = body.workspace_id or str(rt)  # accepts diacritics / spaces

# RIGHT
SELECT * FROM bots
 WHERE record_tenant_id = :rt
   AND workspace_id = :ws
   AND bot_id = :bid
   AND channel_type = :ch

def lookup(record_tenant_id: UUID, workspace_id: str,
           bot_id: str, channel_type: str): ...

workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)

record_tenant = request.state.record_tenant_id  # JWT-bound only
ws = resolve_workspace_id(body.workspace_id, record_tenant_id=record_tenant)
WorkspaceIdValidator.validate(ws)  # raises WorkspaceIdInvalid -> 422
```
