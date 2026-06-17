"""Create message_feedback table — thumbs up/down loop scaffolding.

The application persists per-message verdicts (thumbs_up / thumbs_down +
optional free-text comment) into a dedicated analytics table. The
parallel ``request_logs.feedback_*`` columns remain for the synchronous
write-path attached to a single request; ``message_feedback`` holds the
durable signal record keyed by the upstream message id (BIGINT) so a
future training loop can join on it without scanning the request log.

Tenancy is enforced two ways:

* Direct ``record_tenant_id`` column with the same RLS policy installed
  in alembic 0069 — read and write paths obey
  ``current_setting('app.tenant_id', true)::uuid``.
* Composite analytics index on ``(record_tenant_id, record_bot_id,
  created_at DESC)`` so the per-bot aggregate query is index-only.

The ``message_id`` column is BIGINT and nullable — it carries the
external upstream identifier (per the project naming rule, no
``record_`` prefix on external ints). When the signal originates locally
(no upstream platform message) the column is NULL and callers join via
``record_conversation_id`` instead.

Dependency note: this migration's ``down_revision`` points at ``0073``
which lands as part of the sibling TASK-1 worktree. Admin running
``alembic upgrade head`` must merge TASK-1 first (or rebase this file's
``down_revision`` to the actual head if TASK-1 lands later). The
coder-side branch only ships the file — no DB DDL is executed here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


_TABLE = "message_feedback"
_VERDICT_ENUM = "message_feedback_verdict"
_POLICY_NAME = "tenant_isolation"
_VERDICT_THUMBS_UP = "thumbs_up"
_VERDICT_THUMBS_DOWN = "thumbs_down"


def upgrade() -> None:
    verdict_enum = sa.Enum(
        _VERDICT_THUMBS_UP,
        _VERDICT_THUMBS_DOWN,
        name=_VERDICT_ENUM,
    )
    verdict_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_tenant_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "record_bot_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("bots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # External upstream message id — BIGINT, no ``record_`` prefix
        # because the value is supplied by the caller (not an internal PK).
        # Nullable: a thumbs verdict raised against a locally-generated
        # message has no upstream id; the row keys via record_conversation_id.
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("record_conversation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("connect_id", sa.String(length=255), nullable=True),
        sa.Column(
            "verdict",
            verdict_enum,
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_message_feedback_tenant_bot_created",
        _TABLE,
        ["record_tenant_id", "record_bot_id", sa.text("created_at DESC")],
    )

    # RLS — mirror alembic 0069 pattern. Direct tenant column, FORCE on so
    # even table owners obey the policy; permissive ``true`` second arg so
    # admin-shell sessions without the GUC see an empty result rather than
    # raising.
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {_TABLE}")
    op.execute(
        f"""
        CREATE POLICY {_POLICY_NAME} ON {_TABLE}
        FOR ALL
        USING (
            record_tenant_id = current_setting('app.tenant_id', true)::uuid
        )
        WITH CHECK (
            record_tenant_id = current_setting('app.tenant_id', true)::uuid
        )
        """,
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_message_feedback_tenant_bot_created", table_name=_TABLE)
    op.drop_table(_TABLE)
    sa.Enum(name=_VERDICT_ENUM).drop(op.get_bind(), checkfirst=True)
