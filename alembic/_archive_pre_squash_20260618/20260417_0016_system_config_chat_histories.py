"""0016 — system_config table + chat_histories table + rename user_id→connect_id.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # =====================================================================
    # 1. system_config — key/value store for application-wide settings
    # =====================================================================
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS system_config (
            key         VARCHAR(128)    PRIMARY KEY,
            value       JSONB           NOT NULL,
            value_type  VARCHAR(32)     NOT NULL DEFAULT 'string',
            description TEXT,
            updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """))

    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_system_config_updated
        ON system_config (updated_at DESC)
    """))

    # Seed default config values
    op.execute(text("""
        INSERT INTO system_config (key, value, value_type, description) VALUES
        ('chat_max_history',      '10',    'integer', 'Số tin nhắn tối đa giữ lại trong lịch sử chat (tính cả user + bot)'),
        ('chat_ttl',              '0',     'integer', 'TTL lịch sử chat (giây). 0 = không hết hạn (lưu DB)'),
        ('default_bot_id',        '"1774946011723"', 'string', 'Bot ID mặc định cho demo platform'),
        ('audit_page_size',       '50',    'integer', 'Số bản ghi mỗi trang audit'),
        ('audit_max_temp_tables', '2',     'integer', 'Số temp table tối đa mỗi bot khi query audit'),
        ('rag_top_k',                    '5',      'integer', 'Số chunks lấy khi vector search'),
        ('rag_chunk_size',               '1024',   'integer', 'Chunk size khi ingest document'),
        ('rag_chunk_overlap',            '128',    'integer', 'Overlap giữa chunks'),
        ('llm_default_temperature',      '0.3',    'float',   'Temperature mặc định khi tạo bot'),
        ('llm_default_max_tokens',       '450',    'integer', 'Max tokens mặc định khi tạo bot'),
        ('llm_default_top_p',            '0.4',    'float',   'Top-p mặc định'),
        ('llm_default_model',            '"gpt-4.1-mini"', 'string', 'Model LLM mặc định'),
        ('llm_timeout_s',                '30',     'integer', 'Timeout cho LLM call (giây)'),
        ('question_max_length',          '4000',   'integer', 'Giới hạn độ dài câu hỏi'),
        ('embedding_model',              '"text-embedding-3-small"', 'string', 'Model embedding mặc định'),
        ('embedding_dimension',          '1536',   'integer', 'Chiều embedding vector')
        ON CONFLICT (key) DO NOTHING
    """))

    # =====================================================================
    # 2. chat_histories — lịch sử chat lưu DB (không dùng Redis)
    # =====================================================================
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS chat_histories (
            id              BIGSERIAL       PRIMARY KEY,
            bot_id          UUID            NOT NULL,
            channel_type    VARCHAR(64)     NOT NULL DEFAULT 'web',
            connect_id      VARCHAR(255)    NOT NULL,
            role            VARCHAR(16)     NOT NULL,
            content         TEXT            NOT NULL,
            created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """))

    # Indexes cho query nhanh
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_chat_histories_room
        ON chat_histories (bot_id, channel_type, connect_id, created_at DESC)
    """))

    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_chat_histories_bot
        ON chat_histories (bot_id, created_at DESC)
    """))

    # =====================================================================
    # 3. Rename user_id → connect_id trong request_logs
    # =====================================================================
    op.execute(text("""
        ALTER TABLE request_logs
        RENAME COLUMN user_id TO connect_id
    """))

    # =====================================================================
    # 4. Rename user_id → connect_id trong conversations
    # =====================================================================
    op.execute(text("""
        ALTER TABLE conversations
        RENAME COLUMN user_id TO connect_id
    """))

    # Drop old unique constraint and recreate with new column name
    op.execute(text("""
        ALTER TABLE conversations
        DROP CONSTRAINT IF EXISTS uq_conv_bot_user
    """))
    op.execute(text("""
        ALTER TABLE conversations
        ADD CONSTRAINT uq_conv_bot_connect
        UNIQUE (bot_id, connect_id)
    """))


def downgrade() -> None:
    # Reverse rename
    op.execute(text("ALTER TABLE conversations DROP CONSTRAINT IF EXISTS uq_conv_bot_connect"))
    op.execute(text("ALTER TABLE conversations ADD CONSTRAINT uq_conv_bot_user UNIQUE (bot_id, connect_id)"))
    op.execute(text("ALTER TABLE conversations RENAME COLUMN connect_id TO user_id"))
    op.execute(text("ALTER TABLE request_logs RENAME COLUMN connect_id TO user_id"))
    op.execute(text("DROP TABLE IF EXISTS chat_histories"))
    op.execute(text("DROP TABLE IF EXISTS system_config"))
