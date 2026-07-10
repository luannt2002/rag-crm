"""Seed the full ``module_permissions`` RBAC set into alembic-tracked state.

Root cause (reproducibility gap): every ``module:permission`` gate was seeded by
two run-once scripts (``scripts/seed_rbac_permissions_s11b.py`` +
``…_s12a.py``) referenced only in ``REBUILD_DEV_DB_RUNBOOK.md``. A plain
``alembic upgrade head`` (CI, a DB clone, a dev following alembic convention)
therefore produced a DB with an EMPTY ``module_permissions`` table — and the RBAC
check fails CLOSED (``require_permission`` raises ``ForbiddenError`` when a
``module:permission`` row is absent), so EVERY gated route (chat, admin, ingest,
sync…) returned 403. This is exactly the run-once-script anti-pattern sacred #7
forbids: DB content state must live in a tracked migration, not a manual script.

This migration folds the complete set into alembic so ``alembic upgrade head``
alone fully provisions RBAC. The triples are a faithful snapshot of CURRENT
PRODUCTION state (verified row-for-row against the live ``module_permissions``
table on 2026-07-10, 45/45 exact match on ``(module, permission, min_role_level)``).

Levels come from the SSoT (``ROLE_LEVELS`` + ``DEFAULT_SERVICE_LEVEL``) — no
hardcoded tier numbers. Idempotent via ``ON CONFLICT (module, permission) DO
NOTHING``: on the already-seeded live DB this is a no-op AND it never clobbers a
level an admin later tuned; on a fresh DB it provisions all 45 rows.

NOTE (out of scope, flagged for the owner): ``scripts/…_s12a.py`` intended to
ELEVATE the 7 platform-shared AI-mutate gates (ai.provider_*/model_* create/
update/delete/rotate_key) from tenant (80) to super_admin (100) because
``ai_providers``/``ai_models`` carry no ``record_tenant_id``. That elevation was
never applied to the live DB (still 80). This migration deliberately snapshots
the live level (80) — it does NOT silently change security posture. The
elevation is a separate owner decision that should ship as its own migration.

Revision ID: seed_module_permissions_rbac_260710
Revises: seed_prompt_injection_vi_260710
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from ragbot.shared.constants import DEFAULT_SERVICE_LEVEL
from ragbot.shared.rbac import ROLE_LEVELS

revision = "seed_module_permissions_rbac_260710"
down_revision = "seed_prompt_injection_vi_260710"
branch_labels = None
depends_on = None

# Level names resolved from the SSoT so no tier integer is hardcoded here.
_VIEWER = ROLE_LEVELS["viewer"]        # 10
_USER = ROLE_LEVELS["user"]            # 20
_OPERATOR = ROLE_LEVELS["operator"]    # 40
_SERVICE = DEFAULT_SERVICE_LEVEL       # 50 — upstream service-token gate
_ADMIN = ROLE_LEVELS["admin"]          # 60
_TENANT = ROLE_LEVELS["tenant"]        # 80
_SUPER = ROLE_LEVELS["super_admin"]    # 100

# Complete RBAC gate set — (module, permission, min_role_level).
# Faithful snapshot of live production (45 rows). New gates ship as a new
# migration; this list is the fresh-DB provisioning SSoT.
MODULE_PERMISSION_SEED: list[tuple[str, str, int]] = [
    # ai module
    ("ai", "provider_read", _ADMIN),
    ("ai", "provider_create", _TENANT),
    ("ai", "provider_update", _TENANT),
    ("ai", "provider_delete", _TENANT),
    ("ai", "provider_test", _ADMIN),
    ("ai", "provider_rotate_key", _TENANT),
    ("ai", "model_read", _USER),
    ("ai", "model_create", _TENANT),
    ("ai", "model_update", _TENANT),
    ("ai", "model_delete", _TENANT),
    ("ai", "binding_read", _ADMIN),
    ("ai", "binding_create", _TENANT),
    ("ai", "binding_update", _TENANT),
    ("ai", "binding_delete", _TENANT),
    ("ai", "audit_read", _ADMIN),
    ("ai", "cache_reload", _ADMIN),
    ("ai", "cache_status", _ADMIN),
    ("ai", "effective_config_read", _ADMIN),
    # bot module
    ("bot", "list", _ADMIN),
    ("bot", "cache_status", _ADMIN),
    ("bot", "cache_reload", _ADMIN),
    # system module
    ("system", "metrics_overview", _ADMIN),
    ("system", "metrics_by_model", _ADMIN),
    ("system", "metrics_top_questions", _ADMIN),
    ("system", "metrics_steps", _ADMIN),
    ("system", "metrics_active_models", _ADMIN),
    # policy module
    ("policy", "capability_read", _ADMIN),
    ("policy", "capability_upsert", _TENANT),
    ("policy", "policy_read", _ADMIN),
    ("policy", "policy_upsert", _TENANT),
    # admin module
    ("admin", "audit_message_read", _ADMIN),
    ("admin", "audit_overview_read", _ADMIN),
    ("admin", "audit_query_detail_read", _ADMIN),
    # chat module (public + upstream-service gates)
    ("chat", "submit", _VIEWER),
    ("chat", "feedback", _VIEWER),
    ("chat", "stream", _SERVICE),
    # document module
    ("document", "ingest", _OPERATOR),
    ("document", "delete_by_tool_name", _ADMIN),
    ("document", "rechunk", _OPERATOR),
    # sync module (upstream service-token paths; RBAC fallback for humans)
    ("sync", "bot_upsert", _ADMIN),
    ("sync", "documents_upsert", _ADMIN),
    ("sync", "documents_list", _USER),
    ("sync", "documents_delete", _ADMIN),
    # tenant policy CRUD
    ("tenant", "policy_read", _TENANT),
    ("tenant", "policy_update", _SUPER),
]


def upgrade() -> None:
    stmt = text(
        """
        INSERT INTO module_permissions (module, permission, min_role_level)
        VALUES (:m, :p, :l)
        ON CONFLICT (module, permission) DO NOTHING
        """
    )
    for module, permission, level in MODULE_PERMISSION_SEED:
        op.execute(stmt.bindparams(m=module, p=permission, l=level))


def downgrade() -> None:
    # Remove only the exact (module, permission) rows this migration seeds.
    stmt = text(
        "DELETE FROM module_permissions WHERE module = :m AND permission = :p"
    )
    for module, permission, _level in MODULE_PERMISSION_SEED:
        op.execute(stmt.bindparams(m=module, p=permission))
