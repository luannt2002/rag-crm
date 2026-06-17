"""Delete orphan math_lockdown system_config rows (P2-A/P2-E ↔️).

Revision: 0200
Prev:     0199

The app-side math override was removed in commit 6e9041d and its constants
in cad52dc — the answer pipeline no longer regex-checks or replaces numbers
(sacred #2/#5: the LLM's answer is what the user sees). But three
``system_config`` rows survived the code removal:

    math_lockdown_enabled = true
    math_lockdown_severity = "warn"
    default_math_lockdown_enabled = true

``grep math_lockdown_enabled src/`` = 0 readers — they are pure orphans. An
operator inspecting ``system_config`` would wrongly conclude an app-override
(a sacred violation) is ON. This deletes them so the DB reflects the code.

Sacred-rule alignment:
  - Pure DML via alembic (no psql hot-fix) — the sanctioned path to change
    DB content state.
  - Reversible — downgrade re-inserts the rows at their last-known values.
  - Domain-neutral.
"""
from __future__ import annotations

from alembic import op

revision: str = "0200"
down_revision: str | None = "0199"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_ORPHAN_KEYS = (
    "math_lockdown_enabled",
    "math_lockdown_severity",
    "default_math_lockdown_enabled",
)


def upgrade() -> None:
    op.execute(
        "DELETE FROM system_config WHERE key IN "
        "('math_lockdown_enabled', 'math_lockdown_severity', "
        "'default_math_lockdown_enabled')",
    )


def downgrade() -> None:
    # Restore the orphan rows at their pre-deletion values so the migration
    # is reversible. (They remain dead code-wise; this only round-trips DB
    # state.)
    op.execute(
        """
        INSERT INTO system_config (key, value) VALUES
            ('math_lockdown_enabled', 'true'),
            ('math_lockdown_severity', '"warn"'),
            ('default_math_lockdown_enabled', 'true')
        ON CONFLICT (key) DO NOTHING
        """,
    )
