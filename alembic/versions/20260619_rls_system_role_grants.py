"""Provision the BYPASSRLS system role for trusted cross-tenant workers.

Companion to ``rls_app_role_grants_20260619`` (which provisions the
NOBYPASSRLS *request* role ``ragbot_app``). The RLS + background-jobs split
(plan ``plans/260619-rls-enforcement``) runs the four cross-tenant system
workers — outbox publisher, document recovery scan, semantic-cache GC,
cost-cap aggregate — on a SECOND connection that legitimately sees every
tenant's rows. Those scans have no single tenant context, so under the
NOBYPASSRLS request role they would fail-closed (zero rows): outbox stuck,
recovery blind, GC a no-op, alerter blind.

``ragbot_system`` is the least-privilege answer (operator decision 2026-06-19,
"dedicated role" over "reuse superuser"):

  * **BYPASSRLS** — the four workers see all tenants without per-tenant looping
    (preserves the outbox single-drain exactly-once model).
  * **NOSUPERUSER** + DML-only grants — it CANNOT alter schema, DROP tables, or
    otherwise escape its lane. A compromised worker connection is bounded to
    row read/write on existing tables.

NOT activated here (rule #0 — default OFF): the application keeps using its
current engines until ops sets ``DATABASE_URL_SYSTEM`` to this role. Until then
``create_engine_system`` falls back to the admin DSN (superuser ``ragbot``),
which also bypasses RLS, so worker behaviour is byte-for-byte unchanged.

SECURITY — no secret in git: LOGIN capability only; the password is set
out-of-band by ops on the ``DATABASE_URL_SYSTEM`` credential. A role with LOGIN
but no password cannot authenticate.

Idempotent + reversible (downgrade revokes + NOLOGIN; role not dropped).

Revision ID: rls_system_role_grants_20260619
Revises: rls_app_role_grants_20260619
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "rls_system_role_grants_20260619"
down_revision = "rls_app_role_grants_20260619"
branch_labels = None
depends_on = None

# MUST match ``infrastructure/db/session.py::SYSTEM_DB_ROLE`` (added in the
# Phase 2 code split). Pinned by ``test_rls_system_role_provisioned``.
_SYSTEM_ROLE = "ragbot_system"


def upgrade() -> None:
    # Role exists with LOGIN + BYPASSRLS + NOSUPERUSER (no password — ops sets it).
    op.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_SYSTEM_ROLE}') THEN
                    ALTER ROLE {_SYSTEM_ROLE} LOGIN NOSUPERUSER BYPASSRLS;
                ELSE
                    CREATE ROLE {_SYSTEM_ROLE} LOGIN NOSUPERUSER BYPASSRLS;
                END IF;
            END
            $$;
            """
        )
    )

    # DML grants on current objects (no DDL — least privilege).
    op.execute(text(f"GRANT USAGE ON SCHEMA public TO {_SYSTEM_ROLE}"))
    op.execute(
        text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public TO {_SYSTEM_ROLE}"
        )
    )
    op.execute(
        text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_SYSTEM_ROLE}")
    )

    # Future objects auto-grant so a later migration's table is not invisible
    # to the system workers.
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_SYSTEM_ROLE}"
        )
    )
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {_SYSTEM_ROLE}"
        )
    )


def downgrade() -> None:
    """Revoke grants + remove LOGIN (restore inert state). Role not dropped."""
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_SYSTEM_ROLE}"
        )
    )
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE USAGE, SELECT ON SEQUENCES FROM {_SYSTEM_ROLE}"
        )
    )
    op.execute(
        text(f"REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM {_SYSTEM_ROLE}")
    )
    op.execute(
        text(
            f"REVOKE SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public FROM {_SYSTEM_ROLE}"
        )
    )
    op.execute(text(f"REVOKE USAGE ON SCHEMA public FROM {_SYSTEM_ROLE}"))
    op.execute(text(f"ALTER ROLE {_SYSTEM_ROLE} NOLOGIN"))
