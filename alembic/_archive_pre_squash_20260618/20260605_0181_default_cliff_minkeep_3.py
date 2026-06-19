"""Make rerank_cliff_min_keep DEFAULT = 3 (robust-by-default for every bot).

Revision: 0181
Prev:     0180

Step-level forensic (request_steps funnel, 2026-06-05) showed the cliff filter
with min_keep=1 collapses the kept chunk-set to ONE whenever the semantic
reranker under-ranks an exact-answer chunk that lexical (BM25) ranks #1 — a
single reranker mis-score becomes a hard retrieval miss. Per the design
principle "the DEFAULT must be happy; per-bot custom only adds expert tuning,
never rescues a broken default", the platform default is raised 1→3 so EVERY
bot — including newly-created ones that have no per-bot override — keeps a small
relevant cluster past the cliff. Legal bots keep their per-bot 5 (0180) as an
additive expert override (>= default).

Seeds the system_config row so existing deployments pick up the new default at
runtime (the constant DEFAULT_RERANK_CLIFF_MIN_KEEP is also bumped to 3 for
fresh installs / per-bot schema fallback). Idempotent. Reversible. Rule 7.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0181"
down_revision: str | None = "0180"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES ('rerank_cliff_min_keep', '3'::jsonb, 'int',
                    'Cliff filter min chunks kept — default 3 so one reranker mis-score cannot collapse to 1 (forensic 2026-06-05).',
                    NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = '3'::jsonb, value_type = 'int',
                description = EXCLUDED.description, updated_at = NOW()
        """)
    )


def downgrade() -> None:
    op.execute(text("DELETE FROM system_config WHERE key = 'rerank_cliff_min_keep'"))
