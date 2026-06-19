"""Seed conversation-state memory config: TTL 24h + max 5 slots.

Action-state (slot-fill booking memory) now self-clears after 24h of
inactivity (``conversations.last_message_at``) and is capped to 5 slots
(anti-bloat). Both runtime-tunable here; defaults mirror constants
``DEFAULT_CONVERSATION_STATE_TTL_HOURS`` / ``DEFAULT_MAX_ACTION_SLOTS``.
"""
import sqlalchemy as sa
from alembic import op

revision = "0206"
down_revision = "0205"
branch_labels = None
depends_on = None

_SEED = {
    "conversation_state_ttl_hours": "24",
    "conversation_state_max_slots": "5",
}


def upgrade() -> None:
    conn = op.get_bind()
    for key, val in _SEED.items():
        conn.execute(sa.text(
            "INSERT INTO system_config (key, value) "
            "VALUES (:k, to_jsonb(CAST(:v AS integer))) "
            "ON CONFLICT (key) DO NOTHING"
        ), {"k": key, "v": val})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM system_config WHERE key = ANY(:keys)"
    ), {"keys": list(_SEED.keys())})
