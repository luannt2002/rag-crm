# Onboarding Guide — Provision a fresh Ragbot tenant

> **Audience**: ops + devops onboarding a new tenant (or a fresh staging environment) onto Ragbot.
> **Goal**: from "nothing" → first 3 smoke queries answered correctly, in ~30 minutes.
> **Domain-neutral**: every literal below uses `<bot-name>`, `<tenant-name>`, `<host>` placeholders. Do **not** hard-code customer/brand strings into tracked files (CLAUDE.md zero-tolerance rule).
> **Last verified**: 2026-05-01 against migration HEAD `0054`.

---

## Overview — 10 steps, ~30 min

| Step | Action | ETA |
| :---: | :--- | :---: |
| 1 | Provision Postgres 14+ (with `vector` ext) and Redis 7+ | 5 min |
| 2 | Configure `.env` (env-var checklist) | 3 min |
| 3 | `alembic upgrade head` (HEAD = `0054`) | 1 min |
| 4 | Seed `system_config` defaults | 1 min |
| 5 | Seed RBAC permissions (60 module rows) | 1 min |
| 6 | Insert tenant row + bot row (3-key) | 2 min |
| 7 | Seed `bot_model_bindings` (Jina v3 stack) | 2 min |
| 8 | Upload first corpus via `POST /sync/documents` | 5 min |
| 9 | Run `scripts/preflight_check.py` | 1 min |
| 10 | Smoke 3 queries + verify HALLU=0 trap | 5 min |

If any step fails, see [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) before retrying.

---

## Step 1 — Provision Postgres + Redis

Postgres 14+ with the `vector` extension; Redis 7+ (no Cluster, no AOF requirement). The default Compose stack is fine for staging:

```bash
docker compose up -d postgres redis
docker compose exec postgres psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose exec postgres psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

Production uses managed Postgres (RDS / Cloud SQL / equivalent) with `vector` ≥ 0.7 and `max_connections ≥ 100`. Redis is single-instance (high-throughput, no clustering) — failover via your platform's HA primitive.

---

## Step 2 — Configure `.env`

Copy `.env.example` → `.env`. Required keys (the app refuses to start without them):

| Variable | Purpose |
| :--- | :--- |
| `DATABASE_URL` | Async Postgres DSN (asyncpg) |
| `DATABASE_URL_SYNC` | Sync DSN for Alembic |
| `REDIS_URL` | `redis://...` |
| `RAGBOT_SECRET_KEY` | App secret (random 32-byte hex) |
| `RAGBOT_API_TOKEN` | HS256 service-JWT signing key |
| `OPENAI_API_KEY` | LLM provider (default generation/grading stack) |
| `EMBEDDING_JINA_API_KEY` | Jina v3 embeddings |
| `RERANKER_JINA_API_KEY` | Jina v3 rerank |

Optional but recommended:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `RAGBOT_API_BASE_PATH` | `/api/ragbot` | Mount prefix override |
| `LOG_LEVEL` | `INFO` | structlog level |
| `RAGBOT_BASE_URL` | — | Used by smoke + harness scripts |
| `COHERE_API_KEY` | unset | Optional rerank fallback (NullReranker if unset) |

**Secrets rule**: never commit real values. Place placeholders in `.env.example` only, real values in `.env` (gitignored). Tenant-identifier literals (brand hostnames, prod DSNs, real bearer tokens) are forbidden in any tracked file (CLAUDE.md domain-neutral rule).

After editing, source it before any CLI:

```bash
set -a && source .env && set +a
```

---

## Step 3 — `alembic upgrade head`

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current   # → expect "0054 (head)"
```

If `current` shows older than `0054`:
- Pull latest code (`git pull`).
- Re-run `alembic upgrade head`. Bug-class V2 #1 (`purpose='reranker'` drift) is fixed by `0053`; do **not** roll back below it.

---

## Step 4 — Seed `system_config`

```bash
.venv/bin/python scripts/init_system_config.py
```

This idempotently inserts the global defaults for chunk size, RRF k, ef_search, cache TTLs, threshold floors, etc. Re-running is safe (no-op when keys exist; fresh keys are added on schema bumps).

To inspect:

```bash
.venv/bin/python -c "
import asyncio
from ragbot.application.services.system_config_service import SystemConfigService
# ...connect + .all() — see scripts/init_system_config.py for the canonical pattern
"
```

Edit live via `PUT /api/ragbot/admin/config/{key}` (super-admin) — Redis bust is automatic.

---

## Step 5 — Seed RBAC permissions

```bash
.venv/bin/python scripts/seed_rbac_permissions_s11b.py
```

Inserts the 60 `module_permissions` rows (`chat:submit`, `sync:bot_upsert`, `admin:bots:write`, …) and the 7-tier role table. **Idempotent.** Without it every authenticated request 403s.

Verify count (expect ≥ 60):

```sql
SELECT count(*) FROM module_permissions;
```

---

## Step 6 — Insert tenant + bot rows (3-key)

A tenant row and an empty bot row must exist before sync. Replace `<tenant-name>`, `<bot-name>`, `<channel>` with your values.

```sql
-- Tenant
INSERT INTO tenants (id, name, status, rate_limit_per_min, monthly_token_cap)
VALUES (1, '<tenant-name>', 'active', 120, 10000000)
ON CONFLICT (id) DO NOTHING;

-- Bot — 3-key uniqueness enforced by uq_bots_tenant_bot_channel
INSERT INTO bots (id, tenant_id, bot_id, channel_type, bot_name, system_prompt, is_deleted)
VALUES (
  gen_random_uuid(), 1, '<bot-name>', 'web', 'Customer assistant',
  'Bạn là trợ lý chăm sóc khách hàng. Chỉ trả lời dựa trên tài liệu đã cung cấp. Nếu không có thông tin, từ chối lịch sự.',
  false
)
ON CONFLICT (tenant_id, bot_id, channel_type) DO NOTHING;
```

`tenant_id` is INT (external upstream key). `bot_id` is the slug your callers send. `channel_type` is opaque (`web`, `zalo`, `messenger`, …). All three are **NOT NULL** at the schema (alembic 0049) — see CLAUDE.md "3-key identity REQUIRED".

Alternative: use `POST /api/ragbot/sync/bot` with a service token (NestJS pattern).

---

## Step 7 — Seed `bot_model_bindings` (Jina v3 stack)

The V2 default stack is Jina v3 (1024-dim embeddings + rerank-v3) + GPT-4.1-mini for generation. Seed for the bot you just created:

```bash
.venv/bin/python scripts/db/seed_jina_v3_binding.py
```

Inspect what was bound:

```sql
SELECT b.bot_id, b.channel_type, mb.purpose, m.model_name, p.code
FROM bots b
JOIN bot_model_bindings mb ON mb.record_bot_id = b.id
JOIN ai_models m ON m.id = mb.record_model_id
JOIN ai_providers p ON p.id = m.record_provider_id
WHERE b.bot_id = '<bot-name>' AND b.channel_type = 'web'
ORDER BY mb.purpose;
```

Expect rows for: `embedding`, `rerank`, `llm_primary`, `grading`, plus optionally `rewriting`, `understand_query`, `decompose`. Drift checklist: `purpose='reranker'` (legacy) is **forbidden**; the migration `0053` enforces `'rerank'` only.

Override later via `PATCH /api/ragbot/admin/ai/bindings/{id}` then `POST /api/ragbot/admin/cache/reload`.

---

## Step 8 — Upload first corpus

Mint a service token (super-admin) for the upload:

```bash
TOKEN=$(curl -s -X POST http://<host>:3004/api/ragbot/test/tokens \
  -H "Authorization: Bearer $RAGBOT_BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":1,"role":"super_admin","ttl_seconds":3600}' | jq -r .token)
```

Push documents (small batch first to validate pipeline):

```bash
curl -X POST http://<host>:3004/api/ragbot/sync/documents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 1,
    "bot_id": "<bot-name>",
    "channel_type": "web",
    "wipe_existing": false,
    "documents": [
      { "title": "FAQ #1", "content": "...", "url": "https://...", "source_type": "faq" },
      { "title": "FAQ #2", "content": "...", "url": "https://...", "source_type": "faq" }
    ]
  }'
```

Response shows per-doc `chunks` + `embedded` counts. Validate in DB:

```sql
SELECT count(*) FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
JOIN bots b ON b.id = d.bot_id
WHERE b.bot_id = '<bot-name>' AND b.channel_type = 'web' AND b.tenant_id = 1
  AND dc.embedding IS NOT NULL;
```

If `embedding IS NULL` for new chunks → V2 BUG #3 (`DocumentService` ingest path bypassing per-bot resolver). Workaround: re-embed via `scripts/emergency_restore_embeddings.py`. Permanent fix tracked in `docs/V2_MIGRATION_BUG_LESSONS.md §BUG-3`.

Body cap is 16 MB; chunk ingest larger corpora into multiple POSTs.

---

## Step 9 — Pre-flight gate

```bash
.venv/bin/python scripts/preflight_check.py            # human-readable
.venv/bin/python scripts/preflight_check.py --strict   # warnings → exit 1 (CI gate)
.venv/bin/python scripts/preflight_check.py --json     # machine-readable
```

The script verifies: DB reachable, Alembic at HEAD, every `system_config` key set, every `bot_model_bindings.purpose` valid, every provider key resolvable, no `purpose='reranker'` drift. **Make this your CI/CD post-deploy gate.**

Then exercise the live model resolver:

```bash
curl -s "http://<host>:3004/health/models?tenant_id=1&bot_id=<bot-name>&channel_type=web" | jq .
```

All purposes should return `status: "ok"`.

---

## Step 10 — Smoke 3 queries

Mint a chat token (tenant_admin level is enough):

```bash
TOKEN=$(curl -s -X POST http://<host>:3004/api/ragbot/test/tokens \
  -H "Authorization: Bearer $RAGBOT_BOOTSTRAP_TOKEN" \
  -d '{"tenant_id":1,"role":"tenant_admin","ttl_seconds":3600}' | jq -r .token)
```

Run a real question, an out-of-scope question (must refuse), and a pricing-trap question (must refuse — HALLU=0 sacred contract):

```bash
for q in \
  "Câu hỏi có đáp án trong corpus" \
  "Vé máy bay đi Paris bao nhiêu" \
  "Giá dịch vụ X là bao nhiêu (chưa có trong corpus)"; do
  curl -s -X POST http://<host>:3004/api/ragbot/test/chat \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"tenant_id\":1,
      \"bot_id\":\"<bot-name>\",
      \"channel_type\":\"web\",
      \"user_id\":\"smoke-1\",
      \"content\":\"$q\"
    }" | jq -r '.answer // .error // .'
  echo "---"
done
```

**Expect**:
1. Question 1 → grounded answer with citations.
2. Question 2 → refusal phrased per bot's `system_prompt` (no fabrication).
3. Question 3 → refusal (no invented price). If a number appears that is not in corpus → HALLU breach, escalate per [`TROUBLESHOOTING.md` §HALLU-breach](TROUBLESHOOTING.md).

---

## Step 11+ — Production handover

After smoke:

1. **Set tenant rate-limit** (`tenants.rate_limit_per_min`) appropriately. Default 120/min is for staging.
2. **Enable monthly token cap** (`tenants.monthly_token_cap`) — fail-closed once hit.
3. **Wire NestJS upstream** — point its sync worker at `POST /api/ragbot/sync/bot` and `/sync/documents`.
4. **Hook /health into your LB** — orchestrators read `body.status`, not HTTP status.
5. **Subscribe to `/metrics`** — 13 Prometheus counters live; dashboards in `docs/audit/` examples.
6. **Run a 50-turn harness** with `scripts/agent_d_loadtest.py` to baseline PASS-rate + p95 before opening to real traffic.

---

## See also

- [`docs/API_REFERENCE_V2.md`](API_REFERENCE_V2.md) — full HTTP surface.
- [`docs/ARCHITECTURE_DIAGRAMS.md`](ARCHITECTURE_DIAGRAMS.md) — visualises sync + chat + RBAC flow.
- [`docs/PERFORMANCE_TUNING.md`](PERFORMANCE_TUNING.md) — knobs to dial post-baseline.
- [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — symptom → fix table.
- [`docs/V2_MIGRATION_BUG_LESSONS.md`](V2_MIGRATION_BUG_LESSONS.md) — bug classes the preflight catches.
- [`CLAUDE.md`](../CLAUDE.md) — engineering rules every contributor must read.
