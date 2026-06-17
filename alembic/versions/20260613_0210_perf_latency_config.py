"""Latency config: disable generate structured-output + async grounding.

Phase 1 expert-perf (measured: generate = 42% latency budget, p95 28s).
1. ``generate_use_structured_output=false`` — the JSON-mode structured path
   buffers the whole response server-side (defeats TTFT) and constrains decode;
   free-form generate streams + is faster. Citations are recovered post-hoc by
   the citation regex, so HALLU/citation behaviour is preserved.
2. ``grounding_check_async_intents`` widened to aggregation/comparison/multi_hop/
   range_query so the anti-HALLU grounding judge runs OFF the critical path for
   list/aggregation answers (still runs, still logs breaches — just not blocking).

BOTH are reversible. Re-ingest NOT required. A load-test MUST confirm p95 drop +
recall held + HALLU=0 before treating as shipped (rule#0 no-guess).
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0210"
down_revision = "0209"
branch_labels = None
depends_on = None

_NEW = {
    "generate_use_structured_output": json.dumps(False),
    "grounding_check_async_intents": json.dumps(
        ["factoid", "aggregation", "comparison", "multi_hop", "range_query"]
    ),
}
_OLD = {
    "generate_use_structured_output": json.dumps(True),
    "grounding_check_async_intents": json.dumps(["factoid"]),
}


def _apply(values: dict) -> None:
    conn = op.get_bind()
    for k, v in values.items():
        conn.execute(sa.text(
            "INSERT INTO system_config (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ), {"k": k, "v": v})


def upgrade() -> None:
    _apply(_NEW)


def downgrade() -> None:
    _apply(_OLD)
