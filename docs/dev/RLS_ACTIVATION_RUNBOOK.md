# RLS Activation Runbook (mega-sprint-G1)

> Operations manual to flip the runtime DSN from the superuser admin
> role to the unprivileged `ragbot_app` role so the 20 RLS policies on
> `documents` / `document_chunks` / `conversations` actually enforce
> tenant isolation. Coder-B4 has shipped the code-level routing
> (mega-sprint-G1-pgvector + G7); this runbook covers the runtime steps
> the Auditor / ops handler must execute manually on the live host.

---

## Background

- `pg_roles` shows the configured admin role has `rolbypassrls=t`. With
  this role active every RLS policy evaluates as if always-true; the 20
  policies are deployed but bypassed.
- `ragbot_app` already exists with `rolbypassrls=f` but its DSN is not
  bound. `infrastructure/db/engine.create_engine_app()` accepts an
  optional `RAGBOT_ALLOW_SUPERUSER_RUNTIME` escape that lets the runtime
  fall back to the admin DSN when `DATABASE_URL_APP` is unset; today it
  is exercised in production as the sole reason RLS is not in force.

## Why this runbook is code-deferred

DB password rotation + .env edits are sensitive ops actions. The Coder
agent that landed mega-sprint-G1 is sandboxed (no live DB access, no
.env write authority) and the orchestration policy explicitly bans hot
config edits from coder commits. This runbook hands the steps to the
Auditor or platform on-call.

---

## Pre-flight checklist

- [ ] Backup verified: `pg_dump --schema-only` of the current DB stored
      offsite. RLS misconfig = read-blackout; rollback path needs the
      schema dump regardless.
- [ ] Latest mega-sprint-B4 commits merged into `main` and the runtime
      build deployed (so the read-path threading through
      `session_with_tenant` is live before the role flip).
- [ ] All Coder-B / Coder-C / Coder-D test suites green on the deployed
      build.
- [ ] Plan a maintenance window — every read after the flip must carry
      `record_tenant_id`; any in-flight worker that omits it will see
      zero rows until restarted.

## Steps

### 1. Generate a fresh password (do NOT commit)

```bash
# On the DB host, generate a random password OOB and hold it in the
# operator's password manager. Do not paste into shell history.
```

### 2. Rotate the `ragbot_app` role password

Run from the Postgres admin shell on the DB host (substitute the env
vars on the operator host so the literal never lands in shell history):

```bash
PGPASSWORD="$DB_ADMIN_PASS" psql \
  -h "$DB_HOST" -U "$DB_ADMIN_USER" -d "$DB_NAME" \
  -c "ALTER ROLE ragbot_app WITH PASSWORD '$NEW_RAGBOT_APP_PASS';"
```

### 3. Sanity check the role state

```bash
PGPASSWORD="$DB_ADMIN_PASS" psql \
  -h "$DB_HOST" -U "$DB_ADMIN_USER" -d "$DB_NAME" \
  -c "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = 'ragbot_app';"
# Expected: rolname=ragbot_app, rolbypassrls=f
```

### 4. Update `.env` on the API + worker hosts

```bash
# Append the runtime DSN. .env is .gitignore'd; never commit.
echo "DATABASE_URL_APP=postgresql+asyncpg://ragbot_app:$NEW_RAGBOT_APP_PASS@$DB_HOST:5432/$DB_NAME" \
  >> /etc/ragbot/.env

# Disable the superuser fallback escape so a stale runtime cannot
# silently bypass RLS again.
sed -i \
  's/^RAGBOT_ALLOW_SUPERUSER_RUNTIME=.*/# RAGBOT_ALLOW_SUPERUSER_RUNTIME disabled mega-sprint-G1/' \
  /etc/ragbot/.env
```

### 5. Restart the runtime

```bash
systemctl restart ragbot-api
systemctl restart ragbot-document-worker
sleep 5
systemctl status ragbot-api --no-pager | head -12
```

### 6. Smoke verify RLS active

```bash
# Pull a service token, then run a known-good chat. Expect chunks_used >= 1.
TOKEN=$(curl -s "$BASE_URL/api/ragbot/test/tokens/self" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")

curl -s -X POST "$BASE_URL/api/ragbot/test/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$REQUEST_JSON" \
  -o /tmp/smoke.json -w "HTTP=%{http_code} time=%{time_total}s\n"

python3 -c "
import json
d = json.load(open('/tmp/smoke.json'))
print('chunks_used:', d.get('chunks_used'))
print('answer (200 char):', (d.get('answer') or '')[:200])
"
# Expected: chunks_used >= 1 (RLS allows access to the bot's own chunks).
```

### 7. Cross-tenant negative check

```bash
# Mint a service token for tenant A; query a bot owned by tenant B.
# Expected: zero chunks (RLS denies cross-tenant SELECT).
```

---

## Rollback

If smoke fails (chunks_used = 0 or 5xx burst):

```bash
# Re-enable the superuser fallback so the runtime opens the admin DSN
# again — restores read traffic immediately while we diagnose.
sed -i \
  's/^# RAGBOT_ALLOW_SUPERUSER_RUNTIME.*/RAGBOT_ALLOW_SUPERUSER_RUNTIME=1/' \
  /etc/ragbot/.env
systemctl restart ragbot-api ragbot-document-worker
```

After rollback, file a regression bug pinned to the live state and
re-open mega-sprint-G1 — the code path must thread `record_tenant_id`
through every read site (Coder-B4 audit catalogue lists the 8 sites
patched in `query_graph.py`; verify no new omission has crept in).

---

## Post-flip cleanup

- [ ] Remove `RAGBOT_ALLOW_SUPERUSER_RUNTIME` from `.env` entirely
      (commented placeholder is fine for future emergencies; raw `=1`
      is not).
- [ ] Update `STATE_SNAPSHOT.md` with the new runtime DSN role and the
      flip date.
- [ ] Audit `~/.bash_history` on the DB host — scrub the `ALTER ROLE`
      line so the password literal never lingers.

---

## Status

- mega-sprint-G1-pgvector — **CODE LANDED** (this branch)
- mega-sprint-G7 — **CODE LANDED** (this branch)
- mega-sprint-G1-rls (runtime activation) — **DEFERRED to ops**
  (this runbook). Coder-B4 cannot rotate DB credentials.
