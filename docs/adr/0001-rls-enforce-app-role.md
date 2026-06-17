# ADR: Enforce Row-Level Security by connecting as a non-superuser app role

Status: Accepted
Date: 2026-06-08
Stream: S1 (RLS enforce — security)

## Context

Tenant isolation is meant to be defence-in-depth across three layers:

1. **Policies** — `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY` plus a
   `CREATE POLICY ... USING (record_tenant_id = current_setting('app.tenant_id', true)::uuid)`
   on 23 tenant-scoped tables. Installed by alembic `0069`, made
   workspace-aware by `0141`.
2. **Connection role** — PostgreSQL skips *every* RLS policy for a
   superuser or any role with `rolbypassrls`, even when the table is
   `FORCE ROW LEVEL SECURITY`.
3. **Per-transaction binding** — each transaction must
   `SET LOCAL app.tenant_id = '<uuid>'` so the policy has a value to
   compare.

The application historically connects to Postgres as the **superuser**
(the admin DSN, `DATABASE_URL`). Consequence: **all 23 policies are dead**
— layer 1 exists on paper but `rolbypassrls` makes it a no-op. Tenant
isolation has rested entirely on the application-layer repository `WHERE
record_tenant_id = :tid` filters. Any raw-SQL path that forgets that
predicate is a silent cross-tenant leak.

Layer 3 was also only partially wired: `session_with_tenant` (in
`infrastructure/db/engine.py`) binds the GUC at *explicit* call sites, but
repositories that open the plain `session_factory` session never issued
`SET LOCAL` — so even after the role switch those paths would see zero
rows.

## Decision

Enforce RLS by closing layers 2 and 3:

* **Layer 2 (role)** — provision/assert a dedicated runtime role
  `ragbot_app` with `NOSUPERUSER NOBYPASSRLS` and DML-only grants
  (`SELECT/INSERT/UPDATE/DELETE`, no DDL). Migration `0186`
  (idempotent re-assert of the role first created in `0073`). The runtime
  connects via `DATABASE_URL_APP`; migrations keep using the admin
  `DATABASE_URL`.
* **Policy re-assert** — migration `0187` DROP+CREATEs every tenant policy
  with the canonical GUC-driven body so a drifted environment cannot leave
  a table comparing a literal or skipping the setting once the
  NOBYPASSRLS role is live.
* **Layer 3 (per-tx binding)** — a SQLAlchemy `after_begin` listener
  (`infrastructure/db/session.py::attach_rls_session_hook`) reads the
  request-scoped `tenant_id_ctx` contextvar (populated by
  `TenantContextMiddleware` → `bind_request_context`) and issues
  `SET LOCAL app.tenant_id = '<uuid>'` inside *every* transaction. It is a
  no-op when no tenant is bound (ops / migration / background jobs), so the
  same engine code is safe on a superuser connection.

### Why the GUC is `app.tenant_id` (not `app.current_tenant_id`)

The 23 existing policies already read `current_setting('app.tenant_id',
true)`. The binding hook MUST use the **same** key — binding a different
GUC name would leave every policy comparing against an unset setting
(NULL), which under a NOBYPASSRLS role returns zero rows everywhere. We
therefore standardise on the established `app.tenant_id` GUC and define it
as a module-level `Final` constant (`TENANT_SETTING_KEY`) next to the hook.

### Default OFF (rule #0 — no unmeasured default change)

`attach_rls_session_hook` is **opt-in**: the coordinator attaches it only
when the runtime DSN points at the NOBYPASSRLS role. Until then behaviour
is byte-for-byte unchanged. Attaching it early on a superuser connection is
harmless (policies bypassed regardless; the SET LOCAL just sets a GUC
nothing enforces).

## Why this is hard to reverse / surprising / a real trade-off

* **Hard to reverse** — switching the runtime to a NOBYPASSRLS role is an
  operational state change (DSN swap + role/grants in the DB). Rolling it
  back means reverting `DATABASE_URL_APP` to the admin DSN (or exporting
  `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`) AND not attaching the hook.
* **Surprising without context** — a developer who later adds a raw-SQL
  read path and forgets `SET LOCAL` will, under the enforced role, get
  *zero rows* instead of all rows. That fail-closed surprise is the whole
  point, but it must be documented or it reads like a bug.
* **Trade-off** — fail-closed (zero rows on a missing bind) trades a
  potential availability blip for the elimination of cross-tenant leaks.
  Given HALLU/isolation is sacred, closed > open.

## Rollback

1. Point the runtime back at the admin DSN: set
   `DATABASE_URL_APP=<admin DSN>` (or unset it and export
   `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, which falls back to `DATABASE_URL`
   with a structured warning).
2. Do not call `attach_rls_session_hook` (or call
   `detach_rls_session_hook`).
3. Optionally `alembic downgrade` `0187`/`0186` (downgrade revokes grants
   and drops the role; the policy bodies stay at the `0069/0141` canonical
   state).

The application-layer `record_tenant_id` repository filters remain
authoritative in all cases — RLS is the backstop, not the only guard.

## Consequences

* Raw-SQL paths that skip the tenant predicate now fail closed instead of
  leaking — but a path that forgets to bind the contextvar will see no
  rows. The hook's no-op-when-unset behaviour keeps ops/migration sessions
  working.
* Future tables inherit DML grants via `ALTER DEFAULT PRIVILEGES`, so the
  runtime role does not need re-granting after each migration.
