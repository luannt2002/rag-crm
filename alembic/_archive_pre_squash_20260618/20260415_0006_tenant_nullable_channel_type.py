"""v0.3.0 — tenant_id nullable ở 18 bảng + thêm channel_type (nullable) vào 6 bảng audit.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-15

Rationale (per user):
- Không phải service khách nào cũng định danh được tenant → `tenant_id` có thể NULL.
  `bot_id` vẫn bắt buộc; khi cần derive tenant có thể JOIN `bots.tenant_id`.
- Thêm `channel_type VARCHAR(64)` — nullable — vào các bảng audit để nhận context
  từ khách (vd: 'zalo_oa', 'zalo_personal', 'api', 'web'). Không ràng buộc giá trị.

Tables touched:
- Nullable tenant_id: 18 bảng (mọi nơi cột tenant_id đang NOT NULL).
- Add channel_type: 6 bảng audit chính chưa có cột tương đương
  (request_logs, request_steps, feedback, jobs, outbox, tenant_model_policy).
  `messages` + `conversations` + `bots` đã có `channel VARCHAR(64)` (ý nghĩa tương
  đương) — KHÔNG add thêm để tránh confuse.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"

# Bảng có tenant_id cần thành nullable. `quotas` bị loại vì tenant_id là PK
# (không thể nullable — khoá định danh bản ghi quota theo tenant).
TENANT_NULLABLE_TABLES = [
    "ai_config_audit_log",
    "bot_ai_tools",
    "bot_model_bindings",
    "bots",
    "conversations",
    "documents",
    "feedback",
    "golden_questions",
    "intent_routes",
    "jobs",
    "messages",
    "outbox",
    "policy_audit_log",
    "prompt_templates",
    "request_logs",
    "request_steps",
    "tenant_model_policy",
]

# Bảng audit chính chưa có channel column — thêm channel_type (nullable).
CHANNEL_TYPE_TABLES = [
    "request_logs",
    "request_steps",
    "feedback",
    "jobs",
    "outbox",
    "tenant_model_policy",
]


def upgrade() -> None:
    for t in TENANT_NULLABLE_TABLES:
        # Some tables may not exist on clean DB (dropped in 0010); some never
        # had a literal ``tenant_id`` column (later history created them with
        # ``record_tenant_id`` directly). Guard on column existence so a fresh
        # ``alembic upgrade head`` replay stays reproducible.
        op.execute(
            f"""DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema='{SCHEMA}' AND table_name='{t}'
                      AND column_name='tenant_id'
                ) THEN
                    ALTER TABLE {SCHEMA}.{t} ALTER COLUMN tenant_id DROP NOT NULL;
                END IF;
            END $$;"""
        )

    for t in CHANNEL_TYPE_TABLES:
        op.execute(
            f"ALTER TABLE IF EXISTS {SCHEMA}.{t} "
            f"ADD COLUMN IF NOT EXISTS channel_type VARCHAR(64)"
        )


def downgrade() -> None:
    for t in CHANNEL_TYPE_TABLES:
        op.execute(
            f"ALTER TABLE {SCHEMA}.{t} DROP COLUMN IF EXISTS channel_type"
        )

    # Restoring NOT NULL is unsafe if rows now have NULL. Leave to manual ops.
    for t in TENANT_NULLABLE_TABLES:
        op.execute(
            f"ALTER TABLE {SCHEMA}.{t} ALTER COLUMN tenant_id SET NOT NULL"
        )
