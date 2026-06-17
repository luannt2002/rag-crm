"""Embedding-text strategy = "auto" (domain-neutral, structure-driven).

Revision: 0167
Prev:     0166

Replaces the per-bot band-aid (0166: thong-tu plan_limits = raw_only) with a
GENERIC rule that works for EVERY bot in EVERY domain, with no per-bot config:

  system_config.embedding_text_strategy = "auto"
    → DocumentService derives the strategy from each document's CHUNK
      STRUCTURE at ingest:
        - structural docs (HDT: legal/admin with Điều/Chương anchors) → raw_only
          (exact-anchor lookup; CR prefix would dilute)
        - prose / table / FAQ docs → prefix_plus_raw (situated context aids
          semantic match)

Any legal/structured bot auto-gets raw_only; any prose/FAQ bot auto-gets
prefix_plus_raw — selection keys on chunk structure, never on bot identity.
The per-bot override from 0166 is removed so thong-tu falls through to "auto".

Sacred-rule: zero-hardcode (config "auto" + STRUCTURAL_CHUNK_STRATEGIES in
shared/constants.py), domain-neutral (no bot/domain literal in the rule),
reversible. NOTE: re-ingest required for embeddings to rebuild.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0167"
down_revision: str | None = "0166"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # Generic structure-driven default for all bots/domains.
    op.execute(text("""
        UPDATE system_config
        SET value = '"auto"', updated_at = NOW()
        WHERE key = 'embedding_text_strategy'
    """))
    # Remove the per-bot band-aid (0166) — "auto" now covers it generically.
    op.execute(text("""
        UPDATE bots
        SET plan_limits = plan_limits - 'embedding_text_strategy',
            updated_at = NOW()
        WHERE plan_limits ? 'embedding_text_strategy'
    """))


def downgrade() -> None:
    op.execute(text("""
        UPDATE system_config
        SET value = '"prefix_plus_raw"', updated_at = NOW()
        WHERE key = 'embedding_text_strategy'
    """))
    op.execute(text("""
        UPDATE bots
        SET plan_limits = COALESCE(plan_limits, '{}'::jsonb)
                          || '{"embedding_text_strategy": "raw_only"}'::jsonb,
            updated_at = NOW()
        WHERE bot_id = 'thong-tu-09-2020-tt-nhnn'
    """))
