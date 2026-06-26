"""Re-assert the document_service_index RLS policy with missing_ok GUC reads.

RLS-2 gap (discovered 2026-06-26): the ``tenant_isolation`` policy on
``document_service_index`` reads the tenant GUC WITHOUT the ``missing_ok``
second argument::

    USING  (record_tenant_id = current_setting('app.tenant_id')::uuid)
    CHECK  (record_tenant_id = current_setting('app.tenant_id')::uuid)

``current_setting('app.tenant_id')`` (no second arg) RAISES
``unrecognized configuration parameter`` whenever the GUC is unset — i.e.
every query that runs without a per-transaction ``SET LOCAL app.tenant_id``
(ops shell, migration, an un-bound background path) THROWS instead of
fail-closing to zero rows. Every OTHER policy on the live DB already uses
``current_setting('app.tenant_id', true)`` (the ``missing_ok`` form, which
returns ``NULL`` when unset → the row comparison is ``NULL`` → no rows). This
table was added after the ``0187`` re-assert sweep and never picked up the
``, true`` form.

``document_service_index`` carries BOTH ``record_tenant_id`` and
``workspace_id`` (NOT NULL), so the canonical body is the workspace-aware
shape used by ``0187`` for the other dual-column tables — tenant match AND
(workspace unset → tenant-only, else workspace match). This brings the
policy to the same GUC-driven, missing-ok contract as the rest.

Idempotent: ``DROP POLICY IF EXISTS`` before ``CREATE POLICY``; ENABLE/FORCE
re-asserted. Down re-installs the same canonical body (this migration only
hardens — it never weakens — so down == up for the policy text).

Revision ID: rls_missing_ok_setting_20260626
Revises: revive_grounding_slot_260626
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op

revision = "rls_missing_ok_setting_20260626"
down_revision = "revive_grounding_slot_260626"
branch_labels = None
depends_on = None


# Identifiers (alembic files are the one place DDL identifiers live).
_TABLE = "document_service_index"
_POLICY_NAME = "tenant_isolation"
_TENANT_GUC = "app.tenant_id"
_WORKSPACE_GUC = "app.workspace_id"


def _workspace_aware_policy(table: str) -> str:
    # Mirrors alembic 0187 ``_workspace_aware_policy`` exactly — tenant match
    # AND (workspace GUC unset → tenant-only, else workspace match). Both GUC
    # reads use the missing_ok (``, true``) form so an unset GUC yields NULL
    # (→ zero rows) instead of raising.
    return f"""
        CREATE POLICY {_POLICY_NAME} ON {table}
        FOR ALL
        USING (
            record_tenant_id = current_setting('{_TENANT_GUC}', true)::uuid
            AND (
                coalesce(current_setting('{_WORKSPACE_GUC}', true), '') = ''
                OR workspace_id = current_setting('{_WORKSPACE_GUC}', true)
            )
        )
        WITH CHECK (
            record_tenant_id = current_setting('{_TENANT_GUC}', true)::uuid
            AND (
                coalesce(current_setting('{_WORKSPACE_GUC}', true), '') = ''
                OR workspace_id = current_setting('{_WORKSPACE_GUC}', true)
            )
        )
    """


def _reassert() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {_TABLE}")
    op.execute(_workspace_aware_policy(_TABLE))


def upgrade() -> None:
    _reassert()


def downgrade() -> None:
    # This migration only hardens the GUC read (missing_ok) + aligns the body
    # to the canonical workspace-aware shape; it never weakens the policy.
    # Re-applying the same body on downgrade keeps the table at the canonical
    # state rather than restoring the throwing pre-fix definition.
    _reassert()
