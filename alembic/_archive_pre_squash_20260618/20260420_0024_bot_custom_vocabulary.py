"""0024 — Add custom_vocabulary JSONB column to bots table.

Per-bot custom abbreviation/diacritic map for domain-specific vocabulary.
Format: {"abbreviations": {"goi dau": "gội đầu"}, "diacritics": {"co vai gay": "cổ vai gáy"}}

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-20
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE bots "
        "ADD COLUMN IF NOT EXISTS custom_vocabulary JSONB DEFAULT '{}'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN bots.custom_vocabulary IS "
        "'Per-bot custom abbreviation/diacritic map. "
        "Format: {\"abbreviations\": {\"goi dau\": \"gội đầu\"}, "
        "\"diacritics\": {\"co vai gay\": \"cổ vai gáy\"}}'"
    ))


def downgrade() -> None:
    op.execute(text(
        "ALTER TABLE bots DROP COLUMN IF EXISTS custom_vocabulary"
    ))
