"""Fix prompt_injection regex DAN word boundary (false positive on 'Pin Daniel').

Revision: 0164
Prev:     0163

Trigger:
  Smoke test Plan 260604-metadata-aware-v4 phát hiện guardrail rule
  ``prompt_injection`` regex chứa literal ``DAN`` (DAN = Do Anything Now
  jailbreak technique) KHÔNG có word boundary. Match false positive
  "Pin **DAN**iel" → block legitimate chemistry query.

Fix:
  ``DAN`` → ``\\bDAN\\b`` (require word boundary, only match exact uppercase DAN).

Sacred-rule alignment:
  ✅ Pure alembic DML (CLAUDE.md rule 7)
  ✅ Reversible — downgrade restores original pattern
  ✅ Domain-neutral fix (regex correctness, không ưu tiên bot riêng)
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0164"
down_revision: str | None = "0163"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_OLD_PATTERN = (
    r"(ignore previous|disregard (all |the )?instructions"
    r"|system prompt|you are now|DAN|base64:|decode this)"
)

_NEW_PATTERN = (
    r"(ignore previous|disregard (all |the )?instructions"
    r"|system prompt|you are now|\bDAN\b|base64:|decode this)"
)


def upgrade() -> None:
    """Apply word boundary around DAN to prevent 'Pin Daniel' false positive."""
    op.execute(
        text(
            """
            UPDATE guardrail_rules
            SET pattern = :new_pattern,
                updated_at = NOW()
            WHERE rule_id = 'prompt_injection'
              AND pattern = :old_pattern
            """,
        ).bindparams(
            old_pattern=_OLD_PATTERN,
            new_pattern=_NEW_PATTERN,
        ),
    )


def downgrade() -> None:
    """Restore original pattern (without word boundary)."""
    op.execute(
        text(
            """
            UPDATE guardrail_rules
            SET pattern = :old_pattern,
                updated_at = NOW()
            WHERE rule_id = 'prompt_injection'
              AND pattern = :new_pattern
            """,
        ).bindparams(
            old_pattern=_OLD_PATTERN,
            new_pattern=_NEW_PATTERN,
        ),
    )
