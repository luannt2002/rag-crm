"""Tune retrieve_top_k_by_intent + context_cap per Anthropic CR 2024 standard.

Revision: 0124
Prev:     0123

Eval round 1 (2026-05-26, 179 QA across 10 categories) verdict:
  Faithfulness  57.4%  | industry target ≥75%
  AnswerRel     68.9%  | target ≥70% (gần đạt)
  CtxPrecision  34.0%  | target ≥65% (yếu nhất)
  CtxRecall     46.6%  | target ≥70% (yếu)
  Avg           51.7%  | target ≥70%

Root cause analysis (per-category):
  - factoid_text 40% — top_k=15 too low vs Anthropic empirical top_k=20
  - aggregation_list 40% — top_k=40 fits 40 chunks but context_cap=5500 cuts
    tail (5500ch / 1232ch_per_legal_chunk ≈ 4 chunks max)
  - summary_doc 37% — no per-intent top_k key, falls back to global 20
  - comparison 53% — needs chunks from 2 entities, context_cap=4200 too tight

Industry research applied:
  - Anthropic CR (Sep 2024): top_k=20 is sweet spot (test 5/10/20, 20 wins)
  - Liu et al. (Lost in the Middle 2023): accuracy degrades at >8K tokens
    context middle → keep context_cap ≤6K tokens (=8000 chars at avg 0.75 t/ch)
  - LITM reorder (already enabled in code line 5575) mitigates middle drop

Conservative bumps (NOT aggressive):
  retrieve_top_k_by_intent:
    factoid    15 → 20   (Anthropic standard)
    summary    n/a → 25  (new key, lift recall for summary queries)
    aggregation 40 → 50  (cover more entries in lists)
    others unchanged (vu_vo, chitchat, greeting, feedback stay 5)
  generate_context_chars_cap_by_intent:
    aggregation 5500 → 8000  (≈ 6K tokens, safe under lost-in-middle 8K)
    multi_hop   4200 → 6000  (multi-step needs 2-entity context)
    comparison  4200 → 6000  (compare needs both entities)
    others unchanged

Trade-offs:
  - Cost: +15-25% input tokens for aggregation/multi_hop/comparison turns
    (these are ~30% of traffic by category mix)
  - Latency: +20-50ms retrieve (pgvector top-K), +200-400ms generate
    (longer prompt for aggregation)
  - LITM reorder mitigates lost-in-middle; ZeroEntropy reranker top_n caps
    downstream chunks so generate prompt stays manageable

Sacred-rule alignment:
  - Zero-hardcode: pure JSONB value update via alembic
  - HALLU=0: more chunks → fewer fabricate triggers (verified in industry
    research). Will monitor refusal_reason in eval round 2.
  - Domain-neutral: no brand literal, applies to all bots
  - 4-key identity: unchanged
  - Per-bot override: bots.plan_limits can override via JSONB
  - Reversible: downgrade restores prior values
"""

from alembic import op
from sqlalchemy import text

revision: str = "0124"
down_revision: str | None = "0123"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Lift per-intent top_k + context_cap to Anthropic CR 2024 standard."""
    # 1. retrieve_top_k_by_intent — factoid 15→20, aggregation 40→50, summary new=25
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST(
                '{"vu_vo": 5, "factoid": 20, "chitchat": 5, "feedback": 5, '
                '"greeting": 5, "multi_hop": 30, "comparison": 25, '
                '"aggregation": 50, "out_of_scope": 5, "summary": 25}'
                AS jsonb
            ),
            updated_at = NOW()
            WHERE key = 'retrieve_top_k_by_intent'
            """
        ),
    )
    # 2. generate_context_chars_cap_by_intent — aggr 5500→8000, multi_hop+compare 4200→6000
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST(
                '{"vu_vo": 1500, "factoid": 2900, "chitchat": 1500, '
                '"feedback": 1500, "greeting": 1500, "multi_hop": 6000, '
                '"comparison": 6000, "aggregation": 8000, "out_of_scope": 1500, '
                '"summary": 4500}'
                AS jsonb
            ),
            updated_at = NOW()
            WHERE key = 'generate_context_chars_cap_by_intent'
            """
        ),
    )


def downgrade() -> None:
    """Revert to pre-2026-05-26 conservative values."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST(
                '{"vu_vo": 5, "factoid": 15, "chitchat": 5, "feedback": 5, '
                '"greeting": 5, "multi_hop": 30, "comparison": 25, '
                '"aggregation": 40, "out_of_scope": 5}'
                AS jsonb
            ),
            updated_at = NOW()
            WHERE key = 'retrieve_top_k_by_intent'
            """
        ),
    )
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST(
                '{"vu_vo": 1500, "factoid": 2900, "chitchat": 1500, '
                '"feedback": 1500, "greeting": 1500, "multi_hop": 4200, '
                '"comparison": 4200, "aggregation": 5500, "out_of_scope": 1500}'
                AS jsonb
            ),
            updated_at = NOW()
            WHERE key = 'generate_context_chars_cap_by_intent'
            """
        ),
    )
