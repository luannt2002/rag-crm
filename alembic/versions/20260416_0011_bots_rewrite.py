"""0011 — Rewrite `bots` table theo schema user (external bot_id + channel_type).

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-16

Strategy (code mới, 0 data production):
1. DROP TABLE ragbot.bots CASCADE — xoá luôn FK từ documents, conversations,
   messages, bot_model_bindings, etc.
2. Re-create `bots` với schema mới (idempotent: IF NOT EXISTS).
3. Re-add FK cứng từ các bảng con (bọc DO block check existence để re-run
   không crash).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "public"

DEFAULT_SETTING_OPTIONS = (
    '{"frequency_penalty": 0, "max_tokens": 450, '
    '"response_format": "text", "presence_penalty": 0, '
    '"temperature": 0.3, "top_p": 0.4}'
)


def _add_fk_if_absent(
    child_table: str, child_col: str, constraint_name: str
) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = '{constraint_name}'
          ) THEN
            ALTER TABLE {SCHEMA}.{child_table}
              ADD CONSTRAINT {constraint_name}
              FOREIGN KEY ({child_col})
              REFERENCES {SCHEMA}.bots(id) ON DELETE CASCADE;
          END IF;
        END $$;
        """
    )


def upgrade() -> None:
    # 1. Nếu bảng cũ còn shape legacy (có cột 'name' nhưng chưa có 'bot_id'),
    #    drop để recreate. Nếu đã là shape mới → giữ nguyên (idempotent).
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
             WHERE table_schema = '{SCHEMA}' AND table_name = 'bots'
          ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = '{SCHEMA}' AND table_name = 'bots'
               AND column_name = 'bot_id'
          ) THEN
            DROP TABLE {SCHEMA}.bots CASCADE;
          END IF;
        END $$;
        """
    )

    # 2. Re-create bots với schema mới (idempotent).
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.bots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bot_id VARCHAR(64) NOT NULL,
            channel_type VARCHAR(32) NOT NULL,
            tenant_id INTEGER NULL,
            bot_name VARCHAR(255) NOT NULL,
            model_id UUID NULL,
            embedding_model_id UUID NULL,
            system_prompt TEXT NOT NULL DEFAULT '',
            workflow_id VARCHAR(128) NULL,
            setting_options JSONB NOT NULL DEFAULT '{DEFAULT_SETTING_OPTIONS}'::jsonb,
            is_deleted BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_bot_id_not_empty CHECK (length(trim(bot_id)) > 0)
        )
        """
    )

    # UNIQUE partial + lookup indexes.
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_bots_bot_channel_active
          ON {SCHEMA}.bots (bot_id, channel_type)
          WHERE is_deleted = false
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_bots_bot_channel "
        f"ON {SCHEMA}.bots (bot_id, channel_type)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_bots_tenant "
        f"ON {SCHEMA}.bots (tenant_id) WHERE tenant_id IS NOT NULL"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_bots_model "
        f"ON {SCHEMA}.bots (model_id) WHERE model_id IS NOT NULL"
    )

    # 3. Re-add FK cứng từ các bảng con (UUID → ragbot.bots.id), idempotent.
    _add_fk_if_absent("documents", "bot_id", "fk_documents_bot_id")
    _add_fk_if_absent("conversations", "bot_id", "fk_conversations_bot_id")
    _add_fk_if_absent("messages", "bot_id", "fk_messages_bot_id")
    _add_fk_if_absent(
        "bot_model_bindings", "bot_id", "fk_bot_model_bindings_bot_id"
    )


def downgrade() -> None:
    """Không test được production — chỉ recreate empty schema.

    Do 0 data production, downgrade = DROP + recreate với placeholder.
    KHÔNG restore FK constraint vì các bảng con có thể đã bị drop/alter.
    """
    for tbl, fk in (
        ("documents", "fk_documents_bot_id"),
        ("conversations", "fk_conversations_bot_id"),
        ("messages", "fk_messages_bot_id"),
        ("bot_model_bindings", "fk_bot_model_bindings_bot_id"),
    ):
        op.execute(
            f"ALTER TABLE {SCHEMA}.{tbl} DROP CONSTRAINT IF EXISTS {fk}"
        )
    # Cũng drop các FK tên cũ nếu tồn tại.
    for tbl, fk in (
        ("documents", "fk_documents_bot"),
        ("conversations", "fk_conversations_bot"),
        ("messages", "fk_messages_bot"),
        ("bot_model_bindings", "fk_bindings_bot"),
    ):
        op.execute(
            f"ALTER TABLE {SCHEMA}.{tbl} DROP CONSTRAINT IF EXISTS {fk}"
        )

    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.bots CASCADE")

    # Recreate placeholder (không match legacy — ok vì 0 data).
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.bots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
