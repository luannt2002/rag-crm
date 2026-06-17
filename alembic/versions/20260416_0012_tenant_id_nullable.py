"""0012 — Nullable tenant_id trên các bảng core.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-16

Context
-------
Shim boundary: upstream NestJS sở hữu `tenant_id INT`. Các bảng legacy của
ragbot (messages, conversations, request_logs, ...) dùng `tenant_id UUID`
làm FK nội bộ. Khi payload chat.received không kèm `tenant_uuid`,
worker phải ghi NULL thay vì ép INT → UUID (raise 22P02).

Migration này DROP NOT NULL ở các bảng core để chat pipeline chạy khi
chưa có `tenant_uuid`. Code mới, không có dữ liệu production nên
downgrade để rỗng — không thể khôi phục NOT NULL nếu đã có NULL rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "public"

# Bảng nghi có tenant_id NOT NULL. `ALTER COLUMN ... DROP NOT NULL`
# idempotent trên PostgreSQL — nếu bảng đã nullable vẫn chạy OK.
# Bọc IF EXISTS / exception-safe bằng DO block để migration không fail
# nếu bảng / cột chưa tồn tại trong môi trường dev.
_TABLES: tuple[str, ...] = (
    "messages",
    "conversations",
    "request_logs",
    "request_steps",
    "model_invocations",
    "context_snapshots",
    "guardrail_events",
    "feedback",
    "audit_log",
)


def upgrade() -> None:
    for tbl in _TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{SCHEMA}'
                      AND table_name = '{tbl}'
                      AND column_name = 'tenant_id'
                      AND is_nullable = 'NO'
                ) THEN
                    EXECUTE 'ALTER TABLE {SCHEMA}.{tbl} '
                         || 'ALTER COLUMN tenant_id DROP NOT NULL';
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    # Không khôi phục NOT NULL: có thể đã có NULL rows sau migration.
    # Code mới, không có dữ liệu production → no-op là chấp nhận được.
    pass
