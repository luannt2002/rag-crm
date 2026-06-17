"""Align ``system_config`` rows with ``shared/constants.py`` post-ZE.

Revision: 0139
Prev:     0138

Trigger (2026-05-29 master consolidated fix-all plan, Phase 2.4):
  DB scan vs constants.py audit revealed 3 system_config rows are stale
  from the Jina→ZeroEntropy era and no longer match the constants tier 7
  fallback. The constants values are pinned by unit tests as the
  HALLU-safe / latency-tuned floors after the ZE migration; the DB rows
  drifted because alembic 0067 (Jina era) tuned them for the old score
  distribution and nobody re-aligned them when ZE became the production
  reranker.

  | Key                          | DB (stale) | Constants (correct) | Reason |
  |------------------------------|------------|---------------------|--------|
  | reranker_min_score_active    | 0.15       | 0.30                | ZE distribution: 0.30+ correlates with relevance; 0.15 lets noise through (HALLU risk) |
  | rerank_cliff_min_keep        | 8          | 1                   | Cliff filter already preserves at least one chunk; min_keep=8 forces 8 weak chunks past the rerank gate, defeating cliff cutoff |
  | rrf_k                        | 30         | 60                  | RRF k=60 is the canonical "Cormack 2009" default; k=30 was a tuning experiment that never made it into the constants tier |
  | grounding_check_threshold    | 0.5        | 0.3                 | KEEP DB at 0.5 — alembic 0115 explicitly tuned tighter for HALLU=0 sacred; constants kept the permissive em-of-line default |

  We DO NOT touch grounding_check_threshold — 0.5 is the post-0115
  explicit operator decision (load-test verified).

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Aligns DB SSoT with code-tested floor (no surprise drift on restart)
  ✅ Per-bot override remains tier 1+2 (owners can tune via plan_limits)
  ✅ Reversible (downgrade restores prior values)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0139"
down_revision: str | None = "0138"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# (key, new_value, old_value_for_downgrade, value_type)
_ALIGNMENTS: tuple[tuple[str, str, str, str], ...] = (
    ("reranker_min_score_active", "0.30", "0.15", "float"),
    ("rerank_cliff_min_keep",     "1",    "8",    "int"),
    ("rrf_k",                     "60",   "30",   "int"),
)


def upgrade() -> None:
    """Align stale DB rows with the constants tier values."""
    for key, new_val, _old_val, _vtype in _ALIGNMENTS:
        op.execute(
            text(
                """
                UPDATE system_config
                SET value = CAST(:v AS jsonb),
                    updated_at = NOW()
                WHERE key = :k
                """,
            ).bindparams(k=key, v=new_val),
        )


def downgrade() -> None:
    """Restore the pre-alignment DB values."""
    for key, _new_val, old_val, _vtype in _ALIGNMENTS:
        op.execute(
            text(
                """
                UPDATE system_config
                SET value = CAST(:v AS jsonb),
                    updated_at = NOW()
                WHERE key = :k
                """,
            ).bindparams(k=key, v=old_val),
        )
