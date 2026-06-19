"""[T3-Refactor] seed ``vector_store_provider`` into system_config

Revision ID: 010d
Revises: 010c
Create Date: 2026-05-16

Adds the operator-tunable ``vector_store_provider`` key to
``system_config`` so :mod:`ragbot.infrastructure.vector.registry` can resolve
the active vector backend at boot. Default value ``pgvector`` preserves the
current behaviour bit-for-bit; flipping to ``null`` puts the platform into
the fail-soft no-op state (retrieval returns []) without a code change.

Idempotent — ``INSERT … ON CONFLICT DO NOTHING`` lets the migration re-run
safely when an operator has pre-seeded the row manually.
"""

from __future__ import annotations

from alembic import op


revision = "010d"
down_revision = "010c"
branch_labels = None
depends_on = None


_VECTOR_STORE_PROVIDER_KEY = "vector_store_provider"
_VECTOR_STORE_PROVIDER_DEFAULT = "pgvector"


def upgrade() -> None:
    # ``system_config.value`` is JSONB in the canonical schema — store the
    # provider string as a JSON scalar so SystemConfigService.get returns
    # the bare ``"pgvector"`` string after json decode.
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) "
        "VALUES ("
        f"'{_VECTOR_STORE_PROVIDER_KEY}', "
        f"to_jsonb('{_VECTOR_STORE_PROVIDER_DEFAULT}'::text), "
        "'string', "
        "'Vector store backend resolved by infrastructure/vector/registry.py — "
        "pgvector (default) | postgres (alias) | null (disabled)'"
        ") "
        "ON CONFLICT (key) DO NOTHING",
    )


def downgrade() -> None:
    # Idempotent inverse — leaves caller-edited values intact.
    op.execute(
        f"DELETE FROM system_config WHERE key = '{_VECTOR_STORE_PROVIDER_KEY}'",
    )
