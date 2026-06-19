"""[T1-Smartness] quotas: add documents-per-day quota columns

Revision ID: 010i
Revises: 010h
Create Date: 2026-05-18

Multi-tenant fairness — vấn đề 6C from upload-flow audit
(``reports/UPLOAD_FLOW_AUDIT_RAM_REDIS_20260516.md``).

The existing ``quotas`` table tracks **token usage** for chat:
``used_tokens`` increments per LLM call, ``monthly_limit`` caps it.
There is NO daily upload-count cap, so a tenant can flood
``POST /documents/upload`` and either:

1. Exhaust worker capacity (queue dominance → starves other tenants),
2. Bloat ``document_chunks`` table → degraded HNSW index quality
   for all tenants sharing the host.

This migration adds 3 columns to ``quotas`` so the
:class:`IngestQuotaService` can enforce a per-tenant per-day cap before
``INSERT INTO documents``:

- ``documents_per_day_limit`` — operator cap (default 1000/day; 0 = unlimited).
- ``documents_today_count`` — accumulator reset daily.
- ``documents_today_reset_at`` — UTC midnight rollover anchor.

Backfill: existing rows get the SSoT default cap from
``DEFAULT_DOCUMENTS_PER_DAY_LIMIT`` (1000) and a fresh reset anchor
(``now()`` rounded to UTC midnight). The :class:`IngestQuotaService`
checks ``reset_at < now()`` and rolls over the counter on the next
upload.

Tenant scope: RLS policy already in place on ``quotas`` (FORCE row
security; ``record_tenant_id = current_setting('app.tenant_id')``). New
columns inherit it — no policy change needed.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010i"
down_revision = "010h"
branch_labels = None
depends_on = None

_DEFAULT_DOCS_PER_DAY = 1000  # mirror DEFAULT_DOCUMENTS_PER_DAY_LIMIT


def upgrade() -> None:
    op.add_column(
        "quotas",
        sa.Column(
            "documents_per_day_limit",
            sa.Integer(),
            nullable=False,
            server_default=str(_DEFAULT_DOCS_PER_DAY),
        ),
    )
    op.add_column(
        "quotas",
        sa.Column(
            "documents_today_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "quotas",
        sa.Column(
            "documents_today_reset_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("date_trunc('day', now() AT TIME ZONE 'UTC') + interval '1 day'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("quotas", "documents_today_reset_at")
    op.drop_column("quotas", "documents_today_count")
    op.drop_column("quotas", "documents_per_day_limit")
