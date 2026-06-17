"""Per-tenant CORS strict whitelist — tenants.allowed_origins JSONB column.

V9.1.1 — production CORS hardening. Each tenant carries a list of origins
(exact host or wildcard ``https://*.tenant.example``) that browsers may
emit cross-origin requests from. Empty list = block all (deny by default).

Caller-facing impact:
- Admin PATCH /admin/tenants/{id} grows an ``allowed_origins`` field.
- ``CORSPerTenantMiddleware`` reads this column (cached via
  ``TenantConfigCache``) and matches the request ``Origin`` header before
  emitting ``Access-Control-Allow-Origin`` / returning 204 for preflight.
- Routes pre-tenant-context (``/health``, ``/metrics``, ``/static/*``,
  Swagger) keep using the global ``APP_CORS_ALLOWED_ORIGINS`` env list.

Idempotent: column-existence check at the top of ``upgrade()`` / bottom of
``downgrade()``.

Revision ID: 0059
Revises: 0058
Create Date: 2026-05-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    res = op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return res is not None


def upgrade() -> None:
    if _column_exists("tenants", "allowed_origins"):
        # Already migrated — no-op.
        return
    op.execute(
        """
        ALTER TABLE tenants
            ADD COLUMN allowed_origins JSONB NOT NULL DEFAULT '[]'::jsonb;
        """
    )


def downgrade() -> None:
    if not _column_exists("tenants", "allowed_origins"):
        return
    op.execute(
        """
        ALTER TABLE tenants DROP COLUMN allowed_origins;
        """
    )
