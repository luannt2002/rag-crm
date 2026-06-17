"""Seed Redis-cache TTL knobs for the short-TTL hot-path caches.

Stream S5 Pipeline-Opt adds two new short-TTL caches on the LLM/embed
hot path. Both default 3600s (1h) — long enough to amortise repeat
queries inside a typical user session, short enough that a prompt
revision or model swap clears itself out within an hour without manual
Redis flush.

Keys:
- ``understand_query.cache_ttl_s`` (int seconds) — UQ LLM memo.
- ``embed.cache_ttl_s`` (int seconds) — narrow embed wrapper.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already
seeded is a no-op.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0087a"
down_revision = "0087"
branch_labels = None
depends_on = None


_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "understand_query.cache_ttl_s",
        "3600",
        "int",
        (
            "TTL (seconds) for understand_query LLM-output Redis cache. "
            "Repeat queries within the window skip the LLM round-trip. "
            "Default 3600s = 1 hour. Bump PROMPT_VERSION_UQ in code on any "
            "i18n.py understand-query prompt change to namespace prior "
            "cached classifications out without manual flush."
        ),
    ),
    (
        "embed.cache_ttl_s",
        "3600",
        "int",
        (
            "TTL (seconds) for query embedding Redis cache (class-based "
            "infrastructure.cache.embed_cache.EmbedCache). Repeat queries "
            "across bots skip the embed provider round-trip (model-scoped "
            "key — model swap is namespace-safe). Default 3600s = 1 hour."
        ),
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _SEEDS:
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, (:value)::jsonb, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """,
            ).bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            ),
        )


def downgrade() -> None:
    for key, _value, _value_type, _description in _SEEDS:
        op.execute(
            text("DELETE FROM system_config WHERE key = :key").bindparams(key=key),
        )
