"""api_keys table — runtime hot-swap for provider credentials.

Adds a small ``api_keys`` table so an operator can rotate a provider's
API key (ZeroEntropy, OpenAI, Anthropic, etc.) via admin PUT without
restarting the worker. Adapters keep their existing env-var fallback so
the migration is non-breaking — they prefer DB rows when present,
fall back to env when absent.

Schema:
- ``provider_code`` denormalised so the per-call lookup avoids a join.
- ``label`` lets one provider host multiple keys (``primary`` / ``backup``).
- ``rotation_state`` enables blue/green swap (``live`` → ``cooldown`` →
  ``revoked``).
- ``value_plain`` is plain-text. AES-GCM at-rest encryption is a planned
  follow-up commit (column ``value_encrypted`` reserved for it).
- Soft-delete via ``deleted_at`` so audit forensics keep working.

Hot-reload path: see ``ProviderKeyResolver`` + admin
``PUT /api/ragbot/test/admin/api-keys/{provider_code}``.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE api_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                record_provider_id UUID REFERENCES ai_providers(id) ON DELETE SET NULL,
                provider_code VARCHAR(64) NOT NULL,
                label VARCHAR(64) NOT NULL DEFAULT 'primary',
                value_plain TEXT,
                value_encrypted TEXT,
                active BOOLEAN NOT NULL DEFAULT true,
                rotation_state VARCHAR(16) NOT NULL DEFAULT 'live'
                    CHECK (rotation_state IN ('live', 'cooldown', 'revoked')),
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_at TIMESTAMPTZ
            )
            """,
        )
    )
    op.execute(
        text(
            """
            CREATE UNIQUE INDEX uq_api_keys_provider_label_live
            ON api_keys (provider_code, label)
            WHERE deleted_at IS NULL
            """,
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX ix_api_keys_active
            ON api_keys (provider_code, active)
            WHERE active = true AND deleted_at IS NULL
            """,
        )
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS api_keys"))
