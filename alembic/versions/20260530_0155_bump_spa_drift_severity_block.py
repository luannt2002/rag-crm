"""Bump test-spa-id drift_detection severity warn → block.

Revision: 0155
Prev:     0154

Trigger (2026-05-30 fresh multi-turn rerun showed 15% HALLU regression):
  Turn 7 / 8 of spa_booking_drift flow had price flip-flop (BP-2)
  + cross-service feature borrow (BP-3). Alembic 0152 set spa
  drift_detection.severity_default = "warn" (audit only). After owner
  verifies pattern stable on UI test, escalate to "block" so existing
  Phase 3 GuardrailBlocked → OOS refuse flow handles (no app-override).

Sacred-rule alignment:
  ✅ Per-bot scope (only test-spa-id)
  ✅ Pure alembic UPDATE
  ✅ Reversible
  ✅ NO app-override answer — block raises GuardrailBlocked, existing
     OOS flow substitutes bots.oos_answer_template literal (single
     source of truth = bot owner's template)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0155"
down_revision: str | None = "0154"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_ACTION_CONFIG_BLOCK = """{
  "enabled": true,
  "slots_schema": {
    "booking": {
      "required": ["service", "name", "phone", "datetime"],
      "optional": ["note"],
      "service_lock_after_turn": 1
    }
  },
  "drift_detection": {
    "service_name": "block",
    "service_price": "block",
    "severity_default": "warn"
  }
}"""


_ACTION_CONFIG_WARN = """{
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
    """Bump spa drift_detection.{service_name, service_price} → block."""
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
        ).bindparams(cfg=_ACTION_CONFIG_BLOCK),
    )


def downgrade() -> None:
    """Revert to warn (audit only)."""
    op.execute(
        text(
            """
            UPDATE bots
            SET action_config = CAST(:cfg AS jsonb),
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND channel_type = 'web'
            """,
        ).bindparams(cfg=_ACTION_CONFIG_WARN),
    )
