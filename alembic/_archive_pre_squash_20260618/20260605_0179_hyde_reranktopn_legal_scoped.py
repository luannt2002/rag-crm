"""Per-bot retrieval tuning (scoped, blast-radius gated): HyDE + rerank_top_n.

Revision: 0179
Prev:     0178

Targeted fixes from ROOTCAUSE_PAPER_FIX_20260605, applied PER-BOT (not global)
so the blast radius is gated (CLAUDE.md: flags default-OFF, opt-in per-bot):

  - RC#2 (query-phrasing): enable HyDE for bots whose hardest questions are
    verbose multi-fact preambles where the raw query embeds poorly (lich-su,
    thong-tu). HyDE embeds a hypothetical answer instead of the verbose query
    (paper 01/16). Set via per-bot plan_limits.hyde_enabled (PLAN_LIMIT_SCHEMA),
    which overrides the global system_config default (False) only for these bots.

  - RC#3b (retrieval depth): raise rerank_top_n 7→10 for the legal bots so a
    rank-8..10 exact-clause chunk has a chance to clear the rerank cut
    (paper 29 immediate proxy). Set via the dedicated bots.rerank_top_n column.

Global flags (adaptive_rerank_weight, model-tier) intentionally NOT touched here
— they need their own A/B (would regress the 95%+ semantic bots) and the
mini-for-answer cost decision (feedback_haiku_partial_only) stands.

Idempotent (jsonb_set / column set). Reversible. Rule 7 (alembic, not psql).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0179"
down_revision: str | None = "0178"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_HYDE_BOTS = ("lich-su-vn", "thong-tu-09-2020-tt-nhnn")
_TOPN_BOTS = ("thong-tu-09-2020-tt-nhnn", "luat-giao-thong")


def upgrade() -> None:
    op.execute(
        text("""
            UPDATE bots
            SET plan_limits = jsonb_set(
                    COALESCE(plan_limits, '{}'::jsonb), '{hyde_enabled}', 'true'::jsonb, true),
                updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(bots=list(_HYDE_BOTS))
    )
    op.execute(
        text("""
            UPDATE bots SET rerank_top_n = 10, updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(bots=list(_TOPN_BOTS))
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE bots
            SET plan_limits = plan_limits - 'hyde_enabled', updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(bots=list(_HYDE_BOTS))
    )
    op.execute(
        text("""
            UPDATE bots SET rerank_top_n = NULL, updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(bots=list(_TOPN_BOTS))
    )
