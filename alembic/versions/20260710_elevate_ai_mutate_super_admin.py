"""Elevate platform-shared AI-mutate gates from tenant (80) to super_admin (100).

``ai_providers`` and ``ai_models`` rows carry NO ``record_tenant_id`` — they are
platform-shared resources. With the mutate gates at level 80 (tenant admin), a
tenant admin editing provider/model X transparently affects EVERY other tenant
pointing at the same row (shared cost, shared trust, shared abuse posture). Read
and test-call gates stay at admin (60) — reads do not change shared state.

This was authored long ago (``scripts/seed_rbac_permissions_s12a.py``) but never
applied to the live DB (verified 2026-07-10: all 7 gates still at 80). Shipping it
as a tracked migration so the elevation reproduces on every DB, per sacred #7
(DB content state only via alembic, never a run-once script).

Idempotent: only rows currently BELOW super_admin are raised, so a re-run or an
already-elevated DB is a no-op. Downgrade restores the tenant level.

Revision ID: elevate_ai_mutate_super_admin_260710
Revises: seed_module_permissions_rbac_260710
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from ragbot.shared.constants import (
    DEFAULT_SUPER_ADMIN_LEVEL,
    DEFAULT_TENANT_ADMIN_LEVEL,
)

revision = "elevate_ai_mutate_super_admin_260710"
down_revision = "seed_module_permissions_rbac_260710"
branch_labels = None
depends_on = None

# The 7 platform-shared AI-mutate gates (module='ai').
_ELEVATED_PERMISSIONS: list[str] = [
    "provider_create",
    "provider_update",
    "provider_delete",
    "provider_rotate_key",
    "model_create",
    "model_update",
    "model_delete",
]


def upgrade() -> None:
    stmt = text(
        """
        UPDATE module_permissions
        SET min_role_level = :super
        WHERE module = 'ai' AND permission = :perm
          AND min_role_level < :super
        """
    )
    for perm in _ELEVATED_PERMISSIONS:
        op.execute(stmt.bindparams(super=DEFAULT_SUPER_ADMIN_LEVEL, perm=perm))


def downgrade() -> None:
    stmt = text(
        """
        UPDATE module_permissions
        SET min_role_level = :tenant
        WHERE module = 'ai' AND permission = :perm
          AND min_role_level = :super
        """
    )
    for perm in _ELEVATED_PERMISSIONS:
        op.execute(
            stmt.bindparams(
                tenant=DEFAULT_TENANT_ADMIN_LEVEL,
                super=DEFAULT_SUPER_ADMIN_LEVEL,
                perm=perm,
            )
        )
