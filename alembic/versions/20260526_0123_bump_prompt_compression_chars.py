"""Bump prompt_compression_max_chars_per_chunk 500 → 2000 + raise context_cap.

Revision: 0123
Prev:     0122

Bug 2026-05-26: bot ``tessss`` answered "Điều 16 có 2 mục" while corpus
chunk_index=26 (rank-0 retrieve, score=0.877) contains all 6 subsections
of Điều 16 in 1251 chars. Trace:

  prompt_compression.max_chars_per_chunk = 500   ← truncates 1251 → 500
  prompt_build.context_chars_dropped     = 12637 ← 60% of 21170 lost
  generate sees only "1. Xác định trách nhiệm" + "2. Yêu cầu bàn giao"
  → bot answers "2 mục" because that's all it can see.

Root cause: 500-char cap was calibrated for short factoid corpora
(prices, FAQ snippets). Legal-text bots have 1-2KB per Điều / clause —
the cap silently shears the tail of every Điều, breaking enumeration.

Fix: bump default ``prompt_compression_max_chars_per_chunk`` 500 → 2000.
Each Điều / clause / chunk now fits whole. Context cap also lifted from
5500 → 12000 chars to give the generate prompt room for ~6 full clauses
without truncation.

Trade-off: prompt token cost rises ~3x on legal-text turns (3000 → 9000
input tokens roughly). For factoid bots (test-spa-id) the per-chunk cap
is still well above chunk size (~500 chars) so behaviour is unchanged.
Output token quota for generate already at 1000 (alembic 0120) so the
answer side is unaffected.

Sacred rules:
  - HALLU=0 preserved (more grounded context = less hallucination room)
  - Domain-neutral (no brand literal, no per-bot tuning)
  - Reversible via downgrade
"""

from alembic import op
from sqlalchemy import text

revision: str = "0123"
down_revision: str | None = "0122"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Lift compression cap to 2000 + context cap to 12000."""
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'prompt_compression_max_chars_per_chunk',
                CAST('2000' AS jsonb),
                'int',
                'Per-chunk truncation cap during prompt build (legal/long-form bots need 2000 to fit a single Điều / clause).',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = NOW();
            """
        ),
    )
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'prompt_context_cap_chars',
                CAST('12000' AS jsonb),
                'int',
                'Total context budget in chars before truncation. Lifted from 5500 to fit ~6 legal clauses without shearing.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = NOW();
            """
        ),
    )


def downgrade() -> None:
    """Revert compression cap to 500 + drop context_cap override."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST('500' AS jsonb), updated_at = NOW()
            WHERE key = 'prompt_compression_max_chars_per_chunk';
            """
        ),
    )
    op.execute(
        text(
            """
            DELETE FROM system_config WHERE key='prompt_context_cap_chars';
            """
        ),
    )
