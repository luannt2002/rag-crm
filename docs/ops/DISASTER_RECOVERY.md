# Ragbot Disaster Recovery Plan

> Owner: Platform / SRE lead
> Audience: ops-on-call, devops, lead engineer, CSM
> Scope: RTO/RPO targets, backup procedures, restoration procedures, failover scenarios, communication templates
> Companion docs: [`RUNBOOK.md`](./RUNBOOK.md) (daily ops), [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) (legacy DR procedures with concrete CLI; this file is the **plan**, that file is the **operator card**)
> Domain-neutral: this plan uses generic placeholders (`<server-host>`, `<bucket>`, `<bot-uuid>`, `<tenant-name>`).

---

## 0. Document Purpose

This document defines:

1. **Recovery objectives** the platform commits to (SLO, RTO, RPO).
2. **Backup strategy** that makes those numbers achievable.
3. **Restoration procedures** indexed by failure mode.
4. **Failover scenarios** for known recoverable disasters.
5. **Communication templates** to use during an incident.

It is a **plan** — concrete CLI lives in [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) and section §6 of [`RUNBOOK.md`](./RUNBOOK.md). On-call references this plan during the first 5 minutes of an incident, then switches to the operator runbook for execution.

---

## 1. Recovery Objectives (SLO / SLA)

### 1.1 Service-level targets

| Metric | Target | Measured by |
|---|---|---|
| **Availability** | 99.9% (≤ 43 min downtime / month) | uptime monitor on `/health` |
| **RTO** — Recovery Time Objective, full service | **30 min** | from incident declared → `/health/models?skip_smoke=false` all `ok` + smoke chat passes |
| **RPO** — Recovery Point Objective, persistent data (Postgres) | **5 min** | last successful WAL segment in off-host bucket |
| **RPO** — Recovery Point Objective, cache (Redis) | **rebuild-from-DB** | warm cache on first request — no formal RPO |
| **HALLU fabricate** rate after recovery | **0** (sacred) | post-restore golden eval (≥ 100 Qs per critical bot) |
| **Tenant isolation** under recovery | **0 leak** | post-restore 4-key cross-tenant integration test |

### 1.2 Severity definitions

| SEV | Condition | RTO | Escalation |
|---|---|---|---|
| **SEV-1** | Full outage, or HALLU fabricate confirmed, or cross-tenant leak | 30 min | primary + secondary + lead engineer + CSM |
| **SEV-2** | Partial outage (one provider, one tenant, degraded perf) | 2 hours | primary + secondary |
| **SEV-3** | Single-feature degraded, no user impact | next business day | primary |

### 1.3 Hard invariants — preserve during ANY recovery

These three must hold post-recovery; otherwise rollback further:

1. `bots.record_tenant_id IS NOT NULL` — no fallback / no shared-tenant rows.
2. `bots.workspace_id IS NOT NULL` — 4-key unique constraint `uq_bots_record_tenant_workspace_bot_channel` must still pass `\d bots`.
3. `record_bot_id` UUID PK is **stable** for an existing 4-key tuple — otherwise `document_chunks`, `semantic_cache`, `audit_log` lose their FK target.

If any restoration step would violate (1), (2), or (3) → STOP. Switch to PITR (§3.2) before continuing.

---

## 2. Backup Strategy

Two layers, both **must** be alive in production. Verified daily by cron self-check.

### 2.1 Postgres — pg_dump cron + PITR WAL archiving

**Layer A — daily logical dump** (RPO ≤ 24h, restore by `pg_restore`):

- Cron: 02:00 server-local, retain last 14 days.
- Output: encrypted (`gpg --recipient $BACKUP_GPG_ID`) custom-format dump.
- Off-host shipping: `aws s3 cp` to `s3://<bucket>/ragbot/`.
- Self-check: cron at 03:00 verifies file exists in bucket, `pg_restore --list` on a temp DB succeeds.

**Layer B — WAL archiving (PITR)** (RPO ≤ 5 min, restore to any minute):

- `postgresql.conf`: `wal_level=replica`, `archive_mode=on`, `archive_command='aws s3 cp %p s3://<bucket>/wal/%f'`, `archive_timeout=300` (force segment every 5 min).
- `pg_basebackup` weekly, retain last 4 base + all WAL.
- Self-check: cron verifies most recent WAL segment in bucket is ≤ 10 min old.

CLI: see [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §1.1.

### 2.2 Redis — RDB + AOF (cache only)

Redis is a **rebuild-able cache**, not a source of truth. Snapshot only for fast recovery:

- `save 900 1` (RDB every 15 min if ≥ 1 write).
- `appendonly yes`, `appendfsync everysec` (1-sec durability).
- Daily ship `dump.rdb` to bucket — same encrypt + cron pattern as Postgres.

If Redis is lost entirely, restart with empty state: bot registry, semantic cache, embedding cache **all rebuild** on first hit via DB fallback paths. Cold-start cost: O(minutes) of higher embedding API spend until warm.

Production override: see comments in `docker-compose.yml` for `maxmemory 2gb` + `allkeys-lru`. RDB / AOF are disabled in compose for the pilot but the production systemd unit enables both.

### 2.3 Embedding cache — replay, no separate backup

`document_chunks.embedding` is regenerated from `document_chunks.content` when NULL. Embedding API cost ($/chunk) is acceptable in DR; do NOT back up the vector blob separately — Postgres dump already preserves the source `content` rows.

Post-restore re-embed (background, no downtime):

```bash
.venv/bin/python -c "
import asyncio
from ragbot.scripts.reembed_null import reembed_null_chunks
asyncio.run(reembed_null_chunks(batch_size=100))
"
```

### 2.4 Secrets — out-of-band

`.env` (provider keys, DSN, `RAGBOT_SECRET_KEY`) is **never** in `pg_dump` — that would break domain-neutral rule. Secret store is the canonical home:

- Vault / 1Password / sealed-secrets — whichever the deployment uses.
- Recovery path: re-provision host → fetch `.env` from secret store → `set -a && source .env && set +a`.

### 2.5 Backup matrix

| Surface | Backed-up by | Restore path | RPO |
|---|---|---|---|
| `system_config`, `pipeline_config` | pg_dump (Layer A) + WAL (Layer B) | restore DB | 5 min |
| `bot_model_bindings`, `ai_models`, `ai_providers` | pg_dump + WAL | restore DB | 5 min |
| `bots` row (`system_prompt`, `oos_answer_template`, `custom_vocabulary`, `pipeline_config`) | pg_dump + WAL | restore DB | 5 min |
| `documents`, `document_chunks` (text) | pg_dump + WAL | restore DB | 5 min |
| `document_chunks.embedding` (vector) | NOT backed up | re-embed from `content` post-restore | best-effort |
| `semantic_cache`, `exact_hash_cache` | NOT backed up | rebuild on cache miss | 0 (rebuild-able) |
| Redis (bot registry, embed cache, streams pending) | RDB + AOF + replay | restart redis; auto-rebuild | 0 (rebuild-able) |
| `audit_log` | pg_dump + WAL | restore DB | 5 min |
| `.env` (secrets) | out-of-band secret store | re-deploy with secrets | manual |
| Alembic seed scripts | git | `git pull` post-restore | manual |
| `scripts/`, `src/` (code) | git + CI artifacts | `git checkout <tag>` | manual |

---

## 3. Restoration Procedures by Failure Mode

Detailed CLI for each procedure lives in [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md). This section maps **failure mode → which procedure to invoke**.

### 3.1 Full DB loss (host died, disk corruption)

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §2.1.

Sequence:

1. Provision new host or restore on existing.
2. `pg_restore` latest daily dump.
3. `alembic upgrade head` to catch up any post-dump schema changes.
4. Re-seed config that lives outside dump only if `system_config` was reset (rare).
5. Start API + worker, smoke `/health/models?skip_smoke=false`.
6. Re-embed NULL chunks in background.

RTO budget: 20–30 min for a database < 50 GB.

### 3.2 Bad migration / accidental DELETE / DROP

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §2.2 (PITR).

Sequence:

1. Stop API + worker (writes must cease).
2. Capture target time (last known good — usually 1 minute before the bad command).
3. Restore base backup, set `recovery_target_time`, replay WAL until target.
4. Promote, restart Postgres.
5. `alembic current` — may now be behind head; decide whether to re-apply migrations forward.
6. Restart app, smoke.

RTO budget: 30–60 min depending on WAL volume to replay.

### 3.3 Redis loss

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §2.3.

Sequence:

1. Stop redis.
2. Restore latest RDB snapshot (or accept empty state).
3. Start redis, restart app.
4. Accept transient latency spike during cache warm-up (~minutes).

RTO budget: 5 min.

### 3.4 Embedding vector loss (column NULL)

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §2.4.

Sequence:

1. Confirm `content` is intact (`SELECT COUNT(*) FROM document_chunks WHERE content IS NULL` should be 0).
2. Run re-embed script in background — does NOT require app downtime.
3. Watch `/metrics` for embedding API throughput; throttle batch size if provider 429.

RTO budget: 0 min downtime. Background work hours-to-days depending on chunk count.

### 3.5 Bot binding loss

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §2.5.

Re-run idempotent seed: `scripts/db/seed_jina_v3_binding.py`. Uses `ON CONFLICT DO NOTHING`, safe to re-run.

### 3.6 Provider key compromise (security incident)

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §4 (Key Rotation).

This is **not data loss** but counts as DR because it forces a config rotation under time pressure:

1. Generate new key with provider.
2. Test new key with curl before swapping.
3. Swap in `.env`, restart workers.
4. If `RAGBOT_SECRET_KEY` rotated → invalidates all JWTs → all users re-login. Plan a grace-period rolling restart (deploy code that accepts both keys for 24h) unless the exposure is critical.

### 3.7 Cross-tenant data leak

→ [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md) §3.

This is the most serious failure mode. Treat as SEV-1 even if blast radius is one record.

Sequence:

1. **Containment first**: bust all `ragbot:bot:*` cache entries to force DB re-resolve via the 4-key safe path.
2. Identify the offending bot row(s): `psql -c "SELECT * FROM bots WHERE record_tenant_id IS NULL OR workspace_id IS NULL;"` — expect 0.
3. Audit `semantic_cache` for rows where `record_bot_id` doesn't match the requesting tenant.
4. Drop suspect cache rows.
5. Restart workers to flush in-process caches.
6. Run integration test `tests/integration/test_4key_cross_tenant_isolation.py` (or equivalent for current codebase).
7. Open SEV-1 incident channel; CSM begins customer comms within 30 min (see §6).

### 3.8 HALLU fabricate regression

This is also SEV-1 even without data loss.

Sequence:

1. Confirm event: `journalctl -u ragbot-worker | jq 'select(.event=="hallu.fabricate_detected")'` — non-empty.
2. Capture incident pack: 30 min of worker JSONL + the offending bot's `system_prompt` + last 50 audit_log entries.
3. Decide blast radius: one bot vs all bots.
4. **Per-bot containment**: bot_owner updates `bots.system_prompt` (single source of truth — application MUST NOT inject text). Bust bot cache.
5. **Platform containment**: if a code-level cause, roll back to last good git SHA (no data restore needed — code-only).
6. Re-run golden eval (≥ 100 Qs) on the affected bot → 0 fabricate before all-clear.

---

## 4. Failover Scenarios

### 4.1 Provider failover (LLM / embedding / reranker)

**Automatic**, no human intervention required if config is correct.

Mechanism: circuit breaker per provider. CB OPEN → strategy registry picks next-priority provider (or `NullReranker` for rerank step). Defined in `infrastructure/<thing>/registry.py` per the Strategy + DI pattern in CLAUDE.md.

Verification post-failover:

```bash
journalctl -u ragbot-worker --since "5 min ago" -o cat \
  | jq -r 'select(.event=="strategy.fallback_applied") | "\(.from)->\(.to)"'
```

Manual override if CB is stuck OPEN despite provider being healthy:

```bash
psql "$DATABASE_URL" -c "UPDATE system_config SET value='closed' WHERE key='circuit_breaker_force_state_<provider>';"
redis-cli DEL ragbot:system_config:circuit_breaker_force_state_<provider>
```

### 4.2 Worker-pool failover (one host down)

Workers are stateless consumers of Redis Streams. With ≥ 2 worker hosts:

- Surviving workers auto-claim pending messages (idle > 60s) from the dead consumer.
- New work distributes round-robin to surviving consumers.
- No data loss — XACK only after node completion.

Single-host pilot deployments do not have this property. Game-day drill goal for pilots: **make this work** before declaring GA.

### 4.3 Read-replica failover (Postgres)

NOT in scope for MVP. Single-region with off-region backups. Multi-region active-active deferred until traffic justifies cost.

### 4.4 Region failover

NOT in scope for MVP. RTO 30 min assumes single-region recovery from same-region bucket.

---

## 5. Recovery Verification Gate

Before declaring "incident resolved":

- [ ] `curl -s http://localhost:3004/health` returns 200 + `alembic_head_match: true`
- [ ] `curl -s http://localhost:3004/health/models?skip_smoke=false` — all providers `ok`
- [ ] `python scripts/preflight_check.py` exits 0
- [ ] `python scripts/test_bot_smoke.py` — full 1-turn chat passes against a canary bot
- [ ] `pytest tests/integration/test_4key_cross_tenant_isolation.py -x -q` passes (or equivalent — confirm test exists in current branch)
- [ ] HALLU fabricate counter still 0 over 10 min of post-recovery traffic
- [ ] `journalctl -u ragbot-worker --since "5 min ago" -o cat | jq 'select(.level=="error")'` — empty
- [ ] Per-tenant cost replay shows no abnormal token spike

If any gate fails → roll back further; do NOT mark resolved.

---

## 6. Communication Templates

### 6.1 Internal — incident declared

**Channel**: `#ragbot-incidents` (Slack) + PagerDuty incident
**Sender**: on-call primary
**Timing**: within 5 min of detection

```
SEV-{1|2|3} — {one-line summary}

Detected: {YYYY-MM-DD HH:MM Z}
Symptom: {what is broken, user-facing}
Suspected cause: {hypothesis, mark as PRELIMINARY}
Impact: {tenants affected, % of traffic, sacred-invariant status (HALLU=0 / tenant-isolation)}
On-call primary: @{name}
On-call secondary: @{name}
Incident channel: #incident-{YYYY-MM-DD}-{short-slug}
Runbook: docs/ops/RUNBOOK.md §5
DR plan: docs/ops/DISASTER_RECOVERY.md §3
Updates: every 15 min until contained
```

### 6.2 Internal — status update (every 15 min during SEV-1/2)

```
[UPDATE +{minutes}m] SEV-{n} {summary}

Status: INVESTIGATING | CONTAINING | RESTORING | VERIFYING | RESOLVED
What we know: {confirmed facts}
What we are doing: {current action + ETA}
What we need: {help / decisions / approvals}
Next update: {timestamp}
```

### 6.3 Internal — resolution

```
[RESOLVED] SEV-{n} {summary}

Resolved: {YYYY-MM-DD HH:MM Z}
Duration: {minutes}
Total impact: {tenants × minutes, requests failed, sacred-invariant breaches}
Root cause (preliminary): {best current understanding}
Action items:
  - {immediate fix / hotfix / config flip}
  - {follow-up — post-mortem ticket #}
Post-mortem owner: @{name}
Post-mortem due: {YYYY-MM-DD}
```

### 6.4 External — customer-facing (CSM-owned)

CSM sends this; engineering provides facts only.

**SEV-1 initial notice** (within 30 min of confirmed customer impact):

```
Subject: Ragbot service notice — {YYYY-MM-DD}

We are currently investigating an issue affecting {scope: all tenants /
specific tenants / specific bots}. Symptoms include {what users see}.

Our engineering team is actively working on a resolution. We will send the
next update within 30 minutes.

Status page: {url}
Incident reference: {INC-id}
```

**SEV-1 update**:

```
Subject: [Update] Ragbot service notice — {INC-id}

Status: {investigating | mitigation in progress | restored, monitoring}

What we know:
- {confirmed fact}
- {confirmed fact}

What we are doing:
- {action}

Expected next milestone: {ETA}
Next update: within {minutes} minutes
```

**SEV-1 resolution**:

```
Subject: [Resolved] Ragbot service notice — {INC-id}

Service is fully restored as of {YYYY-MM-DD HH:MM TZ}.

Summary: {one-paragraph plain-language summary}
Duration: {start → end, total minutes}
Scope: {tenants / bots / % of traffic affected}

Next steps:
- A full post-mortem will be published within 5 business days.
- Affected customers will receive a dedicated note from their CSM.

We apologize for the disruption.
```

**HALLU / data-quality incident** (special template — never minimize):

```
Subject: Ragbot data-quality incident — {INC-id}

On {date}, we detected an output-quality regression in {scope}. During the
affected window ({start → end}), some responses may have included
{characterization: e.g., information not present in your knowledge base}.

What we did:
- Detected at {time} via {detection mechanism}.
- Contained at {time} by {action}.
- Verified the fix using our golden test suite at {time}.

What you can do:
- Review responses returned to your users during the affected window.
- Contact us with any specific cases needing review.

A full post-mortem and our remediation plan will be sent by {date}.
```

---

## 7. Post-Mortem Process

Every SEV-1 and SEV-2 incident gets a post-mortem within 5 business days. Use `docs/dev/POSTMORTEM_TEMPLATE.md` (create if absent — minimal sections: Summary, Timeline, Root Cause, Resolution, Action Items, Lessons).

Rules:

- **Blameless** — focus on systems and decisions, not individuals.
- **Action items have owners and due dates** — track to closure.
- **Sacred-invariant breaches** (HALLU > 0, cross-tenant leak, data loss > RPO) require a written architectural review, not just a fix.

---

## 8. Game-Day Drill Cadence

| Drill | Cadence | Goal |
|---|---|---|
| Jina 429 storm chaos | quarterly | verify CB OPEN + `NullReranker` fallback; HALLU stays 0 |
| Full DB restore on staging from yesterday's dump | monthly | verify backup integrity; measure actual restore time |
| PITR restore to arbitrary minute on staging | quarterly | verify WAL chain unbroken |
| Redis cold-start (start from empty) | quarterly | verify cache warm-up doesn't violate latency SLO permanently |
| Worker-host kill (with peer survival) | quarterly | verify XCLAIM auto-recovery; no DLQ growth |
| Full DR exercise (DB + Redis + new VM + secrets) | annually | measure end-to-end RTO; certify the plan |
| Cross-tenant leak simulation (write a deliberately bad bot row, verify defence) | annually | confirm 4-key constraint + cache eviction defence both fire |

Each drill writes a one-page report in `reports/drill-<YYYY-MM-DD>-<name>.md`. Failures discovered in drills become P0 backlog items.

---

## 9. Out-of-Scope / Known Limitations

- Multi-region active-active: NOT in scope for MVP. Single-region with off-region backups only.
- Tenant-level self-serve restore: NOT in scope. Ops-team-only operation.
- Vector index rebuild after `CREATE INDEX CONCURRENTLY` is single-threaded — bots with > 1M chunks need an overnight window.
- `.env` rotation requires a coordinated deploy; not a 30-min RTO operation.
- Prompt-cache (provider-side) is opaque to us; we cannot pre-warm or transfer it across hosts.

---

## 10. Owner & Review Cadence

| Document section | Owner | Review cadence |
|---|---|---|
| §1 SLO / RTO / RPO targets | Platform lead + CTO | quarterly |
| §2 Backup strategy | SRE lead | quarterly (verify bucket lifecycle + retention) |
| §3 Restoration procedures | SRE lead | semi-annually (synced with `../DR_RUNBOOK.md` CLI) |
| §4 Failover | SRE lead | quarterly |
| §6 Comm templates | CSM lead | annually |
| §8 Drill cadence | SRE lead + Platform lead | per drill cycle |

Last review: 2026-05-12 (D6 GA hardening).

---

## 11. References

- Operational runbook: [`RUNBOOK.md`](./RUNBOOK.md)
- Legacy DR operator card: [`../DR_RUNBOOK.md`](../DR_RUNBOOK.md)
- Architecture: `RAGBOT_MASTER.md`, `docs/master/`
- Identity rule (4-key): `CLAUDE.md` IDENTITY RULE
- Backup config: `docker-compose.yml` (postgres + redis volumes)
- Preflight: `scripts/preflight_check.py`
- Health: `src/ragbot/interfaces/http/routes/health.py`, `health_models.py`
- Chaos / isolation tests: `tests/integration/test_chaos_resilience.py`, cross-tenant isolation tests under `tests/integration/`
- Cost / model-mix replay: `scripts/cost_audit.py`
