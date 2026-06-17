"""0017 — api_tokens table for service-to-service JWT auth with versioning.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id              UUID            PRIMARY KEY,
            service_name    VARCHAR(128)    NOT NULL UNIQUE,
            description     TEXT            NOT NULL DEFAULT '',
            token_hash      VARCHAR(64)     NOT NULL,
            version         INTEGER         NOT NULL DEFAULT 1,
            created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
            revoked_at      TIMESTAMPTZ
        )
    """))

    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_api_tokens_service
        ON api_tokens (service_name) WHERE revoked_at IS NULL
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS api_tokens"))
