"""Add ZeroEntropy secondary API key to api_keys pool (round-robin failover).

Revision: 0160
Prev:     0159

Trigger (operator request 2026-06-03):
  Primary ZE key (ze_EzjsjfTKqoGB1Xwb) tripped CircuitBreaker on
  "fast" tier rate-limit during 120Q load test. Adding user-supplied
  secondary key (ze_MaUwgJGkwpV7g2ss, verified HTTP 200 on embed + rerank)
  enables round-robin via DBBackedApiKeyPoolFactory.

Effect:
  - Effective rate-limit ~doubles (2 keys round-robin)
  - CB resilience: one key 503 → pool falls back to other
  - Hot-swap behaviour: rotation_state='live' marks both as eligible

Sacred-rule alignment:
  ✅ Pure alembic DML (CLAUDE.md rule 7) — no psql hot-fix
  ✅ value_plain field — DB-side encryption optional (matches existing pattern
     for ze_EzjsjfTKqoGB1Xwb primary key which is plaintext)
  ✅ Reversible — downgrade deletes the secondary key
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0160"
down_revision: str | None = "0159"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_NEW_KEY = "ze_MaUwgJGkwpV7g2ss"


def upgrade() -> None:
    """Add secondary ZE key for round-robin pool."""
    op.execute(
        text(
            """
            INSERT INTO api_keys (
                id, record_provider_id, provider_code, label,
                value_plain, active, rotation_state, metadata_json,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                record_provider_id,
                'zeroentropy',
                'secondary',
                :new_key,
                true,
                'live',
                jsonb_build_object(
                    'source', 'operator_supplied',
                    'verified_endpoints', jsonb_build_array('embed', 'rerank'),
                    'added_at', NOW()::text,
                    'reason', 'failover_pool_for_primary_rate_limit'
                ),
                NOW(),
                NOW()
            FROM api_keys
            WHERE provider_code = 'zeroentropy'
              AND label = 'primary'
              AND active = true
              AND deleted_at IS NULL
            LIMIT 1
            ON CONFLICT DO NOTHING
            """,
        ).bindparams(new_key=_NEW_KEY),
    )


def downgrade() -> None:
    """Remove secondary key."""
    op.execute(
        text(
            """
            UPDATE api_keys
            SET active = false,
                rotation_state = 'retired',
                deleted_at = NOW(),
                updated_at = NOW()
            WHERE provider_code = 'zeroentropy'
              AND label = 'secondary'
              AND value_plain = :new_key
            """,
        ).bindparams(new_key=_NEW_KEY),
    )
