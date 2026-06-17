"""Set gpt-4.1-mini as the platform default answer model.

Rationale (benchmark 2026-06-11): mini Faithfulness 0.960 ≈ full 0.965 at 5x lower
cost. Repoint system_config defaults + answer bindings from gpt-4.1 (full) to
gpt-4.1-mini and disable the full model (kept in ai_models for history; enabled=false).
nano stays available for low-stakes bots. Reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "0205"
down_revision = "0204"
branch_labels = None
depends_on = None

_ANSWER_PURPOSES = (
    "llm_primary", "generation", "llm_factoid", "llm_aggregation",
    "llm_comparison", "llm_multi_hop",
)


def upgrade() -> None:
    conn = op.get_bind()
    # 1) system_config defaults -> mini
    for key in ("default_answer_model", "llm_default_model"):
        conn.execute(sa.text(
            "INSERT INTO system_config (key, value) VALUES (:k, to_jsonb('gpt-4.1-mini'::text)) "
            "ON CONFLICT (key) DO UPDATE SET value = to_jsonb('gpt-4.1-mini'::text)"
        ), {"k": key})
    # 2) repoint answer bindings full -> mini
    mini = conn.execute(sa.text(
        "SELECT id FROM ai_models WHERE name='gpt-4.1-mini' AND enabled=true")).scalar()
    if mini is not None:
        conn.execute(sa.text(
            "UPDATE bot_model_bindings SET record_model_id=:mid "
            "WHERE purpose = ANY(:p) AND active=true"
        ), {"mid": mini, "p": list(_ANSWER_PURPOSES)})
    # 3) disable full (history preserved)
    conn.execute(sa.text("UPDATE ai_models SET enabled=false WHERE name='gpt-4.1'"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE ai_models SET enabled=true WHERE name='gpt-4.1'"))
    for key in ("default_answer_model", "llm_default_model"):
        conn.execute(sa.text(
            "INSERT INTO system_config (key, value) VALUES (:k, to_jsonb('gpt-4.1'::text)) "
            "ON CONFLICT (key) DO UPDATE SET value = to_jsonb('gpt-4.1'::text)"
        ), {"k": key})
    full = conn.execute(sa.text(
        "SELECT id FROM ai_models WHERE name='gpt-4.1'")).scalar()
    if full is not None:
        conn.execute(sa.text(
            "UPDATE bot_model_bindings SET record_model_id=:mid "
            "WHERE purpose = ANY(:p) AND active=true"
        ), {"mid": full, "p": list(_ANSWER_PURPOSES)})
