"""Per-bot embedding_text_strategy = raw_only for thong-tu (legal exact-match).

Revision: 0166
Prev:     0165

Root cause (verify 20Q 2026-06-04): global `embedding_text_strategy =
prefix_plus_raw` (set in 0165) HELPED semantic spa queries but HURT
thong-tu legal exact-article lookup — "Điều 34" top_score dropped
0.79 -> 0.30 because the Contextual-Retrieval prefix dilutes the precise
structural-anchor embedding. Embedding strategy must be PER-BOT: legal
wants raw_only (exact anchor), spa keeps prefix_plus_raw (semantic).

Override goes through `bots.plan_limits.embedding_text_strategy` (per
embedding_text_port.py resolve chain: plan_limits > system_config).

Sacred-rule: pure alembic DML, reversible, per-bot config (not core code).
NOTE: re-ingest thong-tu after upgrade so embeddings rebuild raw_only.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0166"
down_revision: str | None = "0165"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOT = "thong-tu-09-2020-tt-nhnn"


def upgrade() -> None:
    op.execute(text("""
        UPDATE bots
        SET plan_limits = COALESCE(plan_limits, '{}'::jsonb)
                          || '{"embedding_text_strategy": "raw_only"}'::jsonb,
            updated_at = NOW()
        WHERE bot_id = :bot
    """).bindparams(bot=_BOT))


def downgrade() -> None:
    op.execute(text("""
        UPDATE bots
        SET plan_limits = plan_limits - 'embedding_text_strategy',
            updated_at = NOW()
        WHERE bot_id = :bot
    """).bindparams(bot=_BOT))
