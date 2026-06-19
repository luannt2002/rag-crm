"""Phase 14 — per-bot rerank intent whitelist.

Adds optional ``bots.rerank_intent_whitelist`` JSONB column. NULL preserves
legacy always-rerank behaviour. When set, the rerank node skips Jina rerank
unless the live ``state["intent"]`` is in ``intents``.

Source: ``reports/TOP_SCORE_BOOST_ANALYSIS_20260430.md`` — Jina cross-encoder
boost is +0.20..+0.45 on factoid / comparison / aggregation / booking /
yesno but only +0.00..+0.18 on chitchat / off_topic / vu_vo (no relevant
docs to lift). Skipping rerank on the low-boost intents saves ~150ms per
turn + Jina API cost without changing answer quality.

JSONB shape:
    {"enabled": true, "intents": ["factoid", "comparison", ...]}

Backward-compat: every existing row keeps NULL → legacy always-rerank.

Revision: 0053
Down revision: 0052
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``rerank_intent_whitelist`` JSONB NULL column to ``bots``."""
    op.execute(text("""
        ALTER TABLE bots
        ADD COLUMN IF NOT EXISTS rerank_intent_whitelist JSONB NULL
    """))


def downgrade() -> None:
    """Drop the column. Legacy always-rerank is the implicit default."""
    op.execute(text(
        "ALTER TABLE bots DROP COLUMN IF EXISTS rerank_intent_whitelist"
    ))
