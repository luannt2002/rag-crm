"""Add ``bots.action_config`` + ``conversations.action_state`` JSONB columns.

Revision: 0150
Prev:     0149

Trigger (2026-05-30 X2 BUNDLED Tier 2 ship step 3):
  Multi-turn integration test 2026-05-30 baseline confirms 3/20 (15%)
  HALLU on test-spa-id ``spa_booking_drift`` flow:
    - BP-1: service name fusion ("chăm sóc da chuyên sâu thải độc"
            fabricate from 2 separate corpus entries)
    - BP-2: price flip-flop (199K↔800K cho cùng service across turns)
    - BP-3: feature cross-service (PAYOT/Gym Beauté gán cho Thải độc da
            thực là của Detox Ballet)

  Root cause: bot RAG stateless qua turns. Mỗi turn LLM re-infer state
  từ prose history → bias top-chunk current turn → drift.

Schema additions:

  ``bots.action_config`` JSONB:
    - Per-bot OPT-IN action extraction + state tracking config.
    - Default ``{}`` → state tracking OFF → bot behavior unchanged.
    - Owner declare slot_schema + drift_detection per-bot via admin UI.
    - Example for test-spa-id (alembic 0152):
        {
          "enabled": true,
          "slots_schema": {
            "booking": {
              "required": ["service", "name", "phone", "datetime"],
              "service_lock_after_turn": 1
            }
          },
          "drift_detection": {
            "service_name": "exact_match",
            "service_price": "exact_match"
          }
        }

  ``conversations.action_state`` JSONB:
    - Per-conversation RUNTIME state. RLS workspace-aware (inherits from
      conversations.workspace_id via alembic 0141).
    - Default ``{}`` → no state tracked.
    - Populated by orchestration node ``extract_and_validate_slots``
      (Tier 2 ship step 8). Read on next turn so LLM sees state via
      sysprompt rule 20 STATE_ENFORCEMENT (platform-default tier).
    - Example structure:
        {
          "intent": "booking",
          "service_locked": {
            "name": "chăm sóc da chuyên sâu",
            "price_buoi_le": 199000,
            "source_chunk_id": "f59c921d",
            "locked_at_turn": 4
          },
          "slots_filled": {
            "name": "Luân",
            "phone": "0353988280",
            "datetime": "sáng thứ 7"
          },
          "drift_attempts": []
        }

Sacred-rule alignment:
  ✅ Domain-neutral schema (no spa/medispa columns)
  ✅ Per-bot opt-in (default {} = OFF)
  ✅ Per-conversation scope (RLS inherits tenant + workspace)
  ✅ Owner self-service (admin UI edits JSONB)
  ✅ Reversible (downgrade drops columns)
  ✅ CLAUDE.md rule 7 (pure alembic DDL)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0150"
down_revision: str | None = "0149"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add JSONB columns for Tier 2 action extraction + state tracking."""
    op.add_column(
        "bots",
        sa.Column(
            "action_config",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "Per-bot action extraction config. Schema: "
                "{enabled: bool, slots_schema: {...}, drift_detection: {...}}. "
                "Default {} = OFF (bot behavior unchanged). Owner declares "
                "via admin UI."
            ),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "action_state",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "Per-conversation runtime state. Schema: "
                "{intent, service_locked: {...}, slots_filled: {...}}. "
                "RLS inherits from conversations.workspace_id (alembic 0141)."
            ),
        ),
    )


def downgrade() -> None:
    """Drop Tier 2 columns (rollback to pre-X2 state)."""
    op.drop_column("conversations", "action_state")
    op.drop_column("bots", "action_config")
