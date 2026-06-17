"""[T3-Refactor] merge multi-head 0107c + 0109 into single head 010a

Revision ID: 010a
Revises: 0107c, 0109
Create Date: 2026-05-16

Context (multi-head root cause):
  Wave C coders ran in parallel:
    - C2 (G12) authored 0107c with ``down_revision="0107b"``
    - C1 (G14) authored 0108 with ``down_revision="0107b"``
    - C3 (G15) authored 0109 with ``down_revision="0108"``

  Both 0107c and 0108 chained from the SAME parent 0107b, so after the
  C1+C2+C3 merges landed on main, ``alembic heads`` reports two heads:
  ``0107c`` and ``0109`` (0109 inherits from 0108). ``alembic upgrade
  head`` fails with "Multiple head revisions" until the heads are merged.

Fix: empty merge revision that lists both parents in ``down_revision``.
No DDL — just rejoins the DAG into a single head ``010a`` so the chain
is linear again.

This is a SCHEMA-NEUTRAL merge migration (alembic merge -m). The
upgrade and downgrade functions are intentionally no-ops; alembic
walks both parents during ``upgrade head`` automatically.
"""
from __future__ import annotations


revision = "010a"
down_revision = ("0107c", "0109")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: merge migration only rejoins the DAG."""


def downgrade() -> None:
    """No-op: merge migration only rejoins the DAG."""
