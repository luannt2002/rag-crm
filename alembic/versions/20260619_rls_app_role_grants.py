"""Provision the NOBYPASSRLS runtime role (layer 2 of the RLS stack).

Row-level security needs three layers ALL live to actually enforce
(see ``infrastructure/db/session.py``):

  1. ``ENABLE/FORCE ROW LEVEL SECURITY`` + ``CREATE POLICY`` on the GUC
     ``app.tenant_id`` — present (20 tables / 21 policies on the live DB).
  2. A **non-superuser, NOBYPASSRLS login role** the app connects as. A
     superuser / ``rolbypassrls`` connection ignores every policy, so until
     the app connects as this role the policies are inert.
  3. Per-transaction ``SET LOCAL app.tenant_id`` — wired generically by the
     ADR-W1-D3 ``after_begin`` hook (no-op under a superuser DSN).

Layer 2 gap (discovered 2026-06-19): the ``ragbot_app`` role pre-exists on
the live DB (created by a pre-squash migration) but the ``20260618`` squash
baseline carried forward NONE of its GRANT / LOGIN DDL — ``\\du`` shows
``rolcanlogin=f`` and ``information_schema.role_table_grants`` returns zero
rows for it. A fresh clone from the squash baseline would not even have the
role. This migration re-asserts the provisioning idempotently so the role is
reproducible and ready for the eventual DSN cut-over.

NOT activated here (rule #0 — default OFF): this only PROVISIONS the role.
The application keeps connecting as the superuser ``DATABASE_URL_APP`` until
ops points that DSN at ``ragbot_app`` in a governed, load-test-gated window.
Granting privileges to a role nothing connects as is inert.

SECURITY — no secret in git: the login PASSWORD is deliberately NOT set here
(``ALTER ROLE ... LOGIN`` grants the capability only). Ops sets the password
out-of-band on the ``DATABASE_URL_APP`` credential — mirrors the contract
documented at ``session.py`` (``Login/credential handled by ops``). A role
with LOGIN but no password cannot authenticate, so this is safe to ship.

Least privilege: SELECT/INSERT/UPDATE/DELETE only — no DDL, no superuser, no
BYPASSRLS, no TRUNCATE. The role can read/write tenant rows (RLS-filtered)
but cannot alter schema or escape the policies.

Idempotent (DO-block role guard + GRANT is repeatable) so re-running is a
no-op.

Revision ID: rls_app_role_grants_20260619
Revises: phase4_costwin_20260619
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "rls_app_role_grants_20260619"
down_revision = "phase4_costwin_20260619"
branch_labels = None
depends_on = None

# The runtime role name — MUST match ``session.py::RUNTIME_DB_ROLE``. Kept as
# a literal here (alembic files are the one place DDL identifiers live) but
# the contract is pinned by ``test_rls_app_role_provisioned`` against that
# constant so a rename cannot silently diverge.
_APP_ROLE = "ragbot_app"


def upgrade() -> None:
    # 1. Role exists with LOGIN capability (no password — ops sets it).
    #    NOSUPERUSER + NOBYPASSRLS are the CREATE ROLE defaults; an ALTER on a
    #    pre-existing role leaves any prior NOSUPERUSER/NOBYPASSRLS intact.
    op.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                    ALTER ROLE {_APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS;
                ELSE
                    CREATE ROLE {_APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS;
                END IF;
            END
            $$;
            """
        )
    )

    # 2. Schema + DML grants on all CURRENT objects (least privilege: no DDL).
    op.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
    op.execute(
        text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"
        )
    )
    op.execute(
        text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}")
    )

    # 3. FUTURE objects created by the table owner auto-grant to the app role
    #    so a new table from a later migration does not silently become
    #    invisible (zero-grant → permission denied) to the runtime role.
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}"
        )
    )
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE}"
        )
    )


def downgrade() -> None:
    """Revoke grants + remove LOGIN (restore the pre-provision inert state).

    The role is NOT dropped — it pre-existed this migration and other DBs may
    reference it; we only undo what ``upgrade`` asserted.
    """
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_APP_ROLE}"
        )
    )
    op.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE USAGE, SELECT ON SEQUENCES FROM {_APP_ROLE}"
        )
    )
    op.execute(
        text(f"REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE}")
    )
    op.execute(
        text(
            f"REVOKE SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public FROM {_APP_ROLE}"
        )
    )
    op.execute(text(f"REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE}"))
    op.execute(text(f"ALTER ROLE {_APP_ROLE} NOLOGIN"))
