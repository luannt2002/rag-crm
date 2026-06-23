"""Bring document_service_index RLS to parity with the other 20 tenant tables.

document_service_index was the only one of the 21 tenant-scoped tables that
(a) was ENABLEd for RLS but never FORCEd — so the owning role still bypasses
the policy — and (b) carried a tenant_isolation policy whose predicate read the
GUC as ``current_setting('app.tenant_id')`` WITHOUT the ``, true`` missing-ok
flag, so an unbound GUC throws "unset parameter" instead of denying the row.
The sibling tables all FORCE and all use the missing-ok form plus the workspace
dimension. This migration recreates the policy in the canonical shape and adds
FORCE, closing the only divergent RLS boundary. DDL-only, tracked in git
(CLAUDE.md: no psql hot-fix for schema/RLS state).

The DDL strings spell out ``public.document_service_index`` and the canonical
predicate literally (not via variable interpolation) so the schema text stays
grep-auditable — the F-5 regression test introspects exactly this.

Revision ID: dsi_force_rls_parity_20260624
Revises: enable_vision_gpt41_20260621
"""

from __future__ import annotations

from alembic import op

revision = "dsi_force_rls_parity_20260624"
down_revision = "enable_vision_gpt41_20260621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON public.document_service_index"
    )
    # Canonical predicate shared by the other 20 tenant tables: tenant match via
    # the missing-ok GUC read, AND workspace match when a workspace GUC is bound
    # (empty GUC ⇒ tenant-wide visibility). Identical text in USING + WITH CHECK.
    op.execute(
        "CREATE POLICY tenant_isolation ON public.document_service_index "
        "USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) "
        "AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) "
        "OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) "
        "WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) "
        "AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) "
        "OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))))"
    )
    op.execute(
        "ALTER TABLE public.document_service_index FORCE ROW LEVEL SECURITY"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.document_service_index NO FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON public.document_service_index"
    )
    # Restore the prior tenant-only predicate (no missing-ok flag).
    op.execute(
        "CREATE POLICY tenant_isolation ON public.document_service_index "
        "USING ((record_tenant_id = (current_setting('app.tenant_id'::text))::uuid)) "
        "WITH CHECK ((record_tenant_id = (current_setting('app.tenant_id'::text))::uuid))"
    )
