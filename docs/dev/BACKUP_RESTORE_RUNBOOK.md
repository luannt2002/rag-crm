# Backup / Restore Runbook

> Production runbook for Postgres + Redis disaster recovery on the ragbot
> platform. T4 (ops sustainability). Doc-only — code already exists at
> `scripts/backup_db.sh`.

## Overview

**In scope** (this runbook covers):

| Subsystem | Mechanism | Recovery point |
|---|---|---|
| Postgres (relational + pgvector) | logical dump via `pg_dump -Fc` | last nightly dump |
| Redis (caches, semantic cache, registry) | `BGSAVE` snapshot of `dump.rdb` | last on-demand snapshot |

**NOT in scope** (operator must address separately):

* Object storage (uploaded source files, parsed artefacts) — replicated at
  the storage layer, not by this runbook.
* Application logs / structured event sinks — handled by the log shipper.
* Off-host shipping of dumps to cold storage — operator policy; this
  runbook only writes to the local backup directory.

---

## Backup commands

### Postgres

The DSN comes from the `DATABASE_URL` environment variable. Inline
credentials are forbidden (CLAUDE.md domain-neutral / secret-literal
rule). The shipped helper does this correctly:

```bash
# Source the platform .env so DATABASE_URL is exported
set -a && source /opt/ragbot/.env && set +a

# One-shot
bash scripts/backup_db.sh

# Precondition check only (cron-friendly: validates dir + reachability)
bash scripts/backup_db.sh --check-only
```

The script writes to `${RAGBOT_BACKUP_DIR:-/var/backups/ragbot}` with an
ISO-8601 UTC timestamp filename (`ragbot_YYYYMMDDTHHMMSSZ.dump`), uses
`pg_dump -Fc` (custom format — restorable with `pg_restore`), and rotates
dumps older than `${RAGBOT_BACKUP_RETAIN:-7}` days.

### Redis

```bash
# Trigger a non-blocking snapshot (returns immediately; the fork writes RDB in background)
redis-cli -h <server-host> -p 6379 BGSAVE

# Wait for the background save to finish, then copy the file off
redis-cli -h <server-host> -p 6379 LASTSAVE   # remember this timestamp

# Once LASTSAVE has advanced past the BGSAVE call:
cp /var/lib/redis/dump.rdb <dump-path>/redis_$(date -u +%Y%m%dT%H%M%SZ).rdb
chmod 600 <dump-path>/redis_*.rdb
```

Notes:

* `BGSAVE` is the only safe option in production — `SAVE` blocks the
  Redis main loop.
* If Redis AUTH is enabled, the operator passes credentials via a
  `redis-cli` config file or the `REDISCLI_AUTH` env var, never inline.
* RDB on its own is acceptable for ragbot because Redis only holds
  caches + ephemeral registry rows — the source of truth is Postgres.

---

## Schedule recommendation

| Cadence | Action | Owner |
|---|---|---|
| Nightly (02:00 UTC) | `bash scripts/backup_db.sh` via cron | platform ops |
| Nightly | `redis-cli BGSAVE` + copy `dump.rdb` off | platform ops |
| Weekly | restore the most recent dump into a scratch DB and run `alembic check` to prove it loads | platform ops |
| Monthly | full restore drill end-to-end (steps below) on a non-production host | platform ops |

The cron line that ships in `scripts/backup_db.sh` header comment is the
recommended template.

---

## Restore drill (5 steps, in order)

> Run these in order on the target host. Do not skip a step.

### 1. Stop the API

```bash
systemctl stop ragbot-api
```

The API must be down before the DB is replaced. Active connections to
the old DB will be terminated; in-flight requests will surface as 5xx
to clients (this is expected during a restore drill).

### 2. `pg_restore` into a fresh database

```bash
# Create the target DB (drop-and-recreate is operator's call; pg_restore --clean works on an existing DB)
psql -d postgres -c "CREATE DATABASE ragbot_restore;"

# Restore from the most recent custom-format dump
pg_restore --no-owner --no-acl \
  --dbname=ragbot_restore \
  --jobs=4 \
  <dump-path>/ragbot_<TS>.dump
```

`--no-owner` and `--no-acl` match the dump-side flags so the restore
does not require role-replay. `--jobs=4` parallelises table loads;
operator may tune.

After restore, point `DATABASE_URL` (or `DATABASE_URL_APP`) at
`ragbot_restore` for the smoke run, or rename the DB into place once
verified.

### 3. Replace Redis RDB and restart

```bash
systemctl stop redis
cp <dump-path>/redis_<TS>.rdb /var/lib/redis/dump.rdb
chown redis:redis /var/lib/redis/dump.rdb
chmod 660 /var/lib/redis/dump.rdb
systemctl start redis

# Sanity: confirm Redis loaded the snapshot
redis-cli -h <server-host> -p 6379 INFO persistence | grep -E "^(rdb_last_load_keys|loading)"
```

If `loading:1` persists, wait — Redis is still reading the RDB.

### 4. `alembic check` — expect no drift

```bash
set -a && source /opt/ragbot/.env && set +a
.venv/bin/alembic check
```

`alembic check` exits non-zero if the live schema diverges from the
declared model state. A clean exit proves the dump captured the schema
revision the application expects — this is the gate before bringing the
API back up.

If drift is reported: do not start the API. Investigate whether the
dump was taken before or after a partially-applied migration; the
operator may need an older dump or a forward `alembic upgrade head`
on the restored DB.

### 5. Smoke test the restored deployment

```bash
systemctl start ragbot-api

# Health: provider probes match runtime call signature (no drift)
curl -fsS http://<server-host>:8000/health/models | jq '.summary'

# Smoke: send a known prompt to a known bot (4-key identity required)
curl -fsS -X POST http://<server-host>:8000/api/ragbot/test/chat \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"bot_id": "<known-slug>", "channel_type": "web", "workspace_id": "<known-slug>", "message": "<known-prompt>"}' \
  | jq '{status: .status, has_answer: (.answer | length > 0)}'
```

**Acceptance for smoke step**:

* `/health/models` returns 200 and reports each configured provider as
  reachable.
* The chat endpoint returns 200, a non-empty `answer`, and a non-empty
  `citations` array (when the prompt is a knowledge query for which the
  bot has documents).

**What the smoke step MUST NOT assert** (Quality Gate #10):

> Do not assert on the LLM answer text. The bot owner's `system_prompt`
> + bot config is the single source of truth for what the model says.
> The platform's job is to confirm the pipeline is wired (HTTP 200,
> non-empty `answer`, citations populated). Asserting on specific
> wording — refusal phrasing, hedging, exact numbers — couples the
> drill to bot-owner content and will cause false alarms whenever the
> owner edits their prompt.

If the smoke check fails: roll back by repointing `DATABASE_URL` at
the prior DB and restarting the API while you investigate.

---

## Verify-dump-loadable cadence

Weekly, on a non-production host or in a scratch DB:

```bash
LATEST="$(ls -1t <dump-path>/ragbot_*.dump | head -n1)"

psql -d postgres -c "DROP DATABASE IF EXISTS ragbot_verify;"
psql -d postgres -c "CREATE DATABASE ragbot_verify;"

pg_restore --no-owner --no-acl --dbname=ragbot_verify --jobs=4 "${LATEST}"

# Confirm the restored DB matches the application's expected revision
DATABASE_URL="postgresql+asyncpg://<user>@<server-host>/ragbot_verify" \
  .venv/bin/alembic check

# Drop after verify — verification DB is ephemeral
psql -d postgres -c "DROP DATABASE ragbot_verify;"
```

Failure to load (`pg_restore` errors, `alembic check` drift) is the
single highest-signal indicator that the backup pipeline is broken.
Treat any failure as P1 and pause production writes until the next
clean dump verifies.

---

## Audit of `scripts/backup_db.sh`

The shipped helper at `scripts/backup_db.sh` was reviewed against four
required attributes:

| Attribute | Status | Evidence |
|---|---|---|
| Idempotent (re-running is safe) | PASS | `mkdir -p`, atomic write to `.tmp` then `mv`, `find ... -mtime ... -delete` rotation — re-running on the same minute either creates a fresh dump or returns cleanly via `--check-only` |
| No inline DSN | PASS | DSN sourced from `DATABASE_URL` env; `pg_isready` and `pg_dump` both consume the env DSN; fail-loud (`exit 2`) if unset |
| Exit non-zero on failure | PASS | `set -euo pipefail` plus explicit `exit 2..5` for each named failure mode (env missing, `pg_dump` not on PATH, host unreachable, dump failed) |
| ISO date in filename | PASS | `TS="$(date -u +%Y%m%dT%H%M%SZ)"` — ISO 8601 UTC compact form, monotonic-sortable |

**Verdict**: no enhancement required. The script is production-ready
as shipped; this runbook documents the operational envelope around it.

---

## Open questions

None at time of writing. If the operator wants encrypted-at-rest dumps
(e.g. piping `pg_dump` through `age` or `gpg`), that is a separate
plan — out of scope for this runbook.
