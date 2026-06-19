"""Create non-superuser ``ragbot_app`` runtime role.

PostgreSQL superuser and ``BYPASSRLS`` roles ignore the row-security
policies installed by ``20260508_0069_enable_rls_tenant_isolation.py``
even though those tables run with ``FORCE ROW LEVEL SECURITY``. Until
the application connects as a non-superuser role, the RLS layer is dead
in practice and tenant isolation rests on the application-layer
repository filter alone.

This migration provisions the dedicated runtime role:

* ``CREATE ROLE ragbot_app NOSUPERUSER NOBYPASSRLS LOGIN``
* Schema and DML grants on every existing table + sequence in
  ``public``
* ``ALTER DEFAULT PRIVILEGES`` so future tables inherit the same DML
  set without a separate manual grant after each migration

The password is read from the GUC ``app.ragbot_app_password`` which the
admin must set with ``SET LOCAL`` before running ``alembic upgrade
head``. ``current_setting(..., true)`` returns NULL when unset so the
``DO`` block can detect the missing-secret case and abort with a clear
message instead of creating a passwordless role.

Revision ID: 0073
Revises: 0072
Create Date: 2026-05-09
"""

from __future__ import annotations

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


_ROLE_NAME = "ragbot_app"
# GUC name (not a credential) — admin sets the secret via SET LOCAL.
_PASSWORD_GUC = "app.ragbot_app_password"  # noqa: S105 — GUC name, not a literal secret


def upgrade() -> None:
    # CREATE ROLE is idempotent — re-running the migration after a manual
    # rollback must not error. We also abort with a clear message if the
    # admin forgot to set the password GUC; otherwise the role would be
    # created without a password and silently refuse logins.
    op.execute(
        f"""
        DO $$
        DECLARE
            pwd text := current_setting('{_PASSWORD_GUC}', true);
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_ROLE_NAME}') THEN
                IF pwd IS NULL OR pwd = '' THEN
                    RAISE EXCEPTION
                        'Set GUC % before alembic upgrade head '
                        '(e.g. ALTER DATABASE ... SET % = ...)',
                        '{_PASSWORD_GUC}', '{_PASSWORD_GUC}';
                END IF;
                EXECUTE format(
                    'CREATE ROLE {_ROLE_NAME} NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD %L',
                    pwd
                );
            END IF;
        END $$;
        """
    )

    # DML grants on existing objects. ``ALTER DEFAULT PRIVILEGES`` covers
    # objects created in future migrations so the runtime role does not
    # need re-granting after every schema bump.
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
    # Revoke before drop so dependent grants do not block removal. Drop
    # is guarded — if the role was never created (e.g. partial upgrade)
    # the migration still rolls back cleanly.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT, UPDATE ON SEQUENCES FROM {_ROLE_NAME}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_ROLE_NAME}"
    )
    op.execute(
        f"REVOKE USAGE, SELECT, UPDATE "
        f"ON ALL SEQUENCES IN SCHEMA public FROM {_ROLE_NAME}"
    )
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE, DELETE "
        f"ON ALL TABLES IN SCHEMA public FROM {_ROLE_NAME}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_ROLE_NAME}")
    op.execute(f"DROP ROLE IF EXISTS {_ROLE_NAME}")
