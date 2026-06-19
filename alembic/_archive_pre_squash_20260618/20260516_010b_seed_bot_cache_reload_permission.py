"""[T2-CostPerf] seed module_permission row for bot:cache_reload

Revision ID: 010b
Revises: 010a
Create Date: 2026-05-16

Per SECURITY_AUDIT_20260516 AUTH-5 (HIGH):
  /api/ragbot/admin/bots/cache/reload + /api/ragbot/admin/bots/cache/status
  endpoints had no ``require_permission_dep("bot", "cache_reload")``
  decorator. Adding the decorator without seeding the permission row
  would cause ``require_permission`` (rbac.py:131) to deny-by-default
  with ``"Permission bot:cache_reload not configured"``.

This migration seeds the row at level 60 (same as ``bot:create``) so the
decorator can be enabled in a follow-up commit on admin_bots.py.

Idempotent: ON CONFLICT DO NOTHING. Safe to re-apply.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "010b"
down_revision = "010a"
branch_labels = None
depends_on = None


_UPSERT_SQL = text(
    """
    INSERT INTO module_permissions (module, permission, min_role_level, description)
    VALUES (
        'bot', 'cache_reload', 60,
        'Reload bot registry cache (force re-fetch from DB on next request)'
    )
    ON CONFLICT (module, permission) DO NOTHING
    """
)

_DELETE_SQL = text(
    "DELETE FROM module_permissions WHERE module = 'bot' AND permission = 'cache_reload'"
)


def upgrade() -> None:
    op.execute(_UPSERT_SQL)


def downgrade() -> None:
    op.execute(_DELETE_SQL)
