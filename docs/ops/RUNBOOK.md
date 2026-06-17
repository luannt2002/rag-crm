# Ragbot Operational Runbook

> Owner: Platform / SRE on-call
> Audience: ops-on-call, devops, release engineer
> Scope: day-to-day service operation — start/stop, health, common errors, hot-reload, cache bust, migrations
> Companion docs: [`DISASTER_RECOVERY.md`](./DISASTER_RECOVERY.md) (full DR plan), [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) (legacy DR procedures kept for parity)
> Domain-neutral: this runbook uses generic placeholders (`<server-host>`, `<bucket>`, `<bot-uuid>`) — no tenant literal.

---

## 0. Quick Reference Card

| Need | Command / Endpoint |
|---|---|
| App liveness | `curl -s http://<server-host>:3004/health` |
| Model providers health | `curl -s http://<server-host>:3004/health/models` |
| Tail API logs | `journalctl -u ragbot-api -f --since "10 min ago"` |
| Tail worker logs | `journalctl -u ragbot-worker -f --since "10 min ago"` |
| Hot-reload config | `redis-cli DEL ragbot:system_config:*` then any request |
| Bust bot registry | `redis-cli --scan --pattern 'ragbot:bot:*' \| xargs -r redis-cli DEL` |
| Bust semantic cache | `psql -c "TRUNCATE semantic_cache;"` |
| Replay DLQ | `python scripts/replay_dlq.py --limit 100` |
| Cost snapshot | `python scripts/cost_audit.py today` |

---

## 1. Service Topology

| Process | systemd unit | Port | Depends on |
|---|---|---|---|
| HTTP API (FastAPI) | `ragbot-api` | 3004 | Postgres, Redis |
| Chat worker (Redis Streams consumer) | `ragbot-worker` | — | Postgres, Redis, LLM/embed/rerank providers |
| Postgres 16 + pgvector | `postgresql` | 5432 | local disk |
| Redis Stack 7.4 | `redis` | 6379 | local disk (RDB optional) |

All processes read `.env` from `/var/www/html/ragbot/.env`. `python-dotenv` is loaded by every pydantic sub-setting block — **no manual export needed once systemd `EnvironmentFile=` is wired**.

---

## 2. Service Startup / Shutdown / Restart

### 2.1 Cold start (after host reboot)

```bash
# Order matters — DB and Redis MUST be Ready before app.
systemctl start postgresql
systemctl start redis
# Wait for DB ready (~5s) before starting app.
until pg_isready -h 127.0.0.1 -p 5432; do sleep 1; done
systemctl start ragbot-api ragbot-worker
```

Verify:

```bash
curl -s http://localhost:3004/health | jq '.status'           # "ok"
curl -s http://localhost:3004/health/models | jq '.summary'   # all "ok"
```

### 2.2 Rolling restart (after config / code deploy)

```bash
# Always: API first (drains traffic via reverse proxy timeout), then worker.
systemctl restart ragbot-api
sleep 5
curl -fs http://localhost:3004/health > /dev/null || { echo "API DOWN"; exit 1; }
systemctl restart ragbot-worker
sleep 5
journalctl -u ragbot-worker --since "30 sec ago" | grep -iE "started|consumer_ready"
```

### 2.3 Graceful shutdown (for maintenance window)

```bash
# Stop API first to stop accepting new traffic; worker drains its Redis Stream pending list.
systemctl stop ragbot-api
# Wait for worker pending list to drain (or kill after 60s for hard cutoff)
redis-cli XPENDING ragbot:chat:requests workers | head -3
systemctl stop ragbot-worker
# Optional: also stop infra
systemctl stop redis postgresql
```

### 2.4 Docker Compose local dev

```bash
docker compose up -d postgres redis
# Wait for healthchecks (≤15s)
docker compose ps
# Then run app on host or in another container
set -a && source .env && set +a
.venv/bin/uvicorn ragbot.interfaces.http.app:app --host 0.0.0.0 --port 3004
```

---

## 3. Log Locations + Grep Recipes

| Log | Path |
|---|---|
| API stdout (structlog JSON) | `journalctl -u ragbot-api` |
| Worker stdout | `journalctl -u ragbot-worker` |
| Postgres | `/var/log/postgresql/postgresql-16-main.log` |
| Redis | `/var/log/redis/redis-server.log` |
| Nginx (reverse proxy) | `/var/log/nginx/ragbot-{access,error}.log` |
| Audit trail (DB table) | `SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 50;` |
| Cost replay (JSONL) | `~/.claude/projects/-var-www-html-ragbot/*.jsonl` (Claude Code only) |

### Useful greps

```bash
# All errors in last hour
journalctl -u ragbot-api --since "1 hour ago" -o cat \
  | jq -r 'select(.level=="error") | "\(.timestamp) \(.event) \(.error_type // "") \(.error_msg // "")"'

# Slow queries (P95 > 2s in node)
journalctl -u ragbot-worker --since "1 hour ago" -o cat \
  | jq -r 'select(.event=="node.complete" and .duration_ms > 2000) | "\(.node_name) \(.duration_ms)"'

# HALLU fabricate events (zero-tolerance)
journalctl -u ragbot-worker -o cat \
  | jq -r 'select(.event=="hallu.fabricate_detected") | .'
# Expect: empty. Any output → page on-call.

# Circuit breaker opens (provider degradation)
journalctl -u ragbot-worker -o cat | jq -r 'select(.event=="circuit_breaker.open")'

# Per-tenant cost trend (replay last 24h)
python scripts/audit_per_tenant_cost.py --since "$(date -u -d '1 day ago' +%Y-%m-%dT%H:%MZ)"
```

---

## 4. Health Endpoints

| Endpoint | Owner | What it checks |
|---|---|---|
| `GET /health` | app | DB ping, Redis ping, alembic head match |
| `GET /health/models` | app | each registered `ai_provider` reachable; CB state |
| `GET /health/models?skip_smoke=false` | app | + send tiny canary request to every model |
| `GET /metrics` (internal port) | app | Prometheus scrape — token/latency/error counters |

A `/health` 200 only means **infra reachable**. Use `/health/models?skip_smoke=false` for full smoke before declaring "service restored" post-incident.

```bash
curl -s http://localhost:3004/health/models?skip_smoke=false \
  | jq '.checks[] | {provider, model, status, latency_ms, cb_state}'
```

Source: `src/ragbot/interfaces/http/routes/health.py`, `health_models.py`.

---

## 5. Common Errors + Remediation

### 5.1 `500 — RetrievalError: pgvector index missing`

**Symptom**: `/test/chat` returns 500, log shows `IndexUndefinedError` or `relation "document_chunks_embedding_idx" does not exist`.

**Cause**: `alembic upgrade head` skipped after schema bump, or index was dropped manually.

**Fix**:

```bash
cd /var/www/html/ragbot
set -a && source .env && set +a
.venv/bin/alembic current
.venv/bin/alembic upgrade head     # idempotent
psql "$DATABASE_URL" -c "\di document_chunks_*"   # confirm index visible
systemctl restart ragbot-worker
```

### 5.2 `429 — provider rate limited`

**Symptom**: spike of `circuit_breaker.open` events for one provider (`jina`, `openai`, `cohere`).

**Fix is automatic**: CB OPEN → strategy falls back to `NullReranker` / next-priority LLM. Verify fallback works:

```bash
# Look for "fallback_applied" event right after CB open
journalctl -u ragbot-worker --since "5 min ago" -o cat \
  | jq -r 'select(.event=="strategy.fallback_applied") | "\(.from)->\(.to)"'
```

Manual mitigation if storm persists ≥ 15 min:

```bash
# Increase CB cool-down to 10 min via config hot-reload
psql "$DATABASE_URL" <<'SQL'
UPDATE system_config SET value = '600' WHERE key = 'circuit_breaker_cooldown_s';
SQL
redis-cli DEL ragbot:system_config:circuit_breaker_cooldown_s
```

### 5.3 `Bot lookup failed — 4-key mismatch`

**Symptom**: HTTP 404 `BOT_NOT_FOUND` despite the row existing.

**Cause**: caller missing `record_tenant_id` JWT claim, or `workspace_id` format invalid.

**Diagnostic**:

```bash
# 1. Confirm row exists with intended 4-key
psql "$DATABASE_URL" -c "SELECT record_bot_id, record_tenant_id, workspace_id, bot_id, channel_type FROM bots WHERE bot_id='<slug>' AND channel_type='<ch>';"

# 2. Check Redis cache for stale entry (cross-tenant cache poisoning defence)
redis-cli KEYS 'ragbot:bot:*:<slug>:<ch>'

# 3. Bust if poisoned
redis-cli --scan --pattern 'ragbot:bot:*' | xargs -r redis-cli DEL
```

Identity rule reference: `CLAUDE.md` IDENTITY RULE — 4-KEY REQUIRED.

### 5.4 Worker stuck — Redis Stream pending list growing

**Symptom**: `XPENDING ragbot:chat:requests workers` > 50, `/health` still 200.

**Cause**: worker consumed message but crashed before XACK, or LLM timeout > consumer timeout.

**Fix**:

```bash
# 1. Inspect pending
redis-cli XPENDING ragbot:chat:requests workers - + 10

# 2. Auto-claim stale (idle > 60s) — worker has built-in claimer; force a cycle:
systemctl restart ragbot-worker

# 3. If specific consumer name is gone (host reboot), reassign:
redis-cli XCLAIM ragbot:chat:requests workers <new-consumer> 60000 <message-id>

# 4. Persistently failing → DLQ
python scripts/replay_dlq.py --limit 100 --dry-run    # inspect first
python scripts/replay_dlq.py --limit 100              # actually replay
```

### 5.5 OOM / Redis eviction warnings

**Symptom**: `redis-cli INFO stats | grep evicted_keys` > 1000/min.

**Cause**: working set exceeds `maxmemory 2gb` ceiling (configured in `docker-compose.yml`).

**Fix**:

```bash
# 1. Confirm which keyspace is bloating
redis-cli --bigkeys

# 2. Tactical bust (semantic_cache lives in pgvector, not Redis — usually it is the embedding-cache namespace)
redis-cli --scan --pattern 'ragbot:embed:*' | xargs -r redis-cli DEL

# 3. Strategic: raise maxmemory in compose override + restart
docker compose up -d redis
```

LRU eviction is configured (`allkeys-lru`) so this is graceful degradation, not data loss — embeddings re-compute on next miss. See `docker-compose.yml` `redis` service comment block.

### 5.6 HALLU fabricate detected (zero-tolerance)

**Symptom**: any `hallu.fabricate_detected` event in worker log, or load-test golden-set regression.

**Response**: SEV-1. Page lead engineer immediately. See [`DISASTER_RECOVERY.md`](./DISASTER_RECOVERY.md) §4.

Containment:

```bash
# 1. Capture context
journalctl -u ragbot-worker --since "30 min ago" -o cat \
  | jq -r 'select(.event=="hallu.fabricate_detected")' > /tmp/hallu-incident.jsonl

# 2. If a specific bot is the source, switch it to a stricter system_prompt
#    (NEVER patch refusal text in code — bot_owner owns oos_answer_template)
psql "$DATABASE_URL" -c "UPDATE bots SET system_prompt = '<stricter-prompt>' WHERE record_bot_id = '<bot-uuid>';"
redis-cli --scan --pattern 'ragbot:bot:*<bot-uuid>*' | xargs -r redis-cli DEL
```

---

## 6. Config Hot-Reload

`system_config` is the source of truth, Redis is the cache (TTL 60s).

```bash
# 1. Inspect current values
psql "$DATABASE_URL" -c "SELECT key, value, updated_at FROM system_config ORDER BY key;"

# 2. Update one knob (example: rerank top-k)
psql "$DATABASE_URL" -c "UPDATE system_config SET value='20' WHERE key='rerank_top_k';"

# 3. Bust cache so next request reads fresh
redis-cli DEL ragbot:system_config:rerank_top_k
# Or nuclear: bust all
redis-cli --scan --pattern 'ragbot:system_config:*' | xargs -r redis-cli DEL

# 4. Verify
curl -s http://localhost:3004/admin/system-config | jq '.rerank_top_k'
```

**Per-bot config** lives in the `bots` row (system_prompt, oos_answer_template, custom_vocabulary, pipeline_config JSON). Same bust pattern but key `ragbot:bot:<tenant>:<workspace>:<slug>:<channel>`.

---

## 7. Cache Bust Quick Reference

| Cache | Key pattern | Bust |
|---|---|---|
| `system_config` (Redis) | `ragbot:system_config:<key>` | `redis-cli DEL` |
| Bot registry (Redis) | `ragbot:bot:<tenant>:<ws>:<slug>:<ch>` | `redis-cli DEL` |
| Embedding (Redis) | `ragbot:embed:<sha256>` | scan + DEL |
| Semantic cache (pgvector) | table `semantic_cache` | `TRUNCATE` or `DELETE WHERE record_bot_id=...` |
| Exact-hash cache (pgvector) | table `exact_hash_cache` | `TRUNCATE` |
| Prompt cache (Anthropic / internal) | provider-side | not bustable; rotates by content hash |

---

## 8. Migration Procedures

### 8.1 Apply pending migrations (zero-downtime path)

```bash
cd /var/www/html/ragbot
set -a && source .env && set +a

# 1. Inspect what's pending
.venv/bin/alembic current
.venv/bin/alembic heads
.venv/bin/alembic history --indicate-current | tail -20

# 2. Dry-run SQL (capture for change-mgmt ticket)
.venv/bin/alembic upgrade head --sql > /tmp/migration-$(date +%F).sql
less /tmp/migration-$(date +%F).sql

# 3. Apply
.venv/bin/alembic upgrade head

# 4. Smoke
.venv/bin/python scripts/preflight_check.py    # must exit 0
curl -s http://localhost:3004/health | jq '.alembic_head_match'   # true
```

### 8.2 Rollback last migration (only if reversible)

```bash
.venv/bin/alembic downgrade -1
# If migration was destructive (drop column / rename) — STOP. Use DR procedure §2.2 PITR instead.
```

### 8.3 Online DDL pattern for big tables

For `document_chunks` (>1M rows), prefer:

```sql
CREATE INDEX CONCURRENTLY ... ;   -- never blocks writes
ALTER TABLE ... ADD COLUMN ... DEFAULT NULL;   -- instant in PG16
-- Then backfill in batches via Python script, not in migration.
```

Migration files at `alembic/versions/`. History is immutable — version-ref strings in those files are exempted from the no-version rule.

---

## 9. Monitoring Dashboards

| Dashboard | URL (placeholder) | Source |
|---|---|---|
| RAG quality (faithfulness, grounded, refuse rate) | `http://<grafana-host>/d/ragbot-quality` | Prometheus + `eval_per_bot_golden.py` cron |
| Latency P50/P95/P99 (per-node) | `http://<grafana-host>/d/ragbot-latency` | `/metrics` scrape |
| Token + cost (per-tenant, per-bot) | `http://<grafana-host>/d/ragbot-cost` | `audit_per_tenant_cost.py` materialized view |
| Provider CB state | `http://<grafana-host>/d/ragbot-providers` | `/health/models` exporter |
| HALLU fabricate counter | same `ragbot-quality` board, panel **HALLU=0 sacred** | structlog event `hallu.fabricate_detected` |

Alert routing (PagerDuty / Opsgenie):

| Severity | Condition | Page |
|---|---|---|
| SEV-1 | HALLU fabricate counter > 0 in 5 min | lead engineer immediately |
| SEV-1 | `/health` 5xx for 3 consecutive scrapes | on-call primary |
| SEV-2 | P95 latency > 2× rolling-7d baseline for 10 min | on-call primary |
| SEV-2 | DLQ depth > 200 | on-call primary |
| SEV-3 | CB OPEN on any provider for > 30 min | on-call secondary, Slack |
| SEV-3 | Tenant cost overrun > 150% baseline | finance ops, no page |

---

## 10. On-Call Rotation

| Role | Coverage | Response |
|---|---|---|
| Primary on-call | 24/7 weekly rotation, follow-the-sun if multi-region staffed | SEV-1 ack ≤ 5 min, SEV-2 ack ≤ 15 min |
| Secondary on-call | shadow primary | take over if primary unreachable for 10 min |
| Lead engineer | business hours + SEV-1 escalation | HALLU=0 / cross-tenant leak / data-loss decisions |
| Tenant CSM | business hours | external comms (see `DISASTER_RECOVERY.md` §6 templates) |

Handover protocol (Friday EOW):

1. Outgoing primary writes a 5-line handover note in `#ragbot-oncall` Slack: open incidents, ongoing watches, planned deploys.
2. Incoming primary acks in-channel within 24h.
3. Schedule maintained in PagerDuty calendar; manual sync to a shared calendar weekly.

On-call expectations:

- Laptop reachable within ack-SLO; runbook bookmarked.
- DO NOT make destructive changes (`DROP`, `TRUNCATE`, `FLUSHDB`) without secondary on-call concurrence in chat.
- DO NOT patch code on a production host — always deploy from git via standard pipeline.
- Always update an incident channel as you go; post-mortem template lives in `docs/dev/POSTMORTEM_TEMPLATE.md` (create if absent).

---

## 11. Operational Scripts

All under `scripts/`:

| Script | Purpose |
|---|---|
| `scripts/preflight_check.py` | full pre-deploy sanity: alembic head, env vars, provider keys reachable |
| `scripts/cost_audit.py` | rolling cost / model-mix / sonnet-leak (Claude Code replay) |
| `scripts/audit_per_tenant_cost.py` | per-tenant token/cost rollup |
| `scripts/replay_dlq.py` | replay dead-letter queue (chat worker) |
| `scripts/owner_action/run_all.sh` | per-bot golden eval (smartness gate) |
| `scripts/smoke_embedding_e2e.py` | one-shot end-to-end embed → store → search |
| `scripts/streaming_smoke_test.py` | SSE / streaming endpoint smoke |
| `scripts/test_bot_smoke.py` | full chat 1-turn smoke |
| `scripts/backup_db.sh` | wrapper around `pg_dump` (see DR plan §1) |

When adding a new script: place under `scripts/ops/` for ops-only utilities, update this table, and ensure no domain literal — config from `.env` or `system_config` only.

---

## 12. Pre-Deploy Checklist

Before any prod release:

- [ ] `git pull && git status` clean
- [ ] `.venv/bin/alembic heads` returns single head
- [ ] `.venv/bin/alembic upgrade head --sql` reviewed
- [ ] `python scripts/preflight_check.py` exits 0
- [ ] `pytest tests/unit -x -q` passes
- [ ] `pytest tests/integration -x -q -m "not slow"` passes
- [ ] Load-test smoke (5 bots × 10Q) HALLU fabricate = 0
- [ ] PagerDuty schedule covers next 24h
- [ ] Rollback plan written (last good SHA + alembic revision)

---

## 13. References

- DR plan: [`DISASTER_RECOVERY.md`](./DISASTER_RECOVERY.md)
- Legacy DR runbook (kept for parity): [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md)
- Architecture: `RAGBOT_MASTER.md`, `docs/master/`
- Identity rule (4-key): `CLAUDE.md` IDENTITY RULE
- Health endpoints: `src/ragbot/interfaces/http/routes/health.py`, `health_models.py`
- Compose / infra: `docker-compose.yml`
- System config keys: `scripts/init_system_config.py`
- Cost replay tool: `scripts/cost_audit.py`
