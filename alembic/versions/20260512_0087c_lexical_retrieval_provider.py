"""Seed lexical_retrieval_provider system_config (Strategy + DI defaults).

The Postgres tsvector + GIN index on ``document_chunks.search_vector`` is
already established by alembic 0028; this migration only seeds the
``system_config`` rows that drive the new ``LexicalRetrievalPort`` Strategy
registry. Default provider is ``"null"`` so existing tenants see no
behaviour change until an operator flips the row.

Operator opt-in:
    UPDATE system_config SET value='pg_textsearch'
    WHERE key='lexical_retrieval_provider';
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0087c"
down_revision = "0087b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``ON CONFLICT DO NOTHING`` keeps the migration idempotent and respects
    # any operator-set value already present from staging experiments.
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value) VALUES
              ('lexical_retrieval_provider', 'null'),
              ('lexical_top_k', '20'),
              ('lexical_rrf_k', '60')
            ON CONFLICT (key) DO NOTHING
            """,
        ),
    )


def downgrade() -> None:
    # Leave operator-curated rows in place; deleting them would surprise
    # admins who may have flipped lexical_retrieval_provider to a non-null
    # value. The keys are harmless when unused.
    return None
