"""Re-assert every tenant RLS policy filters on the GUC (no literal skips).

RLS enforce (Stream S1). With the runtime now connecting as the
NOBYPASSRLS ``ragbot_app`` role (alembic ``0186``), the policy bodies must
be guaranteed to compare ``record_tenant_id`` against
``current_setting('app.tenant_id', true)::uuid`` — NOT a hard-coded
literal and NOT a body that silently skips the setting (which under a
NOBYPASSRLS role would either leak across tenants or return zero rows).

Policies were installed by ``0069`` (tenant-only) and upgraded by ``0141``
(workspace-aware). This migration is the enforce-gate re-assertion: it
DROPs + re-CREATEs the canonical policy on each table so a drifted
environment (manual ``CREATE POLICY`` edit, partially-applied history) is
brought back to the GUC-driven definition. The bodies here are identical
to the ``0141`` workspace-aware shape for tables that carry
``workspace_id`` and the ``0069`` tenant-only shape for the rest — so a
fully-migrated DB is a no-op re-assert.

Idempotent (``DROP POLICY IF EXISTS`` before each ``CREATE``). Reversible:
downgrade re-installs the same canonical bodies (this migration never
weakens a policy, so down == up for the policy text).

Revision ID: 0187
Revises: 0186
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op

revision = "0187"
down_revision = "0186"
branch_labels = None
depends_on = None


_POLICY_NAME = "tenant_isolation"
_TENANT_GUC = "app.tenant_id"
_WORKSPACE_GUC = "app.workspace_id"


# Tables carrying BOTH record_tenant_id AND workspace_id — workspace-aware
# policy (mirrors alembic 0141 _TENANT_WORKSPACE_TABLES).
_TENANT_WORKSPACE_TABLES: tuple[str, ...] = (
    "audit_log",
    "bot_model_bindings",
    "bots",
    "conversations",
    "documents",
    "guardrail_events",
    "jobs",
    "messages",
    "model_invocations",
    "outbox",
    "prompt_templates",
    "prompt_versions",
    "quotas",
    "request_logs",
    "request_steps",
    "semantic_cache",
    "tenant_model_policy",
)

# Direct-tenant tables WITHOUT a workspace_id column — tenant-only policy
# (the 0069 set minus the workspace-aware ones above).
_TENANT_ONLY_TABLES: tuple[str, ...] = (
    "refuse_suggestions",
)

# Child tables that inherit tenancy via a FK to a parent — JOIN policy.
# (child, parent, child_fk, parent_pk) — mirrors alembic 0069.
_JOIN_TENANT_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("document_chunks", "documents", "record_document_id", "id"),
    ("knowledge_edges", "bots", "record_bot_id", "id"),
)


def _workspace_aware_policy(table: str) -> str:
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


def _tenant_only_policy(table: str) -> str:
    return f"""
        CREATE POLICY {_POLICY_NAME} ON {table}
        FOR ALL
        USING (
            record_tenant_id = current_setting('{_TENANT_GUC}', true)::uuid
        )
        WITH CHECK (
            record_tenant_id = current_setting('{_TENANT_GUC}', true)::uuid
        )
    """


def _join_policy(child: str, parent: str, child_fk: str, parent_pk: str) -> str:
    return f"""
        CREATE POLICY {_POLICY_NAME} ON {child}
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM {parent} p
                WHERE p.{parent_pk} = {child}.{child_fk}
                  AND p.record_tenant_id = current_setting('{_TENANT_GUC}', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM {parent} p
                WHERE p.{parent_pk} = {child}.{child_fk}
                  AND p.record_tenant_id = current_setting('{_TENANT_GUC}', true)::uuid
            )
        )
    """


def _reassert_all() -> None:
    for table in _TENANT_WORKSPACE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}")
        op.execute(_workspace_aware_policy(table))

    for table in _TENANT_ONLY_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}")
        op.execute(_tenant_only_policy(table))

    for child, parent, child_fk, parent_pk in _JOIN_TENANT_TABLES:
        op.execute(f"ALTER TABLE {child} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {child} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {child}")
        op.execute(_join_policy(child, parent, child_fk, parent_pk))


def upgrade() -> None:
    # Re-assert the GUC-driven policy bodies so no table is left comparing a
    # literal or skipping the setting under the now-NOBYPASSRLS role.
    _reassert_all()


def downgrade() -> None:
    # This migration only re-asserts the canonical GUC-driven bodies; it
    # never weakens a policy. Re-applying the same bodies on downgrade keeps
    # the DB at the alembic 0141/0069 canonical state (the prior migrations
    # own the structural enable/disable). down == up for the policy text.
    _reassert_all()
