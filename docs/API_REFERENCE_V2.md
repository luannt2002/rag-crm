# Ragbot API Reference (V2)

> **Scope**: Production HTTP surface for the V2 Jina-v3 stack.
> **Source-of-truth**: `src/ragbot/interfaces/http/routes/` + `src/ragbot/interfaces/http/router.py`. Live OpenAPI is served at `GET /docs` when the API is running.
> **Domain-neutral**: every example uses `<bot-name>`, `<tenant-name>`, `<host>`, `<token>` placeholders. Substitute with values from your `.env` and `bots` table.
> **Last verified**: 2026-05-01 against router commit `5cdf17c`.

---

## 1. Conventions

### 1.1 Base path

All routes (except `/health` and `/health/models`) are mounted under the configurable prefix `app.api_base_path` (default `/api/ragbot`). Examples below use the default; override via `RAGBOT_API_BASE_PATH` if you remap.

| Mount | Prefix |
| :--- | :--- |
| Health (liveness/readiness) | `/health` |
| Health (per-bot model resolver) | `/health/models` |
| Production chat + sync + documents + jobs | `/api/ragbot/...` |
| Admin (RBAC + AI + bots + analytics + audit + GDPR) | `/api/ragbot/admin/...` |
| Test platform (HTML + harness API) | `/api/ragbot/test/...` |

### 1.2 Authentication — JWT bearer

Every non-health route requires `Authorization: Bearer <token>`. Two token classes are accepted by the same middleware (`tenant_context.py`):

| Class | Algorithm | Issued by | `state.role` examples | Tenant scope |
| :--- | :--- | :--- | :--- | :--- |
| **Service JWT** | HS256 (signed with `RAGBOT_API_TOKEN`) | NestJS upstream / platform tools | `service`, `super_admin` | Body `tenant_id` MUST match `tenant_id` claim (cross-check enforced); `super_admin` may bypass |
| **User JWT** | RS256 | Customer auth provider, JWKS fetched | `tenant_admin`, `editor`, `viewer`, … | Embedded in token claims |

The middleware refuses requests where the body `tenant_id` mismatches the JWT `tenant_id` claim with HTTP `403 tenant_mismatch` (super-admin bypass).

### 1.3 3-key external identity — REQUIRED

Production routes that act on a bot **must** carry the full external 3-key tuple in the request body or query:

```jsonc
{
  "tenant_id":    1,                  // int — required
  "bot_id":       "<bot-name>",       // str — required, ≤ 64 chars
  "channel_type": "web"               // str — required, ≤ 32 chars
}
```

The internal UUID `record_bot_id` is resolved server-side via `BotRegistryService.lookup(...)` and is **never** accepted from the wire (Phase-6 hard-cut).

### 1.4 Trace + correlation

Every response includes the `trace_id` (also returned via the `X-Trace-Id` header). Use it when filing tickets — it joins `request_logs`, `request_steps`, `audit_log`, and structlog JSON output.

### 1.5 Standard error envelope

```json
{
  "error": {
    "code": "<machine-code>",
    "message": "<human-readable>",
    "trace_id": "<uuid>"
  }
}
```

| Code | HTTP | Cause |
| :--- | :---: | :--- |
| `validation_error` | 422 | Pydantic field missing/invalid (e.g. tenant_id missing on `/chat`) |
| `tenant_mismatch` | 403 | JWT `tenant_id` ≠ body `tenant_id` (no super-admin) |
| `bot_not_found` | 404 | 3-key tuple did not resolve via `BotRegistryService` |
| `permission_denied` | 403 | RBAC level < required, or token missing module/action permission |
| `rate_limited` | 429 | Tenant per-minute or monthly token cap exhausted (fail-closed) |
| `payload_too_large` | 413 | Body > 256 KB chat / 16 MB ingest (`body_size` middleware) |
| `internal_error` | 500 | Unhandled exception (always logged with `exc_info=True`) |

### 1.6 Rate limiting

- Per-tenant per-minute window stored in `tenants.rate_limit_per_min` (Redis sliding window). Override per-bot via `bots.bypass_rate_limit = true`.
- Monthly token cap (`tenants.monthly_token_cap`) enforced in `chat_worker` after invocation log.
- Rate-limiter is **fail-closed**: a Redis outage rejects requests rather than letting them through (Sprint-12B decision).

---

## 2. Health endpoints (no auth)

### 2.1 `GET /health`

Liveness + readiness merged. Always returns HTTP 200; the `status` field tells the truth so orchestrators do not loop-restart on transient hiccups.

```bash
curl -s http://<host>:3004/health
```

```json
{
  "status": "ok",
  "version": "ragbot/2026-05-01",
  "dependencies": { "postgres": "ok", "redis": "ok" },
  "pool_stats": {
    "db_in_use": 1, "db_idle": 4, "db_overflow": 0, "db_size": 5,
    "redis_in_use": 0, "redis_available": 50, "redis_max": 50
  }
}
```

`status` ∈ `ok | degraded | down`. Route on the body, not on HTTP status.

### 2.2 `GET /health/models`

Per-bot model resolver smoke. Verifies `bot_model_bindings` → `ai_models` → provider key live ping (commit `01fd439`). Use after every binding edit and in CI/CD post-deploy.

```bash
curl -s "http://<host>:3004/health/models?tenant_id=1&bot_id=<bot-name>&channel_type=web"
```

Returns per-purpose status (`embedding`, `rerank`, `llm_primary`, …) — flags the four V2 bug classes (purpose drift, dim mismatch, ingest bypass, prefix-unaware preflight) before they bite.

---

## 3. Chat — production

### 3.1 `POST /api/ragbot/chat`

Submit a question; returns `202 Accepted` with a `job_id` (default `mode="async"` enqueues to chat-worker via Redis Streams). Use `mode="sync"` for the legacy blocking path; `mode="async"` is the production default.

**Request body** (`ChatRequest`):

| Field | Type | Required | Notes |
| :--- | :--- | :---: | :--- |
| `tenant_id` | int | yes | External 3-key |
| `bot_id` | str ≤ 64 | yes | External slug |
| `channel_type` | str ≤ 32 | yes | e.g. `web`, `zalo`, `api` |
| `user_id` | str ≤ 100 | yes | End-user identifier |
| `content` | str ≤ 2000 | yes | User question |
| `history_limit` | int 1–N | no | Default `DEFAULT_HISTORY_LIMIT` |
| `system_prompt` | str | no | Per-call override; only honoured for super-admin tokens |
| `external_message_id` | str | no | Idempotency key from upstream NestJS |
| `mode` | `"sync" \| "async"` | no | Default `"async"` |
| `callback_url` | str (HTTP/HTTPS) | no | Webhook for async result |

**curl example**:

```bash
curl -X POST http://<host>:3004/api/ragbot/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 1,
    "bot_id": "<bot-name>",
    "channel_type": "web",
    "user_id": "u-001",
    "content": "Câu hỏi của bạn",
    "mode": "async"
  }'
```

**Python httpx**:

```python
import httpx, os

resp = httpx.post(
    f"{os.environ['RAGBOT_BASE_URL']}/api/ragbot/chat",
    headers={"Authorization": f"Bearer {os.environ['RAGBOT_TOKEN']}"},
    json={
        "tenant_id": 1,
        "bot_id": "<bot-name>",
        "channel_type": "web",
        "user_id": "u-001",
        "content": "Câu hỏi của bạn",
        "mode": "async",
    },
    timeout=30.0,
)
resp.raise_for_status()
job = resp.json()
# poll job["status_url"] until status_url returns final result
```

**Response (202)**:

```json
{
  "ok": true,
  "job_id": "f5b6...",
  "status": "queued",
  "status_url": "/api/ragbot/jobs/f5b6...",
  "trace_id": "abc-..."
}
```

### 3.2 `POST /api/ragbot/chat/stream`

Production SSE variant. Same body schema as `/chat`. Returns `text/event-stream` with per-token deltas. Used by web UI for low-TTFT UX.

```bash
curl -N -X POST http://<host>:3004/api/ragbot/chat/stream \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{ "tenant_id":1, "bot_id":"<bot-name>", "channel_type":"web",
         "user_id":"u-001", "content":"Hỏi gì đó" }'
```

Event types: `delta` (token), `meta` (citations), `done` (final), `error` (terminal failure).

### 3.3 `POST /api/ragbot/feedback`

Record up/down feedback on an assistant message.

```bash
curl -X POST http://<host>:3004/api/ragbot/feedback \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 1, "bot_id": "<bot-name>", "channel_type": "web",
    "conversation_id": "<uuid>", "message_id": "<uuid>",
    "user_id": "u-001", "rating": "up"
  }'
```

### 3.4 `GET /api/ragbot/jobs/{job_id}`

Poll an async chat job. Returns the final `JobStatusResponse` once the worker completes.

---

## 4. Sync (NestJS upstream) — bot + corpus lifecycle

All `/sync/*` routes are gated by `require_permission_dep("sync", "<action>")` and emit a forensic `audit_log` row.

### 4.1 `POST /api/ragbot/sync/bot`

Upsert a bot row scoped by 3-key. Used by NestJS to provision a bot before document ingest.

```bash
curl -X POST http://<host>:3004/api/ragbot/sync/bot \
  -H "Authorization: Bearer <service-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 1,
    "bot_id": "<bot-name>",
    "channel_type": "web",
    "bot_name": "Customer Support",
    "system_prompt": "Bạn là trợ lý...",
    "temperature": 0.3,
    "max_tokens": 450
  }'
```

### 4.2 `POST /api/ragbot/sync/documents`

Bulk ingest documents (chunk + embed + store). Default mode is **soft upsert** (replace by `source_url`); `wipe_existing=true` is super-admin only and hard-deletes every doc for the bot before ingest (the legacy default that nuked KBs on partial syncs — Sprint S9-P0 fix).

```bash
curl -X POST http://<host>:3004/api/ragbot/sync/documents \
  -H "Authorization: Bearer <service-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 1,
    "bot_id": "<bot-name>",
    "channel_type": "web",
    "wipe_existing": false,
    "documents": [
      { "title": "FAQ #1", "content": "...", "url": "https://...", "source_type": "faq" }
    ]
  }'
```

Response includes per-document `chunks` + `embedded` counts. Body cap 16 MB.

### 4.3 `GET /api/ragbot/sync/documents`

List documents for a bot. `tenant_id` is a **required** query parameter (3-key v3 hard-cut).

```bash
curl "http://<host>:3004/api/ragbot/sync/documents?tenant_id=1&bot_id=<bot-name>&channel_type=web&limit=50" \
  -H "Authorization: Bearer <token>"
```

### 4.4 `DELETE /api/ragbot/sync/documents`

Wipe corpus for a bot (super-admin only).

```bash
curl -X DELETE http://<host>:3004/api/ragbot/sync/documents \
  -H "Authorization: Bearer <super-admin-token>" \
  -H "Content-Type: application/json" \
  -d '{ "tenant_id":1, "bot_id":"<bot-name>", "channel_type":"web" }'
```

---

## 5. Admin — bots, AI providers, policy, analytics

All admin routes require `Authorization: Bearer <token>` with role level ≥ admin (60) unless noted otherwise. RBAC is metadata-driven — actual minimum levels live in DB (`module_permissions`).

### 5.1 Bots — `POST /api/ragbot/admin/bots`, `PATCH`, `DELETE`, `GET /admin/bots`

CRUD operations on the `bots` table. Cache reload at `POST /api/ragbot/admin/bots/cache/reload` busts Redis after manual DB edits.

### 5.2 AI providers — `/admin/ai/providers`, `/admin/ai/models`, `/admin/ai/bindings`

- `GET / POST / PATCH / DELETE` for `ai_providers`, `ai_models`, `bot_model_bindings`.
- After binding edits, call `/admin/cache/reload` (or wait for the per-key TTL) and verify with `/health/models`.

### 5.3 Policy — `/admin/policies`, `/admin/ai/models/{id}/capability`

Manage allow-lists for purpose-binding (e.g. only embedding models bind to `purpose='embedding'`). Used by Sprint-13 per-tenant policy rollout.

### 5.4 Tenant policy — `/admin/tenant-policy`

Per-tenant overrides (rate-limit window, monthly token cap, policy module flags).

### 5.5 Analytics — `/admin/analytics/bots/{pass-rate|cost|latency}`, `/admin/analytics/bots/{id}/drift`

Read-only aggregate counters surfaced to the admin dashboard.

### 5.6 Audit — `/admin/audit`, `/admin/audit/overview`, `/admin/audit/query-detail`

Forensic read of `audit_log` (admin-only). Pagination via cursor; do **not** rely on `audit_log` for runtime metrics — use `request_steps` instead.

### 5.7 Metrics — `/admin/metrics/{requests|steps|active-models}`

JSON snapshot of internal Prometheus counters for ops dashboards. The full `/metrics` Prometheus exposition is mounted separately by ASGI middleware.

### 5.8 GDPR — `DELETE /admin/gdpr/erase/message/{id}`, `DELETE /admin/gdpr/erase/conversation/{id}`

Right-to-be-forgotten endpoints (writes audit row, cascades to vector store).

### 5.9 System config — `GET /api/ragbot/admin/config`, `PUT /api/ragbot/admin/config/{key}`

Live-edit `system_config`. After PUT the key's Redis cache is busted automatically (per-key TTL invalidation pattern).

---

## 6. Test platform (`/api/ragbot/test/*`)

Internal test harness for QA + load-test runs. Routes here are **not** part of the customer-facing contract — they expose dev tokens, harness chat, generate-test-questions, quality dashboards, and direct Redis inspection. RBAC required. Used by `scripts/agent_d_loadtest.py` and the Locust harness.

Key endpoints:

| Method + path | Purpose |
| :--- | :--- |
| `POST /api/ragbot/test/chat` | Synchronous chat for harness (returns the full final answer in one response) |
| `POST /api/ragbot/test/chat/stream` | SSE harness variant |
| `GET /api/ragbot/test/tokens/self` | Issue a short-lived dev JWT for the caller (RBAC: tenant_admin+) |
| `POST /api/ragbot/test/tokens` | Mint a service token for a tenant (super-admin) |
| `GET /api/ragbot/test/bots/{bot_id}/{channel_type}/quality-dashboard` | Aggregate harness PASS-rate, refuse, FAITH proxy |
| `GET /api/ragbot/test/admin/redis/keys` | Redis introspection (super-admin) |

The `pages_router` from `test_chat.py` (mounted at root, `include_in_schema=False`) serves the operator-monitor HTML.

---

## 7. Documents (job-driven path)

`POST /api/ragbot/documents` and `DELETE /api/ragbot/documents/...` provide the job-driven ingest pipeline (parallel to `/sync/documents`). The two paths share `DocumentService`; choose `/sync` for synchronous bulk upstream-driven ingest, `/documents` for foreground UI uploads.

---

## 8. Versioning + deprecation

- Wire-level breaking changes ship behind a new prefix (e.g. `/api/ragbot/v2/`); `app.api_base_path` would change accordingly.
- Body-schema additions are non-breaking; removals require a migration plan in `plans/YYMMDD-...`.
- `bot_model_bindings.purpose` enum is **frozen** at: `embedding | rerank | llm_primary | grading | grounding | rewriting | understand_query | decompose`. Drift causes V2 BUG #1 — see `docs/V2_MIGRATION_BUG_LESSONS.md`.

---

## 9. See also

- `docs/ONBOARDING_GUIDE.md` — provision a fresh tenant from zero.
- `docs/ARCHITECTURE_DIAGRAMS.md` — flow diagrams for chat, sync, RBAC.
- `docs/PERFORMANCE_TUNING.md` — p95 budget breakdown and parallelisation knobs.
- `docs/TROUBLESHOOTING.md` — symptom → root-cause for V2 bug classes and harness pitfalls.
- `docs/V2_MIGRATION_BUG_LESSONS.md` — pre-flight gate for any binding/embedding change.
- Live OpenAPI: `GET /docs` (Swagger UI) + `GET /openapi.json`.
