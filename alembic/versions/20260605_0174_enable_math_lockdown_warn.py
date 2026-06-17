"""Activate math-lockdown guardrail in WARN mode (numeric grounding).

Revision: 0174
Prev:     0173

The math-lockdown guardrail (wired into guard_output in an earlier phase, now
extended to catch bare Vietnamese dotted amounts and document-citation numbers)
flags any number / price / citation in the answer that is absent from every
retrieved chunk — the parametric-override failure mode (e.g. legal penalty
written 3.000.000 where the document says 4.000.000; fabricated 16/2018 where
the document says 18/2018).

Enabled at severity WARN (not block) platform-wide: it appends a guardrail flag
for observability WITHOUT changing the answer, so we can measure the
false-positive rate (legitimate computed aggregates that aren't verbatim in a
chunk will also flag) before deciding per-bot block for copy-only legal bots.
Sacred-rule 10: warn only annotates; even at block it would substitute the
bot's own oos_answer_template, never platform text.

Rule 7 (config via alembic). Reversible.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0174"
down_revision: str | None = "0173"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(text("""
        INSERT INTO system_config (key, value, updated_at)
        VALUES ('math_lockdown_enabled', 'true'::jsonb, NOW())
        ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb, updated_at = NOW();
    """))
    op.execute(text("""
        INSERT INTO system_config (key, value, updated_at)
        VALUES ('math_lockdown_severity', '"warn"'::jsonb, NOW())
        ON CONFLICT (key) DO UPDATE SET value = '"warn"'::jsonb, updated_at = NOW();
    """))


def downgrade() -> None:
    op.execute(text("""
        UPDATE system_config SET value = 'false'::jsonb, updated_at = NOW()
        WHERE key = 'math_lockdown_enabled';
    """))
