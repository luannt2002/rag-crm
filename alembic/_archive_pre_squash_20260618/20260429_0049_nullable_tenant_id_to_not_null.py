"""Phase 2 Y1: record_tenant_id NOT NULL enforcement on secondary tables.

P0-BUG-3 (audit 2026-04-29): 14 tables have record_tenant_id as nullable=True.
The application layer guards against NULL via _ensure_tenant() but the DB
itself does not enforce it. A bug bypassing the application layer could
persist NULL tenant rows → silent cross-tenant data leakage.

SAFE approach:
1. Check NULL count per table before altering.
2. If NULL count == 0 → ALTER COLUMN SET NOT NULL (safe, no data loss).
3. If NULL count > 0 → skip with WARNING (manual backfill required first).

This migration is IDEMPOTENT: re-running is safe because the constraint is
advisory and already existing NOT NULL is a no-op in Postgres.

Tables targeted:
  Core path (conversations, messages, documents, jobs, outbox):
    - conversations, messages — every chat write sets record_tenant_id from
      BotRegistryService lookup; NULL should not exist in production.
    - documents — ingest path sets record_tenant_id from bot lookup.
    - jobs — queued via ingest, always has tenant context.
    - outbox — event publish always has tenant context.

  AI config (bot_model_bindings, prompt_templates, audit_log):
    - bot_model_bindings — created by admin with known tenant.
    - prompt_templates — tenant-scoped prompt store.
    - audit_log — policy changes always have actor + tenant.

  Monitoring (request_logs, request_steps, tenant_model_policy,
              guardrail_events, prompt_versions, model_invocations):
    - These are observability tables. record_tenant_id is nullable on
      invocation because NOT every upstream identifies tenant (see models_invocation.py
      docstring). For the monitoring tables we log a WARNING but do NOT
      alter — they have a legitimate reason for NULL (decoupled audit).

Revision: 0049
Down revision: 0048
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

logger = logging.getLogger("alembic.runtime.migration")

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

# Tables to attempt NOT NULL migration (application always sets these).
# These are the core operational tables where NULL = application bug.
_CORE_TABLES = [
    "conversations",
    "messages",
    "documents",
    "jobs",
    "outbox",
    "bot_model_bindings",
    "prompt_templates",
    "audit_log",
    "request_logs",
    "guardrail_events",
]

# Monitoring / audit tables where NULL is intentional by design (decoupled audit
# path — upstream may not provide tenant context for raw LLM invocation logs).
# These are logged as INFO but NOT altered in this migration.
_AUDIT_TABLES_SKIP = [
    "request_steps",
    "tenant_model_policy",
    "prompt_versions",
    "model_invocations",
]


def upgrade() -> None:
    conn = op.get_bind()

    for table in _CORE_TABLES:
        # Verify the column exists before touching it
        col_exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :tbl "
            "AND column_name = 'record_tenant_id'"
        ), {"tbl": table}).scalar()

        if not col_exists:
            logger.warning(
                "0049: table %s has no record_tenant_id column — skipping",
                table,
            )
            continue

        null_count = conn.execute(text(
            f"SELECT COUNT(*) FROM public.{table} WHERE record_tenant_id IS NULL"  # noqa: S608
        )).scalar()

        if null_count == 0:
            # Safe to add NOT NULL constraint
            op.alter_column(
                table,
                "record_tenant_id",
                nullable=False,
                schema="public",
            )
            logger.info(
                "0049: %s.record_tenant_id → NOT NULL (0 null rows)",
                table,
            )
        else:
            logger.warning(
                "0049: SKIPPED %s.record_tenant_id — %d row(s) have NULL. "
                "Backfill required before this column can be set NOT NULL. "
                "Run: UPDATE %s SET record_tenant_id = <correct_uuid> WHERE record_tenant_id IS NULL",
                table,
                null_count,
                table,
            )

    for table in _AUDIT_TABLES_SKIP:
        logger.info(
            "0049: %s.record_tenant_id — intentionally nullable (decoupled audit), skipping",
            table,
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Revert all core tables back to nullable=True
    for table in _CORE_TABLES:
        col_exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :tbl "
            "AND column_name = 'record_tenant_id'"
        ), {"tbl": table}).scalar()

        if not col_exists:
            continue

        op.alter_column(
            table,
            "record_tenant_id",
            nullable=True,
            schema="public",
        )
        logger.info("0049 downgrade: %s.record_tenant_id → nullable=True", table)
