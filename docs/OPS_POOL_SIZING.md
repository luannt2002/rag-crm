# OPS — Pool Sizing & Uvicorn Workers (P25-A6 + P25-A7)

Operational tuning notes for the ragbot-api service. Keep this in sync
with `.env`, `start.sh`, `deploy.sh`, and
`src/ragbot/config/settings.py` (see the `DatabaseSettings` docstring
for the Go-equivalent mapping: `pool_size` ≈ `SetMaxIdleConns`,
`pool_size + max_overflow` ≈ `SetMaxOpenConns`, `pool_recycle` ≈
`SetConnMaxLifetime`, `pool_timeout` = wait-for-conn ceiling before
raising).

## Current sizing

| Layer | Setting | Value | Where |
|-------|---------|-------|-------|
| Uvicorn | workers | 2 | `start.sh`, `deploy.sh` |
| Uvicorn | limit_concurrency (per worker) | 200 | `start.sh`, `deploy.sh` |
| Uvicorn | timeout_keep_alive | 30s | `start.sh`, `deploy.sh` |
| Uvicorn | timeout_graceful_shutdown | 30s | `start.sh`, `deploy.sh` |
| DB pool | pool_size | 50 | `.env` → `DATABASE_POOL_SIZE` |
| DB pool | max_overflow | 50 | `.env` → `DATABASE_MAX_OVERFLOW` |
| DB pool | pool_timeout | 5s | `.env` → `DATABASE_POOL_TIMEOUT` |
| DB pool | pool_recycle | 1800s | `.env` → `DATABASE_POOL_RECYCLE` |
| Redis pool | pool_size | 50 | `.env` → `REDIS_POOL_SIZE` |

Per-worker DB pool hard cap = 50 + 50 = **100 conns**. With 2 workers
that is **200 potential conns** at saturation. Each chat holds 6–10
short-lived sessions so ~500 concurrent chats is the design target
before `pool_timeout=5s` starts failing requests fast (rather than
queueing 30s and stalling the event loop).

## Postgres `max_connections` requirement

**Required:** `max_connections` ≥ 150 on the Postgres server (covers
the ~200 theoretical API ceiling in steady state plus headroom for
background workers, alembic migrations, and manual ops / psql
sessions).

**Current server state (checked 2026-04-23):** `SHOW max_connections;`
returned **100** on `<db-host>:5432` (`ragbot_v2_dev`). This is
**below the requirement** — under a spike that saturates both workers'
overflow pools, Postgres will reject new connections with
`FATAL: sorry, too many clients already`.

### Required server-side change (ops owner, NOT ragbot repo)

```sql
-- as superuser on <db-host>
ALTER SYSTEM SET max_connections = 200;
-- restart Postgres for the change to take effect
```

Allow ~10 MB shared memory per extra connection when bumping this on a
constrained host. If the DB is shared with other services, coordinate
a ceiling that leaves room for their pools too.

Until this is raised, keep an eye on the
`engine.pool.checkedout / pool.overflow` metrics and dial
`DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` down if production
traffic triggers `QueuePool limit ... reached`.

## Rationale recap

- **2 uvicorn workers** — each runs its own event loop; single-worker
  asyncio saturates at ~0.1 chat/s under blocking calls. 2 workers ≈
  2× HTTP ingress capacity while still fitting the box.
- **limit_concurrency=200 per worker** — back-pressure cap that
  returns 503 instead of letting the event loop starve.
- **timeout_graceful_shutdown=30s** — in-flight chats drain before
  SIGKILL on deploys.
- **pool_timeout=5s** — fail-fast; never hold a request 30s waiting
  for a free conn.
- **Redis pool=50** — already configured in `settings.py`
  (`RedisSettings.pool_size`).

## P15-1 preflight — BM25 / tsvector readiness (2026-04-25)

Checked installed Postgres extensions against the P15-1 "pg textsearch"
blocker listed in `plans/260425-S8-reranker-activation/`:

| Requirement | Extension | Status |
|---|---|---|
| Built-in FTS (`to_tsvector`, `tsquery`, `ts_rank`) | (core) | ✅ available |
| Trigram fallback + `%` similarity | `pg_trgm` | ✅ installed `1.6` |
| Vietnamese accent-fold | `unaccent` | ✅ installed `1.1` |
| Dense vectors | `vector` (pgvector) | ✅ installed |
| Composite indexes over JSONB | `btree_gin` | ✅ installed |

There is no separate `pg_textsearch` extension — Postgres FTS is core
and the tokenizer/stop-lemma pipeline we need is `pg_trgm + unaccent`
+ `simple` dict (already wired in `shared/vi_tokenizer.py`). P15-1
retrieval swap is therefore **UNBLOCKED from an infra standpoint**;
remaining work is application-layer (populate `documents.raw_content`
on new ingest — shipped S8 δ1 migration 0040 — and add a BM25 edge to
the hybrid retriever).
