"""v0.3.0 Task 4 — ModelInvocationLogger (INVARIANT #2).

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-15

Tạo 4 bảng audit cho mọi LLM/embed/rerank call:
- prompt_versions   : versioned prompt templates (no-overwrite)
- model_invocations : 1 row/call — full chain audit (hash-only payload)
- context_snapshots : retrieved chunks + scores per message/request
- payload_blobs     : optional gzip-ed raw prompt/response (lookup by invocation_id)

Chỉ xài raw SQL qua op.execute (style theo 0003/0004). Không FK cross-service,
request_id/step_id là soft ref (không FK) — tránh phụ thuộc giữa bảng audit.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "public"


def upgrade() -> None:
    # --- prompt_versions ---------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.prompt_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NULL,
            purpose VARCHAR(32) NOT NULL,
            name VARCHAR(128) NOT NULL,
            version_no INT NOT NULL DEFAULT 1,
            template TEXT NOT NULL,
            variables JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_prompt_versions_tenant_name_ver
                UNIQUE (tenant_id, name, version_no)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_prompt_versions_purpose "
        f"ON {SCHEMA}.prompt_versions (purpose)"
    )

    # --- model_invocations -------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.model_invocations (
            invocation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id BIGINT NOT NULL,
            request_id UUID NULL,
            tenant_id UUID NULL,
            step_id UUID NULL,
            attempt_no INT NOT NULL DEFAULT 1,
            purpose VARCHAR(32) NOT NULL,
            provider VARCHAR(32) NOT NULL,
            model_id VARCHAR(128) NOT NULL,
            model_version VARCHAR(64) NULL,
            params JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            system_prompt_version_id UUID NULL,
            user_prompt_hash CHAR(64) NULL,
            full_payload_hash CHAR(64) NULL,
            response_hash CHAR(64) NULL,
            retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{{}}'::uuid[],
            prompt_tokens INT NOT NULL DEFAULT 0,
            completion_tokens INT NOT NULL DEFAULT 0,
            cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ NULL,
            duration_ms INT NOT NULL DEFAULT 0,
            status VARCHAR(16) NOT NULL DEFAULT 'success',
            finish_reason VARCHAR(32) NULL,
            cached BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_model_inv_message "
        f"ON {SCHEMA}.model_invocations (message_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_model_inv_request_attempt "
        f"ON {SCHEMA}.model_invocations (request_id, attempt_no)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_model_inv_tenant_started "
        f"ON {SCHEMA}.model_invocations (tenant_id, started_at)"
    )

    # --- context_snapshots -------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.context_snapshots (
            snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id BIGINT NOT NULL,
            request_id UUID NULL,
            tenant_id UUID NULL,
            retrieved_chunks JSONB NOT NULL DEFAULT '[]'::jsonb,
            retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{{}}'::uuid[],
            retrieval_scores JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_ctx_snap_message "
        f"ON {SCHEMA}.context_snapshots (message_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_ctx_snap_request "
        f"ON {SCHEMA}.context_snapshots (request_id)"
    )

    # --- payload_blobs -----------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.payload_blobs (
            blob_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            invocation_id UUID NOT NULL
                REFERENCES {SCHEMA}.model_invocations(invocation_id) ON DELETE CASCADE,
            kind VARCHAR(16) NOT NULL,
            content_gzip BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_payload_blobs_invocation "
        f"ON {SCHEMA}.payload_blobs (invocation_id)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.payload_blobs")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.context_snapshots")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.model_invocations")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.prompt_versions")
