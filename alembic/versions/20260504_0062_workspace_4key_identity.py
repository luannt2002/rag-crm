"""Workspace 4-key bot identity.

Adds ``workspace_id VARCHAR(64) NOT NULL`` on ``bots`` plus 16 data tables
so the canonical identity becomes the 4-tuple
``(record_tenant_id, workspace_id, bot_id, channel_type)``. The slug is a
pass-through value supplied by tenants — same tenant can isolate teams
(sales / marketing / prod-ws-2024) without provisioning new tenants.

Backfill strategy:
- ``bots.workspace_id`` ← ``str(record_tenant_id)`` so legacy 3-key rows
  land on a deterministic per-tenant slug (matches the runtime fallback in
  ``shared.workspace_id_validator.resolve_workspace_id``).
- FK-chain tables (documents / conversations / semantic_cache /
  request_logs / model_invocations / guardrail_events /
  bot_model_bindings) inherit the slug from ``bots.workspace_id`` via
  ``record_bot_id`` join.
- ``messages`` inherit from ``conversations.workspace_id``.
- ``request_steps`` inherit from ``request_logs.workspace_id`` via
  ``record_request_id`` (the actual FK column name).
- Tenant-level / forensic tables (audit_log / outbox / jobs / quotas /
  prompt_templates / prompt_versions / tenant_model_policy) are
  backfilled with the literal ``'system'`` since they are not 1:1 with a
  single bot row. The literal matches the slug regex.

Schema changes:
- DROP ``uq_bots_record_tenant_bot_channel`` (3-key unique).
- ADD ``uq_bots_record_tenant_workspace_bot_channel`` (4-key unique).
- ADD CHECK ``length 1..64 AND ~ '^[a-zA-Z0-9-]+$'`` per column.
- ADD 6 hot-path indexes for 4-key lookup paths.

Revision ID: 0062
Revises: 0061
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = "0061"


# Tables to alter (17 total). ``bots`` is first so FK-chain backfills can
# JOIN against the canonical slug.
_DATA_TABLES = [
    "bots",
    "documents",
    "conversations",
    "messages",
    "semantic_cache",
    "request_logs",
    "request_steps",
    "audit_log",
    "model_invocations",
    "outbox",
    "jobs",
    "quotas",
    "guardrail_events",
    "prompt_templates",
    "prompt_versions",
    "bot_model_bindings",
    "tenant_model_policy",
]

# Tables with a direct ``record_bot_id`` FK to ``bots`` — backfill from
# ``bots.workspace_id`` via JOIN.
_FK_CHAIN_TABLES = [
    "documents",
    "conversations",
    "semantic_cache",
    "request_logs",
    "bot_model_bindings",
]

# Tables that join to ``request_logs`` via ``record_request_id`` — backfill
# from the parent request's ``workspace_id`` (which itself came from the
# bot via the FK chain above).
_REQUEST_CHAIN_TABLES = [
    "model_invocations",
    "guardrail_events",
]

# Tenant-level / forensic / job tables — no 1:1 bot mapping. Filled with
# the literal ``'system'`` slug (regex-valid).
_SYSTEM_SLUG_TABLES = [
    "audit_log",
    "outbox",
    "jobs",
    "quotas",
    "prompt_templates",
    "prompt_versions",
    "tenant_model_policy",
]

_PLACEHOLDER = "__placeholder__"
_SYSTEM_SLUG = "system"


def upgrade() -> None:
    # Step 1: Add workspace_id with placeholder default. NOT NULL is set
    # immediately because the placeholder satisfies the constraint; we
    # remove the default in step 7 once backfill is complete.
    for table in _DATA_TABLES:
        op.execute(
            text(
                f"ALTER TABLE {table} ADD COLUMN workspace_id "
                f"VARCHAR(64) NOT NULL DEFAULT '{_PLACEHOLDER}'"
            )
        )

    # Step 2: Backfill bots.workspace_id = str(record_tenant_id).
    op.execute(
        text(
            "UPDATE bots SET workspace_id = record_tenant_id::text "
            f"WHERE workspace_id = '{_PLACEHOLDER}'"
        )
    )

    # Step 3: Backfill FK-chain tables from bots.workspace_id.
    for table in _FK_CHAIN_TABLES:
        op.execute(
            text(
                f"UPDATE {table} t SET workspace_id = b.workspace_id "
                f"FROM bots b WHERE b.id = t.record_bot_id "
                f"AND t.workspace_id = '{_PLACEHOLDER}'"
            )
        )

    # Step 4: Backfill messages from conversations.workspace_id.
    op.execute(
        text(
            "UPDATE messages m SET workspace_id = c.workspace_id "
            "FROM conversations c WHERE c.id = m.record_conversation_id "
            f"AND m.workspace_id = '{_PLACEHOLDER}'"
        )
    )

    # Step 5: Backfill request_steps from request_logs.workspace_id.
    # Note: request_steps FK column is ``record_request_id``, request_logs
    # PK is ``request_id``.
    op.execute(
        text(
            "UPDATE request_steps rs SET workspace_id = rl.workspace_id "
            "FROM request_logs rl "
            "WHERE rl.request_id = rs.record_request_id "
            f"AND rs.workspace_id = '{_PLACEHOLDER}'"
        )
    )

    # Step 5b: Backfill model_invocations + guardrail_events from
    # request_logs.workspace_id via the soft ``record_request_id`` ref.
    for table in _REQUEST_CHAIN_TABLES:
        op.execute(
            text(
                f"UPDATE {table} t SET workspace_id = rl.workspace_id "
                f"FROM request_logs rl "
                f"WHERE rl.request_id = t.record_request_id "
                f"AND t.workspace_id = '{_PLACEHOLDER}'"
            )
        )

    # Step 6: Tenant-level / forensic tables — literal 'system' slug.
    for table in _SYSTEM_SLUG_TABLES:
        op.execute(
            text(
                f"UPDATE {table} SET workspace_id = '{_SYSTEM_SLUG}' "
                f"WHERE workspace_id = '{_PLACEHOLDER}'"
            )
        )

    # Step 6b: Safety net — any rows still on the placeholder (unexpected
    # FK orphan) get the 'system' slug rather than NOT NULL violation.
    for table in _DATA_TABLES:
        op.execute(
            text(
                f"UPDATE {table} SET workspace_id = '{_SYSTEM_SLUG}' "
                f"WHERE workspace_id = '{_PLACEHOLDER}'"
            )
        )

    # Step 7: Drop placeholder default + add CHECK constraint.
    for table in _DATA_TABLES:
        op.execute(
            text(
                f"ALTER TABLE {table} ALTER COLUMN workspace_id DROP DEFAULT"
            )
        )
        op.execute(
            text(
                f"ALTER TABLE {table} ADD CONSTRAINT "
                f"{table}_workspace_id_format_check "
                f"CHECK (length(workspace_id) >= 1 "
                f"AND length(workspace_id) <= 64 "
                f"AND workspace_id ~ '^[a-zA-Z0-9-]+$')"
            )
        )

    # Step 8: Drop the legacy unique constraint and create the new one.
    op.execute(
        text(
            "ALTER TABLE bots DROP CONSTRAINT "
            "uq_bots_record_tenant_bot_channel"
        )
    )
    op.execute(
        text(
            "ALTER TABLE bots ADD CONSTRAINT "
            "uq_bots_record_tenant_workspace_bot_channel "
            "UNIQUE (record_tenant_id, workspace_id, bot_id, channel_type)"
        )
    )

    # Step 9: Hot-path indexes covering the new lookup column.
    op.execute(
        text(
            "CREATE INDEX ix_bots_4key_lookup "
            "ON bots (record_tenant_id, workspace_id, bot_id, channel_type) "
            "WHERE deleted_at IS NULL"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_documents_ws_tenant ON documents "
            "(record_tenant_id, workspace_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_conversations_ws_tenant ON conversations "
            "(record_tenant_id, workspace_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_messages_ws_tenant ON messages "
            "(record_tenant_id, workspace_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_request_logs_ws_tenant ON request_logs "
            "(record_tenant_id, workspace_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_semantic_cache_ws ON semantic_cache "
            "(record_bot_id, workspace_id)"
        )
    )


def downgrade() -> None:
    # Drop hot-path indexes first so the column drop doesn't trip on
    # dependent objects.
    indexes = [
        "ix_bots_4key_lookup",
        "ix_documents_ws_tenant",
        "ix_conversations_ws_tenant",
        "ix_messages_ws_tenant",
        "ix_request_logs_ws_tenant",
        "ix_semantic_cache_ws",
    ]
    for idx in indexes:
        op.execute(text(f"DROP INDEX IF EXISTS {idx}"))

    # Restore the prior unique constraint on bots.
    op.execute(
        text(
            "ALTER TABLE bots DROP CONSTRAINT IF EXISTS "
            "uq_bots_record_tenant_workspace_bot_channel"
        )
    )
    op.execute(
        text(
            "ALTER TABLE bots ADD CONSTRAINT "
            "uq_bots_record_tenant_bot_channel "
            "UNIQUE (record_tenant_id, bot_id, channel_type)"
        )
    )

    # Drop CHECK constraints + columns (reverse order to mirror upgrade).
    for table in reversed(_DATA_TABLES):
        op.execute(
            text(
                f"ALTER TABLE {table} DROP CONSTRAINT "
                f"IF EXISTS {table}_workspace_id_format_check"
            )
        )
        op.execute(
            text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS workspace_id")
        )
