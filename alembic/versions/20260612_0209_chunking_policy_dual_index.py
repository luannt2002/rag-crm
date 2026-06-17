"""Roll out table_dual_index as the platform chunking policy.

Phase B/D rollout: flip ``system_config.chunking_policy.table_strategy`` from
the neutral ``table_csv`` (seeded in 0208) to ``table_dual_index`` so table /
CSV documents emit a whole-table group chunk ALONGSIDE the per-row chunks.
Fixes aggregation / "list-all" / min-max recall miss measured at the vector
stage (e.g. spa "liệt kê dịch vụ" → answer at rank 21, outside top-20).

Existing documents must be re-ingested (delete chunks + rechunk event) for
the new chunks to materialise. Reversible — downgrade restores table_csv.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0209"
down_revision = "0208"
branch_labels = None
depends_on = None

_KEY = "chunking_policy"
_NEW = {"table_strategy": "table_dual_index"}
_OLD = {"table_strategy": "table_csv"}


def _set(value: dict) -> None:
    op.get_bind().execute(sa.text(
        "INSERT INTO system_config (key, value) VALUES (:k, CAST(:v AS jsonb)) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    ), {"k": _KEY, "v": json.dumps(value)})


def upgrade() -> None:
    _set(_NEW)


def downgrade() -> None:
    _set(_OLD)
