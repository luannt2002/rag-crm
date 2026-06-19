"""Align dead sub-task bindings to the actually-running model (mini).

Finding (2026-06-12): the ``multi_query`` and ``decompose`` query-graph nodes
read their model DIRECTLY from system_config (``multi_query_model`` /
``decomposer.model`` = gpt-4.1-mini) — NOT from ``bot_model_bindings``. The
nano bindings for these purposes are therefore DEAD/orphan and merely
mislead (a doc audit read them as the live model). Repoint them to mini so
binding == config == actual runtime (no behavioural change; the binding is
not consumed by these nodes). Reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "0207"
down_revision = "0206"
branch_labels = None
depends_on = None

_ORPHAN_PURPOSES = ("multi_query", "decompose")


def _repoint(conn, model_name: str) -> None:
    mid = conn.execute(sa.text(
        "SELECT id FROM ai_models WHERE name = :n AND enabled = true"
    ), {"n": model_name}).scalar()
    if mid is None:
        return
    conn.execute(sa.text(
        "UPDATE bot_model_bindings SET record_model_id = :mid "
        "WHERE purpose = ANY(:p) AND active = true"
    ), {"mid": mid, "p": list(_ORPHAN_PURPOSES)})


def upgrade() -> None:
    _repoint(op.get_bind(), "gpt-4.1-mini")


def downgrade() -> None:
    _repoint(op.get_bind(), "gpt-4.1-nano")
