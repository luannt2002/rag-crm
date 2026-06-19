"""v0.3.0 Task EXT — message_id là ID của khách (INT), không phải UUID của ragbot.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-15

Luồng: khách gửi request kèm `{tenant_id, message_id}` (cả hai là ID bên khách).
Ragbot chỉ LƯU 2 trường này làm metadata để group metric — không FK cross-service,
không transform, không cần bảng map trung gian. `request_logs.id` / `feedback.id`
là PK UUID của ragbot (tách bạch với `message_id`).

Changes:
- `request_logs`: DROP FK `fk_request_logs_message_id` + ALTER `message_id` UUID → INTEGER.
  Replace index `ix_reqlog_message_id` with `(tenant_id, message_id)` composite.
- `feedback`: ALTER `message_id` UUID → INTEGER. Replace index `ix_feedback_msg`
  with `(tenant_id, message_id)` composite.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def upgrade() -> None:
    # --- request_logs ------------------------------------------------------
    # Drop ANY FK on request_logs.message_id (named or auto-named variants).
    op.execute(
        f"""
        DO $$
        DECLARE c record;
        BEGIN
            FOR c IN
                SELECT conname FROM pg_constraint
                WHERE contype='f'
                  AND conrelid = '{SCHEMA}.request_logs'::regclass
                  AND conkey = (
                      SELECT array_agg(attnum) FROM pg_attribute
                      WHERE attrelid = '{SCHEMA}.request_logs'::regclass
                        AND attname = 'message_id'
                  )
            LOOP
                EXECUTE format('ALTER TABLE {SCHEMA}.request_logs DROP CONSTRAINT %I', c.conname);
            END LOOP;
        END $$;
        """
    )
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_reqlog_message_id")
    # UUID → INTEGER. No data preserved: column was UUID = unrelated type.
    # Clear rows + alter type (DB has 0 rows at this point on fresh bootstrap).
    op.execute(f"DELETE FROM {SCHEMA}.request_logs")
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ALTER COLUMN message_id TYPE INTEGER USING NULL"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ALTER COLUMN message_id SET NOT NULL"
    )
    # AdapChunk reorg 2026-05-14: on fresh DB, 0001's Base.metadata.create_all
    # creates request_logs with record_tenant_id (post-0034 ORM rename), so
    # use conditional DDL to pick whichever column actually exists.
    op.execute(
        f"""
        DO $$
        DECLARE col_name text;
        BEGIN
            SELECT column_name INTO col_name FROM information_schema.columns
            WHERE table_schema = '{SCHEMA}'
              AND table_name = 'request_logs'
              AND column_name IN ('tenant_id', 'record_tenant_id')
            ORDER BY column_name DESC LIMIT 1;
            IF col_name IS NULL THEN
                RAISE NOTICE 'request_logs has no tenant column; skip index';
            ELSE
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS ix_reqlog_tenant_message '
                    'ON {SCHEMA}.request_logs (%I, message_id)',
                    col_name
                );
            END IF;
        END $$;
        """
    )

    # --- feedback (may not exist on clean DB — dropped later in 0010) ------
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_feedback_msg")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='{SCHEMA}' AND tablename='feedback') THEN
                DELETE FROM {SCHEMA}.feedback;
                ALTER TABLE {SCHEMA}.feedback ALTER COLUMN message_id TYPE INTEGER USING NULL;
                ALTER TABLE {SCHEMA}.feedback ALTER COLUMN message_id SET NOT NULL;
                -- Conditional column pick (same as request_logs above):
                DECLARE
                    fb_col text;
                BEGIN
                    SELECT column_name INTO fb_col FROM information_schema.columns
                    WHERE table_schema = '{SCHEMA}'
                      AND table_name = 'feedback'
                      AND column_name IN ('tenant_id', 'record_tenant_id')
                    ORDER BY column_name DESC LIMIT 1;
                    IF fb_col IS NOT NULL THEN
                        EXECUTE format(
                            'CREATE INDEX IF NOT EXISTS ix_feedback_tenant_msg '
                            'ON {SCHEMA}.feedback (%I, message_id)',
                            fb_col
                        );
                    END IF;
                END;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_feedback_tenant_msg")
    op.execute(f"DELETE FROM {SCHEMA}.feedback")
    op.execute(
        f"ALTER TABLE {SCHEMA}.feedback "
        "ALTER COLUMN message_id TYPE UUID USING NULL"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_feedback_msg "
        f"ON {SCHEMA}.feedback (message_id)"
    )

    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_reqlog_tenant_message")
    op.execute(f"DELETE FROM {SCHEMA}.request_logs")
    op.execute(
        f"ALTER TABLE {SCHEMA}.request_logs "
        "ALTER COLUMN message_id TYPE UUID USING NULL"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_reqlog_message_id "
        f"ON {SCHEMA}.request_logs (message_id)"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.request_logs
        ADD CONSTRAINT fk_request_logs_message_id
        FOREIGN KEY (message_id) REFERENCES {SCHEMA}.messages(id)
        ON DELETE SET NULL
        """
    )
