"""[T3-Refactor] add ``requires_prefix`` to ai_providers

Revision ID: 010e
Revises: 010d
Create Date: 2026-05-16

Replaces the per-brand literal ``if p.code == "openai"`` branch inside
:mod:`ragbot.application.services.model_resolver` with a DB-driven flag.
``requires_prefix`` controls whether the LiteLLM model wire name is
constructed as ``{provider.code}/{model_name}`` (Cohere / Jina / Voyage /
ZeroEntropy / …) or as a bare ``{model_name}`` (OpenAI / Anthropic).

Default = TRUE (LiteLLM convention for non-OpenAI / non-Anthropic
providers). OpenAI + Anthropic are flipped to FALSE so the existing wire
contract is preserved bit-for-bit.

Idempotent — ``IF NOT EXISTS`` on the ADD COLUMN and ``WHERE code IN (...)``
on the UPDATE both let the migration re-run safely.
"""

from __future__ import annotations

from alembic import op


revision = "010e"
down_revision = "010d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the column NOT NULL with a literal TRUE default so existing rows
    # backfill in one statement (Postgres ≥ 11 stores the default in the
    # catalogue — no full table rewrite).
    op.execute(
        "ALTER TABLE ai_providers "
        "ADD COLUMN IF NOT EXISTS requires_prefix BOOLEAN NOT NULL DEFAULT TRUE",
    )
    # OpenAI + Anthropic native APIs do not require the LiteLLM provider
    # prefix; pin the historical wire contract.
    op.execute(
        "UPDATE ai_providers "
        "SET requires_prefix = FALSE "
        "WHERE code IN ('openai', 'anthropic')",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_providers DROP COLUMN IF EXISTS requires_prefix")
