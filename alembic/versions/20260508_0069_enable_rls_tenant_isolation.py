"""Enable Row-Level Security policies on tenant-scoped tables.

The application already calls ``SET LOCAL app.tenant_id = '<uuid>'`` per
session via ``session_with_tenant`` (``infrastructure/db/engine.py``). That
``GUC`` is dead code unless the database also (a) has ``ROW LEVEL SECURITY``
enabled on each tenant table and (b) has a ``CREATE POLICY`` clause that
filters rows by the GUC. Until that happens any raw-SQL path that forgets
to add ``record_tenant_id = :tid`` to its ``WHERE`` is a cross-tenant leak.

This migration closes that gap defence-in-depth:

* For tables with a direct ``record_tenant_id`` column we install a
  per-row ``USING`` clause that compares the column to
  ``current_setting('app.tenant_id', true)::uuid``. The ``true`` second
  argument means the call returns NULL when the GUC is unset rather than
  raising — admin-shell sessions that haven't bound a tenant simply see
  an empty result instead of a 500.
* For child tables that have NO direct tenant column (``document_chunks``,
  ``knowledge_edges``) we install a JOIN-based policy via the parent table.
  Chunks inherit tenancy from their owning ``documents`` row;
  ``knowledge_edges`` inherits via ``bots``.

Read and write paths share the same policy (``FOR ALL``). RLS is opt-in
per role, so superuser / ``BYPASSRLS`` connections still see everything —
that matches the existing alembic / migration-runner setup.

Revision ID: 0069
Revises: 0068
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op


revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


# Tables with a direct ``record_tenant_id`` column — simple equality policy.
# Order is irrelevant for correctness; kept alphabetical for review hygiene.
_DIRECT_TENANT_TABLES: tuple[str, ...] = (
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
    "refuse_suggestions",
    "request_logs",
    "request_steps",
    "semantic_cache",
    "tenant_model_policy",
)


# Tables without a direct tenant column — inherit via FK to a parent that has one.
# Each entry: (child_table, parent_table, child_fk_column, parent_pk_column).
_JOIN_TENANT_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("document_chunks", "documents", "record_document_id", "id"),
    ("knowledge_edges", "bots", "record_bot_id", "id"),
)


_POLICY_NAME = "tenant_isolation"


def _enable_rls(table: str) -> None:
    """Force RLS on so even table owners obey the policy."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _disable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    # --- Direct-column tables ----------------------------------------------
    for table in _DIRECT_TENANT_TABLES:
        _enable_rls(table)
        # ``current_setting('app.tenant_id', true)`` returns NULL when the
        # GUC is unset (rather than raising). Casting NULL to ``uuid`` is
        # safe and the equality with a NOT-NULL column then yields NULL,
        # which the row-filter treats as "row excluded" — fail-closed.
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {_POLICY_NAME} ON {table}
            FOR ALL
            USING (
                record_tenant_id = current_setting('app.tenant_id', true)::uuid
            )
            WITH CHECK (
                record_tenant_id = current_setting('app.tenant_id', true)::uuid
            )
            """
        )

    # --- JOIN-based child tables -------------------------------------------
    for child, parent, child_fk, parent_pk in _JOIN_TENANT_TABLES:
        _enable_rls(child)
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {child}")
        # ``EXISTS`` is the standard pattern; the planner can use the FK
        # index on the child + the PK index on the parent so the lookup is
        # cheap even on hot paths. The parent table is NOT itself bypassed —
        # it has its own RLS so the EXISTS subquery is also tenant-filtered,
        # which means a row with a parent in a different tenant is invisible
        # even before this child policy runs.
        op.execute(
            f"""
            CREATE POLICY {_POLICY_NAME} ON {child}
            FOR ALL
            USING (
                EXISTS (
                    SELECT 1 FROM {parent} p
                    WHERE p.{parent_pk} = {child}.{child_fk}
                      AND p.record_tenant_id = current_setting('app.tenant_id', true)::uuid
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM {parent} p
                    WHERE p.{parent_pk} = {child}.{child_fk}
                      AND p.record_tenant_id = current_setting('app.tenant_id', true)::uuid
                )
            )
            """
        )


def downgrade() -> None:
    """Drop the policies + disable RLS on every table touched above."""
    for child, _parent, _fk, _pk in _JOIN_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {child}")
        _disable_rls(child)

    for table in _DIRECT_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}")
        _disable_rls(table)
