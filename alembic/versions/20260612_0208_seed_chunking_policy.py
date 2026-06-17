"""Seed platform chunking policy (config-driven chunking, Phase A).

Adds ``system_config.chunking_policy`` — a JSONB object the ingest path
resolves through ``shared.chunking_policy.resolve_chunking_policy`` (per-bot
``plan_limits.chunking_config`` > this platform default > constants).

Seeded value is BEHAVIOUR-NEUTRAL: ``table_strategy = "table_csv"`` reproduces
today's row-as-chunk behaviour exactly. Flip to ``"table_dual_index"`` here
(platform-wide) or per-bot via ``plan_limits.chunking_config`` AFTER re-ingest
validation, to add whole-table group chunks for aggregation queries.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0208"
down_revision = "0207"
branch_labels = None
depends_on = None

_KEY = "chunking_policy"
_VALUE = {"table_strategy": "table_csv"}


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO system_config (key, value) "
        "VALUES (:k, CAST(:v AS jsonb)) "
        "ON CONFLICT (key) DO NOTHING"
    ), {"k": _KEY, "v": json.dumps(_VALUE)})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM system_config WHERE key = :k"
    ), {"k": _KEY})
