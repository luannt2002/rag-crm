"""[T1-Smartness] tune query_complexity.weight_numbers 0.3 → 0.6

Revision ID: 0103
Revises: 0102
Create Date: 2026-05-15

L1 Adaptive Router classifies queries as 'simple' or 'complex' via a
weighted sum (see ``src/ragbot/orchestration/nodes/query_complexity.py``).
``weight_numbers`` multiplies the count of integer tokens in the query.
Pre-tune (0.3), a 3-number compound query like "Điều 7, 29, 51" scored:

    score = 0.5 (commas) + 0 + 0.9 (3*0.3 numbers) + 0 + 0.2 (length/norm)
          = 1.6 — but the threshold is 1.2 so it DID flip complex.
    "Điều 38 và 3" scored:
    score = 0 + 0.4 (và) + 0.6 (2*0.3) + 0 + 0.2 = 1.2 — borderline; on
    the conjunction-less variant "Điều 38 3" the score drops to 0.8 →
    classified simple → decompose skipped.

Post-tune (0.6):

    "Điều 36"            → 0 + 0 + 0.6 + 0 + 0.1 = 0.7   → simple   (correct)
    "Điều 38 và 3"       → 0 + 0.4 + 1.2 + 0 + 0.2 = 1.8 → complex  (correct)
    "Điều 7, 29, 51"     → 0.5 + 0 + 1.8 + 0 + 0.2 = 2.5 → complex  (correct)
    "phí 100 đồng …"     → 0 + 0 + 0.6 + 0 + 0.25 = 0.85 → simple   (correct)
    "5 năm sau"          → 0 + 0 + 0.6 + 0 + 0.15 = 0.75 → simple   (correct)

The tune narrows the gap where numbers alone push a 2-3 entity query
into 'complex', without dragging single-number factoid queries over the
threshold. Reversible via downgrade.

Idempotent: the UPDATE has no effect if value is already 0.6.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


_UPDATE_SQL = text(
    """
    UPDATE system_config
       SET value = '0.6'::jsonb,
           description = :description,
           updated_at = now()
     WHERE key = 'query_complexity.weight_numbers'
       AND value::text != '0.6'
    """
)


_DOWNGRADE_SQL = text(
    """
    UPDATE system_config
       SET value = '0.3'::jsonb,
           description = :description,
           updated_at = now()
     WHERE key = 'query_complexity.weight_numbers'
       AND value::text != '0.3'
    """
)


_DESC_UP = (
    "L1 complexity weight on integer-token count. Raised 0.3 → 0.6 in "
    "alembic 0103 (2026-05-15) so 2-entity numeric queries cross the "
    "1.2 complexity threshold reliably. See plans/260515-multi-query-"
    "audit-fix/issues/issue-6-query-complexity-weight.md for derivation."
)


_DESC_DOWN = "L1 complexity weight on integer-token count."


def upgrade() -> None:
    op.execute(_UPDATE_SQL.bindparams(description=_DESC_UP))


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL.bindparams(description=_DESC_DOWN))
