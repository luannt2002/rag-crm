"""Enable two measured cost-win pipeline flags by default (Phase 4).

Phase 4 A/B (2026-06-19, scripts/ab_flags_20260619.py + ab_cascade_20260619.py)
measured these flags on the 3 test bots with bypass_cache, verifying answers
stayed quality-neutral (HALLU traps still refuse, factoid/superlative/range
answers unchanged):

  - pipeline_multi_query_speculative_enabled: cost -21.1%, p50 latency -779ms.
    Fans out the multi-query paraphrase LLM call in parallel with understand
    so the retrieve node reuses cached variants (no extra serial call) for
    multi-hop / synthesis intents; cancelled (no orphan) for intents that do
    not consume MQ.
  - adaptive_context_enabled: cost -18.3%, p50 latency -619ms. Prunes weak
    chunks after rerank when the top score clears the floor, shrinking the
    LLM context (fewer input tokens). aggregation/comparison intents are
    exempt (need wide context) per DEFAULT_ADAPTIVE_CONTEXT_EXEMPT_INTENTS.

Both shipped OFF (DEFAULT_* = False) pending the A/B. This migration writes the
system-wide default ON; per-bot opt-out remains via bots.plan_limits
(precedence: bot column > plan_limits > system_config > schema default), so a
bot that regresses can be flipped back without a redeploy.

Caveat (rule #0): the A/B was n=1 per case (directional, not multi-iteration
rigorous). Monitor token_ledger cost + the HALLU-trap load-test after rollout;
downgrade restores the prior absent-key state (constant default OFF).

Idempotent ON CONFLICT (key) DO UPDATE so re-running is a no-op.

Revision ID: phase4_costwin_20260619
Revises: squash_base_20260618
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "phase4_costwin_20260619"
down_revision = "squash_base_20260618"
branch_labels = None
depends_on = None


_FLAG_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "pipeline_multi_query_speculative_enabled",
        "true",
        "bool",
        "Phase 4 A/B win (-21% cost, -779ms): fan out the multi-query "
        "paraphrase in parallel with understand so retrieve reuses cached "
        "variants for multi-hop/synthesis; cancelled for non-consuming intents.",
    ),
    (
        "adaptive_context_enabled",
        "true",
        "bool",
        "Phase 4 A/B win (-18% cost, -619ms): prune weak chunks after rerank "
        "when top score clears the floor (fewer LLM context tokens). "
        "aggregation/comparison exempt (need wide context).",
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _FLAG_ROWS:
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, :value, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    """Restore the prior state (keys absent -> constant default OFF)."""
    op.execute(
        text(
            "DELETE FROM system_config WHERE key IN "
            "('pipeline_multi_query_speculative_enabled', 'adaptive_context_enabled')"
        )
    )
