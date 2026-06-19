"""Extend RLS policies with workspace_id filter (defense-in-depth).

Revision: 0141
Prev:     0140

Trigger (2026-05-29 master consolidated fix-all plan, Phase 4.1):
  Audit found 0/23 RLS policies enforce workspace_id; all 23 only filter
  by record_tenant_id. The 4-key bot identity rule (CLAUDE.md MINDSET +
  IDENTITY RULE) requires
  ``(record_tenant_id, workspace_id, bot_id, channel_type)`` for cross-
  layer isolation, but the DB enforces only the first key. This means:

  * If app code forgets to add ``workspace_id = :ws`` to a raw-SQL WHERE
    (or relies solely on ``record_bot_id`` lookup for filtering), two
    workspaces under the same tenant can see each other's rows.
  * Admin / forensic queries that bind ``app.tenant_id`` but no
    workspace cannot distinguish the two — RLS is workspace-blind.

This migration adds workspace scoping AS A SECOND USING clause that
combines with the existing tenant filter. The GUC ``app.workspace_id``
follows the same fail-closed pattern as ``app.tenant_id``:

  - When the GUC is UNSET (admin shell, ingest worker, batch job), the
    workspace filter degrades to pass-through so tenant-scoped admin
    queries still return all rows for the tenant. (No regression vs.
    the pre-0141 behaviour.)
  - When the GUC is SET to a workspace slug, only rows with that exact
    workspace_id (or rows where workspace_id IS NULL — legacy / system
    forensic rows) survive. The application binds the GUC alongside
    ``app.tenant_id`` in ``session_with_tenant_workspace`` (added in a
    follow-up patch).

Tables touched: every table from alembic 0069 ``_DIRECT_TENANT_TABLES``
that also carries a ``workspace_id`` column (17 of 19 in 0069 — verified
by information_schema scan). ``message_feedback`` (alembic 0074) and
``refuse_suggestions`` are NOT touched because they carry only
``record_tenant_id`` / ``record_bot_id`` without workspace scoping;
their tenant-only policy from 0069/0074 stays. The two JOIN-based child
policies (``document_chunks``, ``knowledge_edges``) inherit workspace
filtering via their parents so they require no edit here.

Sacred-rule alignment:
  ✅ Pure DDL via alembic (CLAUDE.md rule 7)
  ✅ Defense-in-depth — app-layer ``workspace_id = :ws`` WHEREs remain
     authoritative; RLS is the backstop.
  ✅ Backward-compat — unsetting the GUC restores pre-0141 semantics.
  ✅ Reversible — downgrade reinstalls the tenant-only policy.
"""

from __future__ import annotations

from alembic import op

revision: str = "0141"
down_revision: str | None = "0140"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_POLICY_NAME = "tenant_isolation"


# Tables with BOTH record_tenant_id AND workspace_id columns. Verified via
# information_schema.columns scan 2026-05-29.
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


def upgrade() -> None:
    """Replace tenant-only policy with workspace-aware policy."""
    for table in _TENANT_WORKSPACE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}")
        # Workspace clause:
        #   current_setting('app.workspace_id', true) returns:
        #     - NULL when GUC unset → degrade to tenant-only (admin shell,
        #       ingest worker that didn't set workspace).
        #     - '' (empty string) when explicitly cleared → same degrade.
        #     - non-empty slug when SET via session_with_tenant_workspace
        #       → filter rows where workspace_id matches.
        # The CASE expression encodes the "unset means no-op" rule.
        op.execute(
            f"""
            CREATE POLICY {_POLICY_NAME} ON {table}
            FOR ALL
            USING (
                record_tenant_id = current_setting('app.tenant_id', true)::uuid
                AND (
                    coalesce(current_setting('app.workspace_id', true), '') = ''
                    OR workspace_id = current_setting('app.workspace_id', true)
                )
            )
            WITH CHECK (
                record_tenant_id = current_setting('app.tenant_id', true)::uuid
                AND (
                    coalesce(current_setting('app.workspace_id', true), '') = ''
                    OR workspace_id = current_setting('app.workspace_id', true)
                )
            )
            """,
        )


def downgrade() -> None:
    """Restore tenant-only policy (rollback to alembic 0069 semantics)."""
    for table in _TENANT_WORKSPACE_TABLES:
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
            """,
        )
