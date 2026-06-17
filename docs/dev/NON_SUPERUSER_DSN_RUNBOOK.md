# Non-superuser DSN runbook

PostgreSQL superuser and `BYPASSRLS` roles ignore the row-security
policies installed on every tenant table (see
`alembic/versions/20260508_0069_enable_rls_tenant_isolation.py`). The
application must connect through a dedicated non-superuser role —
`ragbot_app` — so the policies actually bind. This runbook is the
admin-side checklist for provisioning that role and pointing the
runtime at it.

## Why two DSNs

| Env var             | Role            | Used by                          |
|---------------------|-----------------|----------------------------------|
| `DATABASE_URL`      | admin / owner   | alembic, ops scripts, `psql`     |
| `DATABASE_URL_APP`  | `ragbot_app`    | runtime engine (`bootstrap.py`)  |

The application-layer repository filter remains the first isolation
layer; non-superuser RLS is the second, defence-in-depth layer. Without
`DATABASE_URL_APP` the second layer is silently bypassed even when
`FORCE ROW LEVEL SECURITY` is set on every tenant table.

## Setup steps

### 1. Choose a strong password and set it as a database GUC

The `ragbot_app` password is read from the GUC `app.ragbot_app_password`
inside the migration. Set it on the target database before running
alembic so the migration can pick it up:

```sql
ALTER DATABASE ragbot
    SET app.ragbot_app_password = 'SOME-STRONG-PASSWORD';
```

The GUC is database-scoped and persists across sessions; you do not
need to re-set it after a connection drop. The same password must
appear in `DATABASE_URL_APP`.

### 2. Run alembic upgrade as the admin user

The migration creates the role idempotently:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:...@host:5432/ragbot \
    alembic upgrade head
```

The migration:

- creates `ragbot_app` (NOSUPERUSER, NOBYPASSRLS, LOGIN) if absent
- grants `SELECT/INSERT/UPDATE/DELETE` on every existing table in
  `public`
- grants `USAGE/SELECT/UPDATE` on every existing sequence in `public`
- installs `ALTER DEFAULT PRIVILEGES` so future tables inherit those
  grants

If `app.ragbot_app_password` is unset, the migration aborts with a
clear `RAISE EXCEPTION` — fix the GUC and rerun.

### 3. Configure the application DSN

Set both env vars before restarting the API server:

```bash
export DATABASE_URL=postgresql+asyncpg://postgres:...@host:5432/ragbot
export DATABASE_URL_APP=postgresql+asyncpg://ragbot_app:SOME-STRONG-PASSWORD@host:5432/ragbot
```

If `DATABASE_URL_APP` is unset and `RAGBOT_ALLOW_SUPERUSER_RUNTIME` is
not exported as `1`, the application **refuses to boot**. The escape env
is intended for local development only and emits a structured WARNING
(`engine.app_dsn_superuser_fallback`) when active.

### 4. Restart the API server

```bash
systemctl restart ragbot-api.service
```

## Verify

Connect as the runtime role and check the user / superuser flag:

```bash
psql "postgresql://ragbot_app:SOME-STRONG-PASSWORD@host:5432/ragbot" \
    -c "SELECT current_user, session_user, current_setting('is_superuser')"
```

Expected output:

```
 current_user | session_user | current_setting
--------------+--------------+-----------------
 ragbot_app   | ragbot_app   | off
```

A read against any tenant table while `app.tenant_id` is unset should
return zero rows (RLS policy excludes everything, fail-closed):

```bash
psql "postgresql://ragbot_app:SOME-STRONG-PASSWORD@host:5432/ragbot" \
    -c "SELECT count(*) FROM bots"
```

Expected: `0`. If the value is non-zero, the role is bypassing RLS —
double-check `pg_roles` for the `NOSUPERUSER` and `NOBYPASSRLS`
attributes on `ragbot_app`.

## Rollback

The migration's `downgrade()` revokes all grants and drops the role.
Before downgrading, ensure no application instance is connected as
`ragbot_app` (drop role fails while sessions are open).
