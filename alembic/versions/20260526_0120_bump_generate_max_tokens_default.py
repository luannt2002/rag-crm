"""Bump generate_max_tokens_by_intent.default 500 → 1000.

Revision: 0120
Prev:      0119

User request 2026-05-26: lift default output cap from 500 → 1000 so intents
that the heuristic classifier tags as ``unknown`` / ``None`` (queries that
don't match any of the 9 enumerated patterns) get the same room as
explicitly-tagged factoid / aggregation / comparison / multi_hop (already
at 1000) instead of being silently truncated to 500.

Why this matters: load-test telemetry shows 70-80% of real traffic
classifies as ``unknown`` because the heuristic patterns are narrow by
design (sacred domain-neutral rule — no VN-specific literals beyond a
small core). Cap at 500 risks chopping long range_query lists, multi-doc
summaries, and any answer where the LLM picks up nuance the classifier
missed. Bump to 1000 aligns the default with the explicit-intent group.

Cost impact: at most +500 output tokens per affected turn. For the few
turns that actually need it. The vast majority of turns end well below
500 (load-test avg ~200 tok out), so the bump is a ceiling raise, not a
mean shift. HALLU=0 unaffected (output cap doesn't bypass the grounding
or guard_output checks).
"""

from alembic import op
from sqlalchemy import text

revision: str = "0120"
down_revision: str | None = "0119"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Set generate_max_tokens_by_intent.default = 1000."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = jsonb_set(
                CAST(value AS jsonb),
                '{default}',
                '1000'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE key = 'generate_max_tokens_by_intent'
            """
        ),
    )


def downgrade() -> None:
    """Revert generate_max_tokens_by_intent.default → 500."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = jsonb_set(
                CAST(value AS jsonb),
                '{default}',
                '500'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE key = 'generate_max_tokens_by_intent'
            """
        ),
    )
