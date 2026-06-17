"""Per-bot cliff min_keep for legal bots (downstream retrieval-miss fix).

Revision: 0180
Prev:     0179

Step-level forensic (request_steps funnel, evidence 2026-06-05) located the
thong-tu retrieval miss precisely — and it is NOT embedding/retrieval:

  - After 0179 + CR-prompt re-ingest, the Điều-56 "hiệu lực/thay thế" chunk is
    VECTOR rank 2/80 (cos .486) and BM25 rank 1/80 — it reaches the reranker.
  - rerank (zerank-2) mis-scores it: the issuance-date chunk gets 0.5629 while
    every other chunk (incl. the exact-answer Điều-56) clusters at ≤~0.11 — a
    79.7% gap (filter_min_score: cliff_max_gap=0.7974, cliff_reason="cliff").
  - The cliff filter with rerank_cliff_min_keep=1 then cuts 7→1, dropping the
    exact-answer chunk. generate sees only the wrong chunk → refuse/fabricate.

Root: the semantic reranker under-ranks exact-clause legal chunks that lexical
(BM25 rank-1) ranks correctly; the cliff's min_keep=1 amplifies that single
reranker error into a hard miss. Fix at the CORRECT layer (retrieval config,
not sysprompt): raise rerank_cliff_min_keep for the legal bots so the relevant
cluster survives the cliff and the exact-answer chunk reaches generation. The
key is per-bot (PLAN_LIMIT_SCHEMA, bot_limits.py:149) so blast radius is gated —
the 95%+ semantic bots keep min_keep=1.

Idempotent (jsonb_set). Reversible. Rule 7 (alembic).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0180"
down_revision: str | None = "0179"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOTS = ("thong-tu-09-2020-tt-nhnn", "luat-giao-thong")
_MIN_KEEP = 5


def upgrade() -> None:
    op.execute(
        text("""
            UPDATE bots
            SET plan_limits = jsonb_set(
                    COALESCE(plan_limits, '{}'::jsonb),
                    '{rerank_cliff_min_keep}', to_jsonb(CAST(:mk AS int)), true),
                updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(mk=_MIN_KEEP, bots=list(_BOTS))
    )


def downgrade() -> None:
    op.execute(
        text("""
            UPDATE bots
            SET plan_limits = plan_limits - 'rerank_cliff_min_keep', updated_at = NOW()
            WHERE bot_id = ANY(:bots)
        """).bindparams(bots=list(_BOTS))
    )
