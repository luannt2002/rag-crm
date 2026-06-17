"""Enable action_config for test-spa-id (Tier 2 opt-in).

Revision: 0152
Prev:     0151

Trigger (X2 BUNDLED ship step 11):
  Spa bot opted into multi-turn state tracking. Schema declares the
  booking flow slots. Other bots remain ``{}`` (Tier 2 OFF).

Sacred-rule alignment:
  ✅ Per-bot scope (only test-spa-id)
  ✅ Pure alembic UPDATE
  ✅ Reversible
  ✅ Owner self-service — bot owner can edit via admin UI in future
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0152"
down_revision: str | None = "0151"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_ACTION_CONFIG = """{
  "enabled": true,
  "slots_schema": {
    "booking": {
      "required": ["service", "name", "phone", "datetime"],
      "optional": ["note"],
      "service_lock_after_turn": 1
    }
  },
  "drift_detection": {
    "service_name": "exact_match",
    "service_price": "exact_match",
    "severity_default": "warn"
  }
}"""


def upgrade() -> None:
    """Enable action_config for test-spa-id."""
    op.execute(
        text(
            """
            UPDATE bots
            SET action_config = CAST(:cfg AS jsonb),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ).bindparams(cfg=_ACTION_CONFIG),
    )


def downgrade() -> None:
    """Disable spa action_config (back to {})."""
    op.execute(
        text(
            """
            UPDATE bots
            SET action_config = '{}'::jsonb,
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND channel_type = 'web'
            """,
        ),
    )
