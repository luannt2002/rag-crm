"""Add ``language_packs`` table for DB-driven prompt translations.

Why
---
``src/ragbot/shared/i18n.py`` used to hardcode the platform-internal
prompt set in two ``LanguagePack`` dataclasses (vi + en). Adding a new
language (Spanish, Khmer, Thai, …) required editing Python source and
shipping a new release — direct violation of the CLAUDE.md core MVP
mindset (User explicit 2026-05-01: "có thêm 1 lĩnh vực mới là chạy vào
changes code?").

This migration introduces a tiny key-value table so the platform can
add languages and tune wording at runtime via SQL only:

    code           VARCHAR(8)   NOT NULL  -- BCP47-ish, e.g. 'vi', 'en'
    prompt_key     VARCHAR(64)  NOT NULL  -- 'generator', 'grader', …
    content        TEXT         NOT NULL
    version        INTEGER      NOT NULL DEFAULT 1
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
    PRIMARY KEY (code, prompt_key)

Migration 0056 seeds the existing ``vi`` + ``en`` content verbatim so
behaviour does not change post-deploy. ``ragbot.shared.i18n`` is kept
as an in-memory fallback (boot-time DB outage / partial seeds) but is
no longer the source of truth.

Idempotent. Composite PK is sufficient (no surrogate id needed because
rows are addressed by ``(code, prompt_key)`` from application code).

Revision ID: 0055
Revises: 0054
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "language_packs",
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("prompt_key", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("code", "prompt_key", name="pk_language_packs"),
    )
    # Secondary index on ``code`` is implicit in the leftmost-prefix of
    # the PK; no extra index needed for ``WHERE code = :c`` queries.


def downgrade() -> None:
    op.drop_table("language_packs")
