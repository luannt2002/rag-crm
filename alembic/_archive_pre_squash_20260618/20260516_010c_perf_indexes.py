"""[T2-CostPerf] add 5 critical performance indexes (Agent F P1)

Revision ID: 010c
Revises: 010b
Create Date: 2026-05-16

Per Agent F PostgreSQL deep audit (score 7.2/10) — 5 P1 indexes that the
planner needs for hot-path queries. Skipped 3 from the original 8-index
proposal:
  - ``ix_bot_model_bindings_bot_purpose_active`` — already exists as
    ``ix_binding_bot_purpose(record_bot_id, purpose, active)``
  - ``ix_request_steps_request_step`` — covered by existing
    ``ix_reqstep_request_order`` + ``ix_reqstep_step_name``
  - ``ix_audit_log_tenant_event_time`` — covered by existing
    ``ix_audit_log_tenant_time(record_tenant_id, resource_type, created_at)``

Five new indexes (all idempotent via IF NOT EXISTS):

1. ``ix_documents_tenant_deleted`` — admin audit list active docs per tenant
2. ``ix_outbox_pending_retry`` — outbox poll WHERE status IN ('pending','retry')
   (partial index: ~10x smaller than full index given most rows are 'processed')
3. ``ix_documents_bot_source`` — re-ingest dedup check WHERE bot=X AND url=Y
   (partial index: only non-soft-deleted rows)
4. ``ix_conversations_bot_user_created`` — conversation history lookup
   (record_tenant_id + record_bot_id + connect_id + created_at DESC)
5. ``ix_semantic_cache_versions`` — cache invalidation by version tuple

These are read-only DDL (CREATE INDEX). Writes to indexed tables incur
~0.5-2% per index but queries gain 5-50x on targeted filter+sort patterns.

NOTE: ``CREATE INDEX CONCURRENTLY`` cannot run inside alembic's transaction
wrapper. Tests use ``CREATE INDEX IF NOT EXISTS`` (in-tx, brief lock).
Production rollout can re-issue these statements with CONCURRENTLY in a
post-deploy script for zero-downtime if write traffic high.
"""
from __future__ import annotations

from alembic import op


revision = "010c"
down_revision = "010b"
branch_labels = None
depends_on = None


_INDEXES_CREATE: tuple[tuple[str, str], ...] = (
    (
        "ix_documents_tenant_deleted",
        "CREATE INDEX IF NOT EXISTS ix_documents_tenant_deleted "
        "ON documents (record_tenant_id, deleted_at DESC)",
    ),
    (
        "ix_outbox_pending_retry",
        "CREATE INDEX IF NOT EXISTS ix_outbox_pending_retry "
        "ON outbox (status, created_at) "
        "WHERE status IN ('pending', 'retry')",
    ),
    (
        "ix_documents_bot_source",
        "CREATE INDEX IF NOT EXISTS ix_documents_bot_source "
        "ON documents (record_bot_id, source_url) "
        "WHERE deleted_at IS NULL",
    ),
    (
        "ix_conversations_bot_user_created",
        "CREATE INDEX IF NOT EXISTS ix_conversations_bot_user_created "
        "ON conversations (record_tenant_id, record_bot_id, connect_id, created_at DESC)",
    ),
    (
        "ix_semantic_cache_versions",
        "CREATE INDEX IF NOT EXISTS ix_semantic_cache_versions "
        "ON semantic_cache (record_bot_id, bot_version, corpus_version)",
    ),
)

_INDEX_DROP_SQL = "DROP INDEX IF EXISTS {name}"


def upgrade() -> None:
    for _name, sql in _INDEXES_CREATE:
        op.execute(sql)


def downgrade() -> None:
    # Drop in reverse order (no FK dependency between indexes,
    # but symmetric with upgrade for readability).
    for name, _sql in reversed(_INDEXES_CREATE):
        op.execute(_INDEX_DROP_SQL.format(name=name))
