"""Sprint 11B Phase 1 — seed module_permissions for 35 ungated routes.

Idempotent: ON CONFLICT (module, permission) DO UPDATE SET
min_role_level = EXCLUDED.min_role_level.

Adds the metadata-driven (module, permission, min_role_level) triples
that Phase 2 will wire to actual routes via
``require_module_permission(request, module, permission)``.

Run:
    set -a && source .env && set +a
    python3 scripts/seed_rbac_permissions_s11b.py

After:
    redis-cli DEL ragbot:rbac:perms
"""

from __future__ import annotations

import os
import sys
from typing import Final

from sqlalchemy import create_engine, text


# (module, permission, min_role_level) — see plan mapping table.
# Levels: viewer=10, user=20, operator=40, admin=60, tenant=80, super_admin=100
SEED_PERMISSIONS: Final[list[tuple[str, str, int]]] = [
    # --- ai module (16 admin_ai.py routes) ---------------------------------
    # Existing: ai.configure (60), ai.view_models (20)
    # New fine-grained:
    ("ai", "provider_read", 60),       # GET /ai/providers (admin sees creds)
    ("ai", "provider_create", 80),     # POST /ai/providers
    ("ai", "provider_update", 80),     # PATCH /ai/providers/{id}
    ("ai", "provider_delete", 80),     # DELETE /ai/providers/{id}
    ("ai", "provider_test", 60),       # POST /ai/providers/{id}/test
    ("ai", "provider_rotate_key", 80), # POST /ai/providers/{id}/rotate-key
    ("ai", "model_read", 20),          # GET /ai/models — already ai.view_models=20, alias
    ("ai", "model_create", 80),        # POST /ai/models
    ("ai", "model_update", 80),        # PATCH /ai/models/{id}
    ("ai", "model_delete", 80),        # DELETE /ai/models/{id}
    ("ai", "binding_read", 60),        # GET /bots/{bot_id}/bindings
    ("ai", "binding_create", 80),      # POST /bots/{bot_id}/bindings
    ("ai", "binding_update", 80),      # PATCH /bots/{bot_id}/bindings/{id}
    ("ai", "binding_delete", 80),      # DELETE /bots/{bot_id}/bindings/{id}
    ("ai", "audit_read", 60),          # GET /bots/{bot_id}/audit-log
    ("ai", "cache_reload", 60),        # POST /ai/cache/reload
    ("ai", "cache_status", 60),        # GET /ai/cache/status
    ("ai", "effective_config_read", 60),  # GET /ai/models/{id}/effective-config

    # --- bot module (6 admin_bots.py routes) -------------------------------
    # Existing: bot.create (60), bot.update (40), bot.delete (60),
    #           bot.bypass_rate_limit (80), bot.bypass_token_limit (80)
    ("bot", "list", 60),               # GET /admin/bots — alias of admin role
    ("bot", "cache_status", 60),       # GET /admin/bots/cache/status
    ("bot", "cache_reload", 60),       # POST /admin/bots/cache/reload

    # --- system module (5 admin_metrics.py routes) -------------------------
    # Existing: system.view_metrics (60), system.manage_config (80)
    ("system", "metrics_overview", 60),         # GET /metrics/overview
    ("system", "metrics_by_model", 60),         # GET /metrics/by-model
    ("system", "metrics_top_questions", 60),    # GET /metrics/top-questions
    ("system", "metrics_steps", 60),            # GET /metrics/steps
    ("system", "metrics_active_models", 60),    # GET /metrics/active-models

    # --- policy module (4 admin_policy.py routes) — NEW MODULE ------------
    ("policy", "capability_read", 60),     # GET /ai/models/{id}/capability
    ("policy", "capability_upsert", 80),   # POST /ai/models/{id}/capability
    ("policy", "policy_read", 60),         # GET /policies
    ("policy", "policy_upsert", 80),       # POST /policies — tenant-level

    # --- admin module (3 admin_audit.py routes) ---------------------------
    # Existing: admin.view_audit (60), admin.manage_members (60),
    #           admin.manage_tenants (100)
    ("admin", "audit_message_read", 60),       # GET /audit/messages/{id}
    ("admin", "audit_overview_read", 60),      # GET /audit/overview
    ("admin", "audit_query_detail_read", 60),  # GET /audit/query-detail

    # --- chat module (2 chat.py routes) ------------------------------------
    # Existing: chat.query (10), chat.view_history (20), chat.clear_history (40)
    ("chat", "submit", 10),     # POST /chat — viewer (public, channel-scoped)
    ("chat", "feedback", 10),   # POST /feedback — viewer (public, message-scoped)

    # --- document module (3 documents.py routes) ---------------------------
    # Existing: document.upload (20), document.sync (40), document.delete (60)
    ("document", "ingest", 40),    # POST /documents/create — operator
    ("document", "delete_by_tool_name", 60),  # DELETE /documents — admin
    ("document", "rechunk", 40),   # POST /documents/rechunk — operator

    # --- sync module (4 sync.py routes) — NEW MODULE -----------------------
    # Sync routes are upstream service-token paths today; RBAC fallback is
    # admin (60) for human callers. Service-token middleware bypasses RBAC.
    ("sync", "bot_upsert", 60),         # POST /sync/bot
    ("sync", "documents_upsert", 60),   # POST /sync/documents
    ("sync", "documents_list", 20),     # GET /sync/documents — read-only
    ("sync", "documents_delete", 60),   # DELETE /sync/documents
]


def main() -> int:
    dsn = os.getenv("DATABASE_URL_SYNC") or (
        os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    )
    if not dsn:
        print("ERROR: DATABASE_URL / DATABASE_URL_SYNC env required", file=sys.stderr)
        return 1

    engine = create_engine(dsn)
    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM module_permissions"),
        ).scalar_one()
        for module, permission, level in SEED_PERMISSIONS:
            conn.execute(
                text(
                    """
                    INSERT INTO module_permissions(module, permission, min_role_level)
                    VALUES (:m, :p, :l)
                    ON CONFLICT (module, permission)
                    DO UPDATE SET min_role_level = EXCLUDED.min_role_level
                    """
                ),
                {"m": module, "p": permission, "l": level},
            )
        after = conn.execute(
            text("SELECT count(*) FROM module_permissions"),
        ).scalar_one()

    print(f"Seeded {len(SEED_PERMISSIONS)} entries")
    print(f"module_permissions count: before={before} after={after}")
    print("Remember to invalidate the Redis cache:")
    print("    redis-cli DEL ragbot:rbac:perms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
