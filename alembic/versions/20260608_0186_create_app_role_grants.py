"""Ensure the ``ragbot_app`` runtime role exists with DML-only grants.

RLS enforce (Stream S1). The application historically connects as the
Postgres superuser (``rolbypassrls``), so the 23 tenant RLS policies from
alembic ``0069`` / ``0141`` are dead — a superuser ignores every policy.
Enforcement requires the runtime to connect as a NON-superuser,
NOBYPASSRLS role.

Role provisioning first landed in alembic ``0073`` (with ``LOGIN`` +
password GUC). This migration is the idempotent, login-handled-by-ops
re-assertion required by the enforce roll-out:

* ``CREATE ROLE ragbot_app NOSUPERUSER NOBYPASSRLS NOLOGIN`` when absent.
  ``NOLOGIN`` here means login/credential is owned by ops (``ALTER ROLE
  ... LOGIN PASSWORD`` out-of-band or via the secret-managed DSN), NOT by
  a password literal in a tracked migration. If the role already exists
  (e.g. created by ``0073`` with ``LOGIN``) we leave its login attribute
  untouched — we only *guarantee* NOSUPERUSER + NOBYPASSRLS so RLS cannot
  be silently bypassed.
* DML grants (``SELECT/INSERT/UPDATE/DELETE``) on existing tables +
  ``USAGE/SELECT/UPDATE`` on sequences. NO DDL grants (the role must not
  be able to ``ALTER``/``DROP`` — migrations run as the admin role).
* ``ALTER DEFAULT PRIVILEGES`` so future tables inherit the same DML set.

Idempotent: re-running after a manual rollback must not error. Reversible
downgrade revokes the grants and drops the role only if it was created
here (guarded ``DROP ROLE IF EXISTS``).

Revision ID: 0186
Revises: 0185
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op

revision = "0186"
down_revision = "0185"
branch_labels = None
depends_on = None


_ROLE_NAME = "ragbot_app"


def upgrade() -> None:
    # Create role if missing, else only assert the security-critical
    # attributes (NOSUPERUSER + NOBYPASSRLS). Login is ops-owned: a fresh
    # role is NOLOGIN here; an existing LOGIN role keeps its login bit.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_ROLE_NAME}') THEN
                CREATE ROLE {_ROLE_NAME} NOSUPERUSER NOBYPASSRLS NOLOGIN;
            ELSE
                ALTER ROLE {_ROLE_NAME} NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        """
    )

    # DML-only grants — no DDL (role cannot ALTER/DROP). ALTER DEFAULT
    # PRIVILEGES covers tables created by future migrations.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_ROLE_NAME}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON ALL TABLES IN SCHEMA public TO {_ROLE_NAME}"
    )
    op.execute(
        f"GRANT USAGE, SELECT, UPDATE "
        f"ON ALL SEQUENCES IN SCHEMA public TO {_ROLE_NAME}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_ROLE_NAME}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {_ROLE_NAME}"
    )


def downgrade() -> None:
    # Revoke (default-privileges first, then existing-object grants) before
    # dropping so dependent grants do not block removal. Guarded so a
    # partial upgrade still rolls back cleanly.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname='{_ROLE_NAME}') THEN
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    REVOKE USAGE, SELECT, UPDATE ON SEQUENCES FROM {_ROLE_NAME};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_ROLE_NAME};
                REVOKE USAGE, SELECT, UPDATE
                    ON ALL SEQUENCES IN SCHEMA public FROM {_ROLE_NAME};
                REVOKE SELECT, INSERT, UPDATE, DELETE
                    ON ALL TABLES IN SCHEMA public FROM {_ROLE_NAME};
                REVOKE USAGE ON SCHEMA public FROM {_ROLE_NAME};
                DROP ROLE IF EXISTS {_ROLE_NAME};
            END IF;
        END $$;
        """
    )
