"""Set ai_providers.api_key_ref='JINA_API_KEY' for the jina_ai provider.

0228 seeded the provider before this column was included; the /health/models
probe reads the key via ``ai_providers.api_key_ref`` (the env-var NAME), so a
NULL ref makes the reranker probe report ``missing_api_key`` even though the
runtime adapter resolves ``RERANKER_JINA_API_KEY``/``JINA_API_KEY`` from env
directly. Idempotent backfill so the health probe and runtime agree.
"""
from alembic import op

revision = "0229"
down_revision = "0228"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE ai_providers SET api_key_ref = 'JINA_API_KEY', updated_at = now() "
        "WHERE code = 'jina_ai' AND (api_key_ref IS NULL OR api_key_ref <> 'JINA_API_KEY')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE ai_providers SET api_key_ref = NULL, updated_at = now() "
        "WHERE code = 'jina_ai'"
    )
